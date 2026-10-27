"""Contracted 20 -> 500 correction experiment; immutable plans and metered judging."""
import argparse
import asyncio
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from usersim.correction_v2 import SituationPool, CONDITIONS, SEED, corrected_prompt, previous_agent
from usersim.csds import load, user_turns
from usersim.replay import prompt
from usersim.spark import Spark
from usersim.judge import Judge, input_sha256
from scripts.measure_reaction import judge_item, quantile, predictions
from scripts.validate_judge import cached

OUT = ROOT/'outputs/correction_v2'
SOURCES = ('baseline',)+CONDITIONS


def read(path):
    return json.loads(Path(path).read_text())


def dump(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def frozen(path, value):
    if path.exists() and read(path) != value:
        raise RuntimeError(f'Frozen input mismatch: {path}')
    if not path.exists():
        dump(path,value)


def lines(path):
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []


def credentials():
    # Same ignored local env file used by existing experiments; no values are logged.
    for line in (ROOT/'.env').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        name,value = line.removeprefix('export ').split('=',1)
        if name.strip() in ('SPARK_API_KEY','SANDBOX_API_KEY','SANDBOX_BASE_URL'):
            os.environ.setdefault(name.strip(),shlex.split(value)[0])


def billing():
    url = os.environ['SANDBOX_BASE_URL'].rstrip('/')+'/v1/dashboard/billing/usage'
    try:
        response = httpx.get(url,headers={'Authorization':'Bearer '+os.environ['SANDBOX_API_KEY']},timeout=60)
        response.raise_for_status()
        value = response.json()['total_usage']/100
        if not isinstance(value,(float,int)) or value < 0:
            raise ValueError()
        return value
    except Exception:
        raise RuntimeError('Cannot verify gateway billing; stop') from None


def meter(out, reserve=0):
    path = out/'budget.json'
    value = billing()
    if path.exists():
        b = read(path)
    else:
        b = dict(start_total_usd=value, limit_usd=20, started_at=datetime.now(timezone.utc).isoformat())
    b.update(current_total_usd=value, spent_usd=value-b['start_total_usd'],
             checked_at=datetime.now(timezone.utc).isoformat())
    dump(path,b)
    if b['spent_usd']+reserve > b['limit_usd']:
        raise RuntimeError(f'Budget reserve would exceed ${b["limit_usd"]}; stop')
    return b


def prepare(out):
    rows = load()
    ids = read(ROOT/'outputs/extended/sampled_ids.json')['ids']
    assert len(ids)==500 and len(set(ids))==500
    excluded = set(ids)|set(read(ROOT/'outputs/sampled_ids.json')['ids'])
    dependencies = [ROOT/'outputs/extended/generations.jsonl',ROOT/'outputs/extended/sampled_ids.json',
                    ROOT/'config/judge_prompt.md',ROOT/'outputs/judge_validation/final_prompt.json',
                    ROOT/'config/summary_overrides.json',ROOT/'config/summary_reviews.json',
                    ROOT/'usersim/correction_v2.py', ROOT/'usersim/csds.py',ROOT/'usersim/replay.py']
    dependencies += sorted((ROOT/'data/raw/csds/CSDS').glob('*.json'))
    frozen(out/'inputs.json',dict(seed=SEED,ids=ids,excluded_ids=sorted(excluded),
           sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in dependencies}))
    by_id = {r['DialogueID']:r for r in rows}
    chosen = [by_id[i] for i in ids]
    plan_path = out/'plans.json'
    if plan_path.exists():
        plans = read(plan_path)
    else:
        pool = SituationPool(rows,excluded)
        plans = dict(seed=SEED,pool_size=len(pool.items),pool_sha256=pool.sha256,
                     method='Chinese unigram/bigram TF-IDF cosine of preceding last agent message; top 5; seeded fallback if empty. Unconditional joint empirical chars/messages draw; same draw in structure and combined.',
                     items=[pool.plan(r,t,u) for r in chosen for t,u in enumerate(user_turns(r),1)])
        dump(plan_path,plans)
    lookup = {p['request_id']:p for p in plans['items']}
    audit_count = 0
    baseline = {r['request_id']:r for r in lines(ROOT/'outputs/extended/generations.jsonl')}
    for row in chosen:
        for t,u in enumerate(user_turns(row),1):
            rid = f'{row["DialogueID"]}:{t}'
            p = lookup[rid]
            assert p['query']==previous_agent(row,u)
            assert all(a['dialogue_id'] not in excluded for a in p['examples'])
            assert p['structure']['dialogue_id'] not in excluded
            assert baseline[rid]['prompt_sha256']==hashlib.sha256(json.dumps(prompt(row,u),ensure_ascii=False).encode()).hexdigest()
            audit_count += 1
    frozen(out/'plan_audit.json',dict(turns=audit_count,excluded_dialogues=len(excluded),
           independent_train_pool=True,strict_prefix_queries=True,baseline_prompt_match=True))
    return chosen,lookup,baseline


