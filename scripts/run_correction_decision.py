"""Frozen 20 -> 500 decision control experiment; no writes to source runs."""
import argparse
import asyncio
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import os
import random
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from scripts import run_correction_v2 as v2
from scripts.measure_reaction import active_responses, judge_item, predictions, quantile
from scripts.validate_judge import cached
from usersim.csds import load, user_turns
from usersim.correction_v2 import previous_agent
from usersim.correction_decision import CONDITIONS, TARGETS, SEED, DecisionPool, corrected_prompt, budget_guard, group
from usersim.judge import Judge, input_sha256
from usersim.replay import prompt
from usersim.spark import Spark

OUT = ROOT/'outputs/correction_decision'
SOURCES = ('baseline',)+CONDITIONS
read, dump, frozen, lines = v2.read, v2.dump, v2.frozen, v2.lines


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def prepare(out):
    ids = read(ROOT/'outputs/extended/sampled_ids.json')['ids']
    pilot = read(ROOT/'outputs/sampled_ids.json')['ids']
    assert len(ids) == len(set(ids)) == 500 and len(pilot) == 20
    excluded = set(ids)|set(pilot)
    rows = {r['DialogueID']:r for r in load()}
    chosen = [rows[i] for i in ids]
    pairs = read(ROOT/'outputs/correction_v2/paired_judgments_500.json')
    oracle = {r['request_id']:r['predictions']['human']['topic'] for r in pairs}
    reaction = ROOT/'outputs/reaction/full'
    manifest = read(reaction/'run.json')['manifest']
    pred = predictions(active_responses(reaction, cached(reaction/'judgments.jsonl', allow_request_errors=True)))
    items = []
    for g, entries in manifest['groups'].items():
        for r in entries:
            rid = f"human:{r['dialogue_id']}:{r['turn']}"
            p = pred.get(rid)
            if p is None:
                raise RuntimeError('Missing human pool judgment; stop')
            row = rows[r['dialogue_id']]
            assert group(row, user_turns(row)[r['turn']-1]) == g
            items.append(dict(r, group=g, topic=p['topic'], source_id=rid))
    pool = DecisionPool(items, excluded)
    frozen(out/'pool.json', dict(seed=SEED, excluded_ids=sorted(excluded), counts=pool.counts, items=pool.items))
    # Freeze only inputs actually consumed; the other task can append simulator judgments.
    deps = [ROOT/p for p in ('config/judge_prompt.md', 'outputs/judge_validation/final_prompt.json',
            'outputs/correction_v2/paired_judgments_500.json', 'outputs/correction_v2/judge_inputs_500.json',
            'config/summary_overrides.json', 'config/summary_reviews.json', 'usersim/csds.py', 'usersim/replay.py')]
    deps += sorted((ROOT/'data/raw/csds/CSDS').glob('*.json'))
    frozen(out/'inputs.json', dict(ids=ids, seed=SEED, pool_sha256=digest(pool.items),
           sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in deps}))
    baseline = {r['request_id']:r for r in lines(ROOT/'outputs/extended/generations.jsonl')}
    plans = {}
    # Exactly v2's common eligible rounds; no oracle labels exist outside that scope.
    for row in chosen:
        for t, u in enumerate(user_turns(row), 1):
            rid = f'{row["DialogueID"]}:{t}'
            assert baseline[rid]['prompt_sha256'] == digest(prompt(row,u))
            if rid in oracle:
                plans[rid] = dict(request_id=rid, dialogue_id=row['DialogueID'], turn=t,
                    group=group(row,u), decision=pool.draw(row,u,t), oracle=oracle[rid])
    frozen(out/'plans.json', plans)
    assert len(plans) == len(pairs)
    return chosen, plans, baseline


async def generate(out, chosen, plans, n, concurrency):
    for c in CONDITIONS:
        dest = out/c
        dest.mkdir(exist_ok=True)
        complete = {r['request_id']:r for r in lines(dest/'generations.jsonl')}
        client = Spark(dest, concurrency)
        async def one(row, t, u):
            rid = f'{row["DialogueID"]}:{t}'
            if rid not in plans:
                return
            messages = corrected_prompt(row,u,plans[rid][c])
            sha = digest(messages)
            if rid in complete:
                assert complete[rid]['prompt_sha256'] == sha
                return
            text = await client.generate(messages,rid)
            record = dict(request_id=rid,dialogue_id=row['DialogueID'],turn=t,text=text,
                          status='moderated' if text is None else 'ok',prompt_sha256=sha)
            with (dest/'generations.jsonl').open('a') as f:
                f.write(json.dumps(record,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
            if text is not None and not text.strip():
                raise RuntimeError('Empty decision/oracle output; stop, do not rerun')
        tasks = [asyncio.create_task(one(r,t,u)) for r in chosen[:n] for t,u in enumerate(user_turns(r),1)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            raise
        finally:
            await client.close()


def judge_inputs(out, chosen, baseline, n):
    plans = read(out/'plans.json')
    source_inputs = {r['id']:r for r in read(ROOT/'outputs/correction_v2/judge_inputs_500.json')}
    gen = {c:{r['request_id']:r for r in lines(out/c/'generations.jsonl')} for c in CONDITIONS}
    eligible, todo = [], []
    excluded = Counter()
    for row in chosen[:n]:
        for t,u in enumerate(user_turns(row),1):
            rid = f'{row["DialogueID"]}:{t}'
            if rid not in plans:
                excluded['v2_excluded'] += 1
                continue
            records = [gen[c].get(rid) for c in CONDITIONS]
            if any(r is None for r in records):
                raise RuntimeError('Generation incomplete')
            if any(r['status'] != 'ok' for r in records):
                excluded['moderated_in_any_condition'] += 1
                continue
            if any(not r['text'].strip() for r in records):
                raise RuntimeError('Empty generated reply; stop')
            eligible.append(dict(request_id=rid,dialogue_id=row['DialogueID'],turn=t))
            for c in ('human','baseline'):
                item = dict(id=c+':'+rid,**judge_item(row,t,baseline[rid]['text'] if c=='baseline' else None))
                assert item == source_inputs[item['id']]
                todo.append(item)
            for c,r in zip(CONDITIONS,records):
                todo.append(dict(id=c+':'+rid,**judge_item(row,t,r['text'])))
    return todo,eligible,excluded


def reusable():
    source = ROOT/'outputs/correction_v2'
    result = read(source/'reused_judgments.json')
    dispatch = {(r['id'],r['attempt']):r for r in lines(source/'judgments.jsonl') if r['event']=='dispatch'}
    for r in lines(source/'judgments.jsonl'):
        if r['event']=='response' and r['status']=='ok':
            sha = dispatch[r['id'],r['attempt']]['input_sha256']
            result[sha] = dict(prediction=r['prediction'], cache='outputs/correction_v2/judgments.jsonl',id=r['id'],attempt=r['attempt'])
    return result


BUDGET_LOCK = threading.RLock()
BILLING_BACKOFF_SECONDS = (2, 4, 8, 16, 30)
# A fresh check precedes every POST; allow for all eight in-flight requests.
MAX_IN_FLIGHT = 8
REQUEST_RESERVE_USD = .50
IN_FLIGHT_RESERVE_USD = MAX_IN_FLIGHT * REQUEST_RESERVE_USD


def billing_with_retry():
    for attempt in range(len(BILLING_BACKOFF_SECONDS)+1):
        try:
            return v2.billing()
        except RuntimeError:
            if attempt == len(BILLING_BACKOFF_SECONDS):
                raise RuntimeError('Cannot verify gateway billing after 6 attempts; stop') from None
            time.sleep(BILLING_BACKOFF_SECONDS[attempt])

def meter(out, reserve=0):
    with BUDGET_LOCK:
        return _meter(out, max(reserve, IN_FLIGHT_RESERVE_USD))


def _meter(out, reserve):
    value = billing_with_retry()
    path = out/'budget.json'
    b = read(path) if path.exists() else dict(start_total_usd=value,limit_usd=14)
    count = sum(r['event']=='dispatch' for path in (out/'judgments.jsonl',out/'gemini_sample.jsonl') for r in lines(path))
    b.update(current_total_usd=value,spent_usd=value-b['start_total_usd'],requests=count,
             checked_at=datetime.now(timezone.utc).isoformat(),account_limit_cents=4900,request_limit=10000,
             in_flight_reserve_usd=reserve, max_in_flight=MAX_IN_FLIGHT,
             per_request_reserve_usd=REQUEST_RESERVE_USD)
    dump(path,b)
    budget_guard(value*100,count)
    if value+reserve >= 49:
        raise RuntimeError('Gateway billing plus in-flight reserve reaches $49; stop')
    return b


class MeteredJudge(Judge):
    def __init__(self, out):
        self.out = out
        super().__init__(out/'judgments.jsonl')
    def record(self,row):
        # Judge records dispatch immediately before each paid POST, including retries.
        if row['event']=='dispatch':
            with BUDGET_LOCK:
                meter(self.out)
                super().record(row)
        else:
            super().record(row)
            if row.get('status')=='parse_error':
                raise RuntimeError('Judgment parse failure; stop immediately')


def judge_stage(out,chosen,baseline,n,concurrency):
    text = (ROOT/'config/judge_prompt.md').read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == read(ROOT/'outputs/judge_validation/final_prompt.json')['sha256']
    todo,eligible,excluded = judge_inputs(out,chosen,baseline,n)
    frozen(out/f'judge_inputs_{n}.json',todo)
    old = reusable()
    imports = {input_sha256(text,r):old[input_sha256(text,r)] for r in todo if input_sha256(text,r) in old}
    previous_imports = read(out/'reused_judgments.json') if (out/'reused_judgments.json').exists() else {}
    previous_imports.update(imports)
    dump(out/'reused_judgments.json',previous_imports)
    for r in todo:
        if r['id'].split(':')[0] in ('human','baseline') and input_sha256(text,r) not in imports:
            raise RuntimeError('Human/baseline missing exact v2 cache; no rejudging')
    previous = cached(out/'judgments.jsonl',allow_request_errors=True)
    done = predictions(previous)
    pending = {input_sha256(text,r):dict(r,id=input_sha256(text,r)) for r in todo
               if input_sha256(text,r) not in imports and input_sha256(text,r) not in done}
    print(f'judge {n}: logical={len(todo)} pending_unique={len(pending)}',flush=True)
    client = MeteredJudge(out)
    try:
        # Reuse bounded refill scheduling. MeteredJudge additionally guards every POST.
        original = v2.meter
        v2.meter = meter
        try:
            v2.evaluate_metered(client,text,list(pending.values()),previous,out,concurrency,n)
        finally:
            v2.meter = original
    finally:
        client.close()
    meter(out)


def statistical_report(out,chosen,baseline,n):
    text=(ROOT/'config/judge_prompt.md').read_text()
    todo,eligible,excluded=judge_inputs(out,chosen,baseline,n)
    imports=read(out/'reused_judgments.json')
    pred={k:v['prediction'] for k,v in imports.items()}
    pred.update(predictions(cached(out/'judgments.jsonl',allow_request_errors=True)))
    by_id={r['id']:pred.get(input_sha256(text,r)) for r in todo}
    valid=[r for r in eligible if all(by_id.get(c+':'+r['request_id']) is not None for c in ('human',)+SOURCES)]
    if len(valid)!=len(eligible):
        raise RuntimeError('Judgments incomplete; refuse complete report')
    clusters=defaultdict(list)
    for r in valid:
        clusters[r['dialogue_id']].append(r['request_id'])
    ids=sorted(clusters)
    stats={}
    for metric in ('continue','emotion'):
        hit=lambda p: p['topic']=='continue' if metric=='continue' else p['emotion']
        counts={d:[len(clusters[d])]+[sum(hit(by_id[c+':'+rid]) for rid in clusters[d]) for c in ('human',)+SOURCES] for d in ids}
        def rates(draw):
            sums=[sum(counts[d][i] for d in draw) for i in range(2+len(SOURCES))]
            return [v/sums[0] for v in sums[1:]]
        point=rates(ids)
        rng=random.Random(SEED)
        draws=[rates(rng.choices(ids,k=len(ids))) for _ in range(2000)]
        ci=lambda v:[quantile(v,.025),quantile(v,.975)]
        values={}
        for i,c in enumerate(SOURCES,1):
            gap=point[i]-point[0]
            base_gap=point[1]-point[0]
            improvement=abs(base_gap)-abs(gap)
            values[c]=dict(human=point[0],simulator=point[i],gap=gap,
                 gap_ci95=ci([v[i]-v[0] for v in draws]),
                 absolute_gap_reduction=improvement,
                 absolute_gap_reduction_ci95=ci([abs(v[1]-v[0])-abs(v[i]-v[0]) for v in draws]),
                 relative_gap_reduction=improvement/abs(base_gap) if base_gap else None)
        stats[metric]=values
    result=dict(n_dialogues=n,paired_turns=len(valid),paired_dialogues=len(ids),excluded=dict(excluded),
                bootstrap_replicates=2000,seed=SEED,statistics=stats)
    result['joint_counts']={c:{f'{topic}/emotion={emotion}':sum(by_id[c+':'+r['request_id']]==dict(topic=topic,emotion=emotion) for r in valid)
                              for topic in ('close','continue','unclear') for emotion in (False,True)}
                            for c in ('human',)+SOURCES}
    usage={}
    for c in CONDITIONS:
        requests=lines(out/c/'request_usage.jsonl')
        usage[c]=dict(requests=len(requests),status_counts=dict(Counter(r['status'] for r in requests)),
                      input_tokens=sum(r['input_tokens'] for r in requests),output_tokens=sum(r['output_tokens'] for r in requests),
                      unknown_usage=sum(not r.get('usage_known',False) for r in requests))
    requests=cached(out/'judgments.jsonl',allow_request_errors=True)
    usage['gemini']=dict(requests=len(requests),status_counts=dict(Counter(r['status'] for r in requests)),
                        tokens={k:sum((r.get('usage') or {}).get(k,0) for r in requests) for k in ('prompt_tokens','completion_tokens','total_tokens')},
                        unknown_usage=sum(r.get('usage') is None for r in requests))
    result['cumulative_run_usage']=usage
    result['billing_snapshot']=read(out/'budget.json')
    dump(out/f'paired_judgments_{n}.json',[dict(r,predictions={c:by_id[c+':'+r['request_id']] for c in ('human',)+SOURCES}) for r in valid])
    dump(out/f'summary_{n}.json',result)
    table = ['# P2 决策层控制：baseline / decision / oracle', '',
             f'{n}段；三组与真人共同有效轮次 {len(valid)}；排除 {dict(excluded)}。',
             'continue 分母含 unclear。按对话配对 bootstrap 2000次，种子20261027，95%百分位线性插值区间。',
             '差距=模拟器−真人；缩小=|基线差距|−|本组差距|。所有比较均报告，未作多重比较校正。', '',
             '|指标|组|真人|模拟器|差距（百分点）及95%区间|绝对差距缩小（百分点）及95%区间|',
             '|---|---|---:|---:|---|---|']
    fmt = lambda xs: '['+', '.join(f'{100*x:.2f}' for x in xs)+']'
    for metric, values in stats.items():
        for c,v in values.items():
            table.append(f'|{metric}|{c}|{v["human"]:.2%}|{v["simulator"]:.2%}|{100*v["gap"]:+.2f} {fmt(v["gap_ci95"])}|{100*v["absolute_gap_reduction"]:+.2f} {fmt(v["absolute_gap_reduction_ci95"])}|')
    (out/f'report_{n}.md').write_text('\n'.join(table)+'\n')
    return result

def report(out,chosen,baseline,n):
    result = statistical_report(out,chosen,baseline,n)
    pairs = read(out/f'paired_judgments_{n}.json')
    plans = read(out/'plans.json')
    adherence = {}
    for c in CONDITIONS:
        adherence[c] = {}
        for target in TARGETS:
            selected = [r for r in pairs if plans[r['request_id']][c]==target]
            hits = sum(r['predictions'][c]['topic']==target for r in selected)
            adherence[c][target] = dict(n=len(selected),matches=hits,rate=hits/len(selected) if selected else None)
    targets = Counter(plans[r['request_id']]['decision'] for r in pairs)
    actual = Counter(r['predictions']['decision']['topic'] for r in pairs)
    result['adherence'] = adherence
    result['decision_topic_distribution'] = {t:dict(target_count=targets[t],actual_count=actual[t],
        target_fraction=targets[t]/len(pairs),actual_fraction=actual[t]/len(pairs)) for t in TARGETS}
    result['pool_counts'] = read(out/'pool.json')['counts']
    dump(out/f'summary_{n}.json',result)
    for r in pairs:
        r['targets'] = {c:plans[r['request_id']][c] for c in CONDITIONS}
    dump(out/f'paired_judgments_{n}.json',pairs)
    # Replace v2-specific narrative; preserve the statistical table above it.
    path = out/f'report_{n}.md'
    table = path.read_text().split('## 方法与限制')[0].replace('P2 v2 四组配对比较','P2 决策层控制：baseline / decision / oracle').replace('四组与真人','三组与真人')
    table += '## 方法与限制\n\n'
    table += 'decision 从独立真人 Gemini 标签池按上一条客服回应负面/非负面分组抽样；排除待测500段及原试跑20段，固定种子20261027。不输入本轮或未来用户文本。\n\n'
    table += 'oracle 使用本轮真人实际标签，故意违反 ADR-001，只作诊断，**不是修正方法**；提示词只加入标签对应的目标句，不加入真人本轮文本。\n\n'
    table += 'v2共同有效轮次上生成，新增组任一审核拒绝则全部配对剔除；所有组使用相同分母。真人和基线仅按完整输入SHA-256从v2复用，不重判。判断请求不含来源标签。未设效果阈值，不按效果重抽样或重跑。\n\n'
    table += '目标句原文（仅在基线user提示词前加一句）：\n\n'
    table += '\n'.join(f'- {k}：{v}' for k,v in TARGETS.items())+'\n\n'
    table += '## 按目标遵守率\n\n|组|目标|匹配|分母|比例|\n|---|---|---:|---:|---:|\n'
    for c, values in adherence.items():
        for t,v in values.items(): table += f'|{c}|{t}|{v["matches"]}|{v["n"]}|{v["rate"]}|\n'
    table += '\n## decision 目标与实际比例（含 unclear）\n\n|标签|目标比例|实际比例|\n|---|---:|---:|\n'
    for t,v in result['decision_topic_distribution'].items(): table += f'|{t}|{v["target_fraction"]:.6f}|{v["actual_fraction"]:.6f}|\n'
    table += '\n## 费用\n\n共享账户的账单差额包含同时运行的其他任务，不能精确归因本任务。\n\n'+json.dumps(result['billing_snapshot'],ensure_ascii=False)+'\n\n实际请求与token：'+json.dumps(result['cumulative_run_usage'],ensure_ascii=False)+'\n'
    path.write_text(table)
    return result


def audit_generations(out,chosen,plans,baseline,n):
    selected = {r['DialogueID']:r for r in chosen[:n]}
    result = {}
    for c in CONDITIONS:
        records = lines(out/c/'generations.jsonl')
        assert len(records)==len({r['request_id'] for r in records})
        by_id = {r['request_id']:r for r in records}
        for rid,p in plans.items():
            if p['dialogue_id'] not in selected: continue
            row = selected[p['dialogue_id']]; u = user_turns(row)[p['turn']-1]
            r = by_id[rid]
            assert r['prompt_sha256']==digest(corrected_prompt(row,u,p[c]))
            assert r['status'] in ('ok','moderated')
            if r['status']=='ok': assert isinstance(r['text'],str) and r['text'].strip()
        current = [r for r in records if r['dialogue_id'] in selected]
        good = [r for r in current if r['status']=='ok']
        result[c] = dict(ok=len(good),moderated=len(current)-len(good),mean_chars=sum(len(r['text']) for r in good)/len(good),
                         mean_messages=sum(len(r['text'].splitlines()) for r in good)/len(good))
    dump(out/f'generation_audit_{n}.json',result)


def full_cost_gate(out,chosen,plans):
    smoke = read(out/'smoke_check.json')
    b = meter(out)
    first = {r['DialogueID'] for r in chosen[:20]}
    # Recovery must not charge completed judgments again in the forecast.
    available = set(predictions(cached(out/'judgments.jsonl',allow_request_errors=True)))
    if (out/'reused_judgments.json').exists():
        available.update(read(out/'reused_judgments.json'))
    text = (ROOT/'config/judge_prompt.md').read_text()
    rows = {r['DialogueID']:r for r in chosen}
    unknown = 0
    known = set()
    for c in CONDITIONS:
        generated = {r['request_id']:r for r in lines(out/c/'generations.jsonl')}
        for rid,p in plans.items():
            if p['dialogue_id'] in first:
                continue
            record = generated.get(rid)
            if record is None:
                unknown += 1
            elif record['status']=='ok':
                sha = input_sha256(text,judge_item(rows[p['dialogue_id']],p['turn'],record['text']))
                if sha not in available:
                    known.add(sha)
    pending = unknown+len(known)
    projected = b['spent_usd']+pending*smoke['usd_per_new_judgment']*1.15
    value = dict(projected_total_usd=projected,remaining_upper_bound=pending,margin=1.15,limit_usd=14,
                 shared_account_cost=True)
    dump(out/'cost_projection.json',value)
    if projected>14: raise RuntimeError('Small-batch cost projection exceeds $14; stop')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--phase',choices=('plan','smoke','full','report','reparse','ppi-plan','spark','gemini-sample','ppi-full'),required=True)
    ap.add_argument('--report-n',type=int,choices=(20,500),default=500)
    ap.add_argument('--concurrency',type=int,default=8)
    args=ap.parse_args()
    if not 1<=args.concurrency<=8: ap.error('concurrency must be 1..8')
    OUT.mkdir(parents=True,exist_ok=True)
    if args.phase in ('smoke','full'):
        raise RuntimeError('Full Gemini judging is retired by the PPI Delta; use --phase ppi-full')
    if args.phase in ('reparse','ppi-plan','spark','gemini-sample','ppi-full'):
        from scripts.run_correction_decision_ppi import run_phase
        run_phase(args.phase,args.concurrency)
        return
    if args.phase=='report' and args.report_n==500 and (OUT/'preregistration_ppi.json').exists():
        from scripts.run_correction_decision_ppi import report as ppi_report
        print(json.dumps(ppi_report(),ensure_ascii=False))
        return
    chosen,plans,baseline=prepare(OUT)
    if args.phase=='plan':
        print('Frozen conditional pool and plans verified; api_calls=0'); return
    if args.phase=='report':
        audit_generations(OUT,chosen,plans,baseline,args.report_n)
        print(json.dumps(report(OUT,chosen,baseline,args.report_n),ensure_ascii=False)); return
    v2.credentials(); meter(OUT)
    n=20 if args.phase=='smoke' else 500
    if n==500: full_cost_gate(OUT,chosen,plans)
    asyncio.run(generate(OUT,chosen,plans,n,args.concurrency))
    audit_generations(OUT,chosen,plans,baseline,n)
    judge_stage(OUT,chosen,baseline,n,args.concurrency)
    result=report(OUT,chosen,baseline,n)
    if n==20:
        b=meter(OUT)
        count=b['requests']
        if count<=0 or b['spent_usd']<=0: raise RuntimeError('Unmeasurable smoke cost')
        frozen(OUT/'smoke_check.json',dict(passed=True,dialogues=20,requests=count,
               cost_usd=b['spent_usd'],usd_per_new_judgment=b['spent_usd']/count))
        full_cost_gate(OUT,chosen,plans)
    print(f'{args.phase} complete',flush=True)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'STOPPED.md').write_text(f'{type(exc).__name__}: {exc}\nNo fallback or rerun.\n')
        raise