async def generate(out,chosen,plans,n,concurrency):
    for condition in CONDITIONS:
        dest=out/condition
        dest.mkdir(exist_ok=True)
        cache=dest/'generations.jsonl'
        complete={r['request_id']:r for r in lines(cache)}
        client=Spark(dest,concurrency)
        async def one(row,t,u):
            rid=f'{row["DialogueID"]}:{t}'
            messages=corrected_prompt(row,u,plans[rid],condition)
            sha=hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
            if rid in complete:
                if complete[rid]['prompt_sha256']!=sha:
                    raise RuntimeError('Stale generation')
                return
            text=await client.generate(messages,rid)
            record=dict(request_id=rid,dialogue_id=row['DialogueID'],turn=t,text=text,
                        status='moderated' if text is None else 'ok',prompt_sha256=sha)
            with cache.open('a') as f:
                f.write(json.dumps(record,ensure_ascii=False)+'\n')
                f.flush()
                os.fsync(f.fileno())
            complete[rid]=record
            if len(complete)%100==0:
                print(f'{condition}: {len(complete)} generated',flush=True)
        tasks=[asyncio.create_task(one(row,t,u)) for row in chosen[:n] for t,u in enumerate(user_turns(row),1)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            raise
        finally:
            await client.close()


def judge_inputs(out,chosen,baseline,n):
    generations={'baseline':baseline}
    for c in CONDITIONS:
        generations[c]={r['request_id']:r for r in lines(out/c/'generations.jsonl')}
    todo=[]
    excluded=Counter()
    eligible=[]
    for row in chosen[:n]:
        for t,u in enumerate(user_turns(row),1):
            rid=f'{row["DialogueID"]}:{t}'
            if not previous_agent(row,u):
                excluded['no_previous_agent']+=1
                continue
            records=[generations[c].get(rid) for c in SOURCES]
            if any(r is None for r in records):
                raise RuntimeError('Generation incomplete')
            if any(r['status']!='ok' for r in records):
                excluded['moderated_in_any_condition']+=1
                continue
            eligible.append(dict(request_id=rid,dialogue_id=row['DialogueID'],turn=t))
            todo.append(dict(id='human:'+rid,**judge_item(row,t)))
            for c,r in zip(SOURCES,records):
                todo.append(dict(id=c+':'+rid,**judge_item(row,t,r['text'])))
    return todo,eligible,excluded


def audit_generations(out,chosen,plans,baseline,n):
    result={}
    expected={f'{row["DialogueID"]}:{t}':(row,u) for row in chosen[:n]
              for t,u in enumerate(user_turns(row),1)}
    for c in SOURCES:
        records=list(baseline.values()) if c=='baseline' else lines(out/c/'generations.jsonl')
        by_id={r['request_id']:r for r in records}
        if len(by_id)!=len(records):
            raise RuntimeError('Duplicate generation ID')
        ok=[]
        empty_outside_judge=[]
        for rid,(row,u) in expected.items():
            r=by_id[rid]
            messages=prompt(row,u) if c=='baseline' else corrected_prompt(row,u,plans[rid],c)
            assert r['prompt_sha256']==hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
            assert r['status'] in ('ok','moderated')
            if r['status']=='ok':
                if not isinstance(r['text'],str):
                    raise RuntimeError('Non-text successful generation')
                if not r['text'].strip():
                    if previous_agent(row,u):
                        raise RuntimeError('Empty successful generation in judge-eligible turn')
                    empty_outside_judge.append(rid)
                ok.append((rid,r['text']))
        result[c]=dict(planned=len(expected),ok=len(ok),moderated=len(expected)-len(ok),
                       empty_outside_judge=empty_outside_judge,
                       mean_chars=sum(len(re.sub(r'\s','',s)) for _,s in ok)/len(ok),
                       mean_messages=sum(len([x for x in s.splitlines() if x.strip()]) for _,s in ok)/len(ok))
        if c in ('structure','combined'):
            result[c].update(exact_message_count_fraction=sum(len([x for x in s.splitlines() if x.strip()])==plans[rid]['structure']['messages'] for rid,s in ok)/len(ok),
                mean_absolute_character_error=sum(abs(len(re.sub(r'\s','',s))-plans[rid]['structure']['chars']) for rid,s in ok)/len(ok))
    dump(out/f'generation_audit_{n}.json',result)
    return result


def reusable(prompt_text):
    result={}
    paths=[ROOT/'outputs/reaction/full/judgments.jsonl']
    paths+=sorted((ROOT/'outputs/judge_validation').glob('*.jsonl'))
    for path in paths:
        rows=lines(path)
        dispatch={(r['id'],r['attempt']):r for r in rows if r.get('event')=='dispatch'}
        for r in rows:
            if r.get('event')=='response' and r.get('status')=='ok':
                d=dispatch.get((r['id'],r['attempt']),{})
                if d.get('input_sha256'):
                    result[d['input_sha256']]=dict(prediction=r['prediction'],cache=str(path.relative_to(ROOT)),id=r['id'],attempt=r['attempt'])
    return result


def judge_stage(out,chosen,baseline,n,concurrency):
    text=(ROOT/'config/judge_prompt.md').read_text()
    assert hashlib.sha256(text.encode()).hexdigest()==read(ROOT/'outputs/judge_validation/final_prompt.json')['sha256']
    todo,eligible,excluded=judge_inputs(out,chosen,baseline,n)
    frozen(out/f'judge_inputs_{n}.json',todo)
    imports_path=out/'reused_judgments.json'
    imports=read(imports_path) if imports_path.exists() else {}
    old=reusable(text)
    for item in todo:
        sha=input_sha256(text,item)
        if sha in old:
            imports[sha]=old[sha]
    dump(imports_path,imports)
    cache_path=out/'judgments.jsonl'
    previous=cached(cache_path,allow_request_errors=True)
    pred=predictions(previous)
    by_sha={}
    for item in todo:
        sha=input_sha256(text,item)
        if sha not in imports and sha not in pred:
            by_sha.setdefault(sha,dict(item,id=sha))
    pending=list(by_sha.values())
    print(f'judge {n}: items={len(todo)} reused={sum(input_sha256(text,r) in imports for r in todo)} pending_unique={len(pending)}',flush=True)
    b=meter(out,reserve=.50 if pending else 0)
    if n==500 and pending:
        smoke=read(out/'smoke_check.json')
        projected=len(pending)*smoke['usd_per_new_judgment']*1.15
        dump(out/'cost_projection.json',dict(pending_unique=len(pending),projected_remaining_usd_with_15pct_margin=projected,spent_usd=b['spent_usd']))
        if projected+b['spent_usd']>b['limit_usd']:
            raise RuntimeError(f'Small-batch cost projection exceeds ${b["limit_usd"]}; no automatic downsizing')
    if pending:
        client=Judge(cache_path)
        try:
            evaluate_metered(client,text,pending,previous,out,concurrency,n)
        finally:
            client.close()
    meter(out)
    return todo,eligible,excluded


def evaluate_metered(client,text,pending,previous,out,concurrency,n):
    """Bound live requests; refill finished slots without waiting for stragglers."""
    if not 1 <= concurrency <= 8:
        raise ValueError('Judge concurrency must be between 1 and 8')
    items=iter(pending)
    completed=0
    checked=0
    meter(out,reserve=.50)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        active=set()
        def submit():
            item=next(items,None)
            if item is not None:
                active.add(pool.submit(client.evaluate,text,item,previous))
        for _ in range(concurrency):
            submit()
        try:
            while active:
                done,_=wait(active,return_when=FIRST_COMPLETED)
                active.difference_update(done)
                for future in done:
                    if future.result() is None:
                        raise RuntimeError('Judgment exhausted retry/parse budget; stop')
                completed+=len(done)
                if completed-checked >= concurrency:
                    b=meter(out,reserve=.50)
                    checked=completed
                    print(f'judge {n}: {completed}/{len(pending)}, spent=${b["spent_usd"]:.4f}',flush=True)
                for _ in done:
                    submit()
        except BaseException:
            for future in active:
                future.cancel()
            raise


def full_cost_gate(out,chosen,baseline):
    """Before new full-scale generation, project from measured smoke costs."""
    smoke=read(out/'smoke_check.json')
    text=(ROOT/'config/judge_prompt.md').read_text()
    available=set(read(out/'reused_judgments.json'))
    available.update(predictions(cached(out/'judgments.jsonl',allow_request_errors=True)))
    available.update(reusable(text))
    pending_known=set()
    unknown_new=0
    eligible=0
    for index,row in enumerate(chosen):
        for t,u in enumerate(user_turns(row),1):
            rid=f'{row["DialogueID"]}:{t}'
            if not previous_agent(row,u) or baseline[rid]['status']!='ok':
                continue
            eligible+=1
            for item in (judge_item(row,t),judge_item(row,t,baseline[rid]['text'])):
                sha=input_sha256(text,item)
                if sha not in available:
                    pending_known.add(sha)
            if index>=20:
                unknown_new+=3
    remaining=len(pending_known)+unknown_new
    b=meter(out)
    projected=remaining*smoke['usd_per_new_judgment']*1.15
    value=dict(stage='before_full_generation',eligible_turns_before_new_moderation=eligible,
               known_human_baseline_pending_unique=len(pending_known),unknown_new_condition_inputs=unknown_new,
               projected_pending=remaining,measured_usd_per_request=smoke['usd_per_new_judgment'],
               projected_remaining_usd_with_15pct_margin=projected,spent_usd=b['spent_usd'],
               projected_total_usd=projected+b['spent_usd'],limit_usd=b['limit_usd'],
               assumptions='new outputs may deduplicate or be moderated; counts conservatively assume unique successful new outputs; same average cost as 20-dialogue smoke plus 15% margin')
    dump(out/'preflight_cost_projection.json',value)
    if projected+b['spent_usd']>b['limit_usd']:
        raise RuntimeError(f'Small-batch projected full cost exceeds ${b["limit_usd"]}; stopped before full generation, no automatic downsizing')


def report(out,chosen,baseline,n):
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
            sums=[sum(counts[d][i] for d in draw) for i in range(6)]
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
    table=['# P2 v2 四组配对比较','',f'{n}段；四组与真人共同有效轮次 {len(valid)}；排除 {dict(excluded)}。',
           '只测客服已经回应的轮次；continue 分母包含 unclear。按对话配对 bootstrap 2000次；95%百分位区间；未作多重比较校正，结果为探索性。',
           '差距=模拟器−真人；差距缩小=|基线差距|−|修正差距|，正数为改善，负数为恶化。','',
           '|指标|条件|真人|模拟器|差距（百分点）及95%区间|绝对差距缩小（百分点）及95%区间|','|---|---|---:|---:|---|---|']
    for metric,values in stats.items():
        for c,v in values.items():
            fmt=lambda xs:'['+', '.join(f'{100*x:.2f}' for x in xs)+']'
            table.append(f'|{metric}|{c}|{v["human"]:.2%}|{v["simulator"]:.2%}|{100*v["gap"]:+.2f} {fmt(v["gap_ci95"])}|{100*v["absolute_gap_reduction"]:+.2f} {fmt(v["absolute_gap_reduction_ci95"])}|')
    table+=['','## 方法与限制','',
            '- 同一原始500段的已有基线；三组新生成。全部组保留真实历史，只生成当前用户轮次。',
            '- 独立train示例池排除500段待测及20段原试跑；按上一条客服消息的中文字/二元组TF-IDF余弦取5例；无匹配时固定种子补齐。',
            '- 结构控制从独立池联合抽样总非空白字符数、连发条数；structure与combined使用相同抽样。它是软指令，不保证严格遵守。',
            '- 使用冻结Gemini判断prompt；来源名不进入判断请求。仅以完整判断输入SHA-256匹配复用缓存。',
            '- 此处未设成功阈值；分别报告改善/恶化和不确定性，不选择性重跑。真实直接离开不在逐轮重放测量范围。',
            '- 既有基线与新生成时间不同；单次随机生成；单一GPT系标注者验证判断器；检索为词面相似，非语义嵌入。',
            '- 示例池为训练集，待测集合跨原数据划分；这是对话级隔离，不是全新数据域验证。']
    table+=['',f'本次累计Gemini实际请求 {usage["gemini"]["requests"]}；返回token {usage["gemini"]["tokens"]}；缺失usage {usage["gemini"]["unknown_usage"]}。',
            f'账单快照：本次新增 {result["billing_snapshot"]["spent_usd"]:.6f} 美元，上限{result["billing_snapshot"]["limit_usd"]}美元。复用判断不计入本次请求。']
    audit=read(out/f'generation_audit_{n}.json')
    table+=['','## 生成与结构遵守情况','', '|条件|成功/审核拒绝|平均字数|平均连发条数|目标条数精确遵守率|目标字数平均绝对误差|',
            '|---|---:|---:|---:|---:|---:|']
    for c,v in audit.items():
        adherence=f'{v["exact_message_count_fraction"]:.2%}' if 'exact_message_count_fraction' in v else '—'
        error=f'{v["mean_absolute_character_error"]:.2f}' if 'mean_absolute_character_error' in v else '—'
        table.append(f'|{c}|{v["ok"]}/{v["moderated"]}|{v["mean_chars"]:.2f}|{v["mean_messages"]:.2f}|{adherence}|{error}|')
    for c,v in audit.items():
        if v.get('empty_outside_judge'):
            table.append(f'\n{c}：HTTP成功中有空回复 {v["empty_outside_judge"]}；均无客服前文，按既定口径不进入判断，保留原输出不重跑；上表生成均值包含这些空回复。\n')
    table+=['','## 话题×情绪交叉计数','', '|来源|close/否|close/是|continue/否|continue/是|unclear/否|unclear/是|',
            '|---|---:|---:|---:|---:|---:|---:|']
    for c,v in result['joint_counts'].items():
        table.append('|'+c+'|'+'|'.join(str(x) for x in v.values())+'|')
    (out/f'report_{n}.md').write_text('\n'.join(table)+'\n')
    views={r['id']:r for r in todo}
    examples=['# 固定规则抽取案例','', '按对话ID和轮次升序，取前10个基线与combined至少一个标签不同的轮次；不是额外标注。','']
    changed=[r for r in sorted(valid,key=lambda r:(r['dialogue_id'],r['turn'])) if by_id['baseline:'+r['request_id']]!=by_id['combined:'+r['request_id']]][:10]
    for r in changed:
        rid=r['request_id']
        examples += [f'## {rid}','', '客服：'+views['human:'+rid]['previous_agent'],'']
        for c in ('human',)+SOURCES:
            examples += [f'- {c}：{views[c+":"+rid]["text"].replace(chr(10)," / ")}；判断={by_id[c+":"+rid]}']
        examples.append('')
    (out/f'examples_{n}.md').write_text('\n'.join(examples)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--phase',choices=('plan','smoke','full','report'),required=True)
    ap.add_argument('--report-n',type=int,choices=(20,500),default=500)
    ap.add_argument('--concurrency',type=int,default=8)
    args=ap.parse_args()
    os.chdir(ROOT)
    OUT.mkdir(parents=True,exist_ok=True)
    chosen,plans,baseline=prepare(OUT)
    if args.phase=='plan':
        print('Frozen plan and strict-prefix audit passed; no API requests.',flush=True)
        return
    if args.phase=='report':
        audit_generations(OUT,chosen,plans,baseline,args.report_n)
        print(json.dumps(report(OUT,chosen,baseline,args.report_n),ensure_ascii=False),flush=True)
        return
    credentials()
    meter(OUT)
    n=20 if args.phase=='smoke' else 500
    if n==500 and not (OUT/'smoke_check.json').exists():
        raise RuntimeError('20-dialogue gate not passed')
    if n==500:
        full_cost_gate(OUT,chosen,baseline)
    asyncio.run(generate(OUT,chosen,plans,n,args.concurrency))
    audit_generations(OUT,chosen,plans,baseline,n)
    meter(OUT)
    judge_stage(OUT,chosen,baseline,n,args.concurrency)
    result=report(OUT,chosen,baseline,n)
    if n==20:
        requests=cached(OUT/'judgments.jsonl',allow_request_errors=True)
        b=meter(OUT)
        spent=b['spent_usd']
        if not requests or spent<=0 or any(p is None for p in predictions(requests).values()):
            raise RuntimeError('Smoke billing/request validation failed')
        frozen(OUT/'smoke_check.json',dict(passed=True,dialogues=20,paired_turns=result['paired_turns'],
               actual_requests=len(requests),cost_usd=spent,usd_per_new_judgment=spent/len(requests),
               criteria='all planned generations terminal; all eligible five-way judgments parsed; billing measurable; frozen inputs and independent-plan audit passed'))
    print(f'{args.phase} complete',flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'STOPPED.md').write_text(f'Pipeline stopped: {type(exc).__name__}: {exc}\nNo model fallback, scope expansion, or automatic downsizing.\n')
        raise
