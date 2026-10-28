"""A-only Spark population / registered Gemini cluster sample / paired PPI."""
import fcntl
import os
import shlex
import subprocess
from contextlib import contextmanager
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from scripts import run_correction_decision as a
from usersim.correction_decision import parse_judgment, sample_dialogues, SEED, TARGETS
from usersim.judge import input_sha256, messages
from usersim.judge_spark import judge_batch, read_cache, frozen_prompt, JudgeStopped, write_stop
from usersim import ppi
OUT=a.OUT
C=ROOT/'outputs/spark_judge_validation'
PARSER='strip_field_names_v1'


def active_sample_writers():
    result=subprocess.run(['ps','-axo','pid=,stat=,args='],capture_output=True,text=True,check=True)
    writers=[]
    for line in result.stdout.splitlines():
        parts=line.strip().split(None,2)
        if len(parts)!=3:continue
        pid,state,command=parts
        if int(pid)==os.getpid() or state.startswith('Z'):continue
        try:args=shlex.split(command)
        except ValueError:continue
        if not args or not Path(args[0]).name.lower().startswith('python'):continue
        if any(Path(arg).name=='run_correction_decision.py' for arg in args) and '--phase' in args:
            phase=args[args.index('--phase')+1:args.index('--phase')+2]
            if phase and phase[0] in ('gemini-sample','ppi-full','full'):writers.append(int(pid))
    return writers


@contextmanager
def sample_writer(out):
    with (out/'gemini_sample.jsonl.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Active Gemini cache writer; stop') from None
        try:
            if active_sample_writers():raise RuntimeError('Active Gemini cache writer; stop')
            yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def abandon_sample_dispatches(out):
    """Caller holds sample_writer: close unknown paid attempts without inventing usage."""
    rows=a.lines(out/'gemini_sample.jsonl')
    responses={(r['id'],r['attempt']) for r in rows if r['event']=='response'}
    abandoned=[]
    for row in rows:
        if row['event']!='dispatch' or (row['id'],row['attempt']) in responses:continue
        response=dict(event='response',id=row['id'],attempt=row['attempt'],
            model=row['model'],provider=row['provider'],status='abandoned_in_flight',usage=None,
            recovery_reason='Prior writer exited; exclusive writer lock acquired; charge unknown')
        with (out/'gemini_sample.jsonl').open('a') as cache:
            cache.write(json.dumps(response,ensure_ascii=False)+'\n');cache.flush();os.fsync(cache.fileno())
        responses.add((row['id'],row['attempt']));abandoned.append(response)
    return abandoned


def reparse():
    repaired=[]
    for row in a.lines(OUT/'judgments.jsonl'):
        if row.get('status')=='parse_error':
            repaired.append(dict(id=row['id'],attempt=row['attempt'],
                content_sha256=hashlib.sha256(row['content'].encode()).hexdigest(),
                original_status='parse_error',status='ok',prediction=parse_judgment(row['content'])))
    value=dict(parser=PARSER,api_calls=0,recovered=len(repaired),items=repaired)
    a.frozen(OUT/'gemini_reparse.json',value)
    return value


def prepare():
    chosen,plans,baseline=a.prepare(OUT)
    todo,eligible,excluded=a.judge_inputs(OUT,chosen,baseline,500)
    prompt=frozen_prompt(ROOT)
    assert a.read(C/'selection.json')['thinking']=='enabled'
    ids=a.read(OUT/'inputs.json')['ids']
    selected=sample_dialogues(ids)
    registration=dict(seed=SEED,population_ids=ids,sample_ids=selected,n=100,
        method='simple random sample without replacement of 100/500 dialogues; all their paired turns',
        bootstrap_replicates=2000,bootstrap='paired sample-dialogue bootstrap; Spark population fixed; HT correction via usersim.ppi.estimate',
        spark_thinking='enabled',prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        ppi_source_sha256=hashlib.sha256((ROOT/'usersim/ppi.py').read_bytes()).hexdigest(),
        planned_turns=len(plans),eligible_before_spark=len(eligible),excluded=dict(excluded),
        sample_request_ids=[r['request_id'] for r in eligible if r['dialogue_id'] in selected],
        adoption='PPI can be adopted only if C preregistered adoption_passed is true')
    a.frozen(OUT/'preregistration_ppi.json',registration)
    a.frozen(OUT/'ppi_judge_inputs.json',todo)
    a.frozen(OUT/'ppi_eligible.json',eligible)
    reparse()
    return registration,todo,eligible,prompt


def gemini_predictions():
    result={k:v['prediction'] for k,v in a.reusable().items()}
    for path in (OUT/'judgments.jsonl',OUT/'gemini_sample.jsonl'):
        raw=a.lines(path)
        dispatch={(r['id'],r['attempt']):r for r in raw if r['event']=='dispatch'}
        for row in raw:
            if row['event']!='response': continue
            assert (row['id'],row['attempt']) in dispatch
            if row['status'] in ('ok','parse_error'):
                result[dispatch[row['id'],row['attempt']]['input_sha256']]=parse_judgment(row['content'])
    return result


def registered_inputs(reg,todo,support=None):
    allowed=set(reg['sample_request_ids']) if support is None else {r['request_id'] for r in support if r['dialogue_id'] in reg['sample_ids']}
    return [item for item in todo if item['id'].split(':',1)[0] in a.CONDITIONS and item['id'].split(':',1)[1] in allowed]


def spark_failure_counts(rows):
    return Counter(r['status'] for r in rows if r['event']=='response'
                   and r.get('status') in ('ok','parse_error','moderated'))


def enforce_spark_parse_rate(counts):
    total=sum(counts[s] for s in ('ok','parse_error','moderated'))
    if total and counts['parse_error']/total > .02:
        raise JudgeStopped('parse_failure_rate',f'Spark parse failures {counts["parse_error"]}/{total} exceed 2%; stop')


def build_support(records,eligible):
    valid=[];moderated=[];missing=[]
    for row in eligible:
        values=[records[c+':'+row['request_id']] for c in ('human',)+a.SOURCES]
        bad_parse=any(v['status']=='parse_error' for v in values)
        bad_moderation=any(v['status']=='moderated' for v in values)
        if bad_parse:missing.append(row)
        if bad_moderation:moderated.append(row)
        if not bad_parse and not bad_moderation:
            assert all(v['status']=='ok' for v in values)
            valid.append(row)
    return dict(items=valid,moderated=moderated,parse_error=missing)


def spark_stage(concurrency):
    reg,todo,eligible,prompt=prepare()
    counts=spark_failure_counts(read_cache(OUT/'spark_judgments.jsonl',allow_pending=True))
    try:enforce_spark_parse_rate(counts)
    except JudgeStopped as exc:
        write_stop(OUT/'spark_judgments.jsonl',exc.reason,str(exc))
        raise
    def completed(row):
        counts[row['status']]+=1
        enforce_spark_parse_rate(counts)
        if sum(counts.values())%20==0:
            print(f'Spark response counts: {dict(counts)}',flush=True)
    records=judge_batch(OUT/'spark_judgments.jsonl',todo,concurrency,prompt=prompt,
        thinking='enabled',reuse_caches=[C/p for p in ('dev_enabled.jsonl','test.jsonl','full.jsonl')],on_result=completed)
    support=build_support(records,eligible)
    a.frozen(OUT/'ppi_support.json',support)
    print(f'Spark complete: paired={len(support["items"])}, moderated_pairs={len(support["moderated"])}, parse_missing_pairs={len(support["parse_error"])}',flush=True)


def support_and_spark(todo,prompt):
    support=a.read(OUT/'ppi_support.json')['items']
    terminal={r['input_sha256']:r for r in read_cache(OUT/'spark_judgments.jsonl') if r.get('status') in ('ok','moderated','parse_error')}
    pred={item['id']:terminal[input_sha256(prompt,item)] for item in todo}
    for row in support:
        assert all(pred[c+':'+row['request_id']]['status']=='ok' for c in ('human',)+a.SOURCES)
    return support,pred


def adoption_gate():
    summary=a.read(C/'summary.json')
    expected=a.read(C/'preregistration.json')['preregistration']
    if summary.get('preregistration')!=expected:
        raise RuntimeError('C preregistration mismatch; stop')
    authorization_path=OUT/'ppi_adoption_authorization.json'
    authorization=a.read(authorization_path) if authorization_path.exists() else {}
    if summary.get('status')!='complete' or authorization.get('decision')!='adopt_ppi_post_hoc':
        raise RuntimeError('C completion or post-hoc authorization not verified; no new paid sample requests')
    evidence=dict(adoption_passed=summary.get('adoption_passed'),authorization=authorization,authorization_sha256=hashlib.sha256(authorization_path.read_bytes()).hexdigest(),status='complete',summary_sha256=hashlib.sha256((C/'summary.json').read_bytes()).hexdigest(),preregistration=summary['preregistration'])
    a.frozen(OUT/'ppi_adoption.json',evidence)


class SampleJudge(a.MeteredJudge):
    def __init__(self,allowed,allowed_ids):
        super().__init__(OUT)
        self.cache=OUT/'gemini_sample.jsonl'
        self.allowed=set(allowed)
        self.allowed_ids=set(allowed_ids)
    def evaluate_sample(self,prompt,item,previous):
        sha=input_sha256(prompt,item)
        if sha not in self.allowed or item['id'] not in self.allowed_ids:
            raise RuntimeError('Input outside preregistered 100 dialogues; no Gemini request')
        attempts=[r for r in previous if r['event']=='response' and r['id']==sha]
        for row in attempts:
            if row['status'] in ('ok','parse_error'):
                return parse_judgment(row['content'])
        first=max((r['attempt'] for r in attempts),default=0)+1
        remaining=3-sum(r['status']!='abandoned_in_flight' for r in attempts)
        if remaining<=0: raise RuntimeError('Gemini transient retry budget exhausted; stop')
        last=first+remaining-1
        for retry_index,number in enumerate(range(first,last+1)):
            self.record(dict(event='dispatch',id=sha,attempt=number,input_sha256=sha,model='gemini-3.8-flash',provider='sandbox'))
            row=dict(event='response',id=sha,attempt=number,model='gemini-3.8-flash',provider='sandbox',usage=None)
            retry=False
            try:
                response=self.client.post(self.url,json=dict(model='gemini-3.8-flash',messages=messages(prompt,item),stream=False))
                row['http_status']=response.status_code
                response.raise_for_status()
                body=response.json();row['usage']=body.get('usage')
                if not isinstance(row['usage'],dict) or not all(type(row['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens','total_tokens')):
                    raise RuntimeError('Missing actual Gemini usage; stop')
                row['content']=body['choices'][0]['message']['content']
                try: row['prediction']=parse_judgment(row['content']);row['status']='ok'
                except (ValueError,TypeError): row['status']='parse_error';raise RuntimeError('Gemini sample parse failure; stop')
            except (httpx.TransportError,httpx.HTTPStatusError) as exc:
                row.update(status='request_error',error_type=type(exc).__name__)
                retry=isinstance(exc,httpx.TransportError) or exc.response.status_code in (408,429) or 500<=exc.response.status_code<600
                if not retry: raise RuntimeError('Non-transient Gemini request failure; stop') from None
            except Exception:
                row.setdefault('status','request_error')
                raise
            finally: self.record(row)
            if row['status']=='ok': return row['prediction']
            if not retry or number==last: raise RuntimeError('Gemini transient retry budget exhausted; stop')
            time.sleep(2**retry_index)


def gemini_stage(concurrency):
    with sample_writer(OUT):
        abandon_sample_dispatches(OUT)
        return _gemini_stage_locked(concurrency)


def _gemini_stage_locked(concurrency):
    reg,todo,eligible,prompt=prepare()
    support,_=support_and_spark(todo,prompt)
    allowed=registered_inputs(reg,todo,support)
    sha_allowed={input_sha256(prompt,r) for r in allowed}
    a.frozen(OUT/'gemini_sample_inputs.json',allowed)
    pred=gemini_predictions()
    pending={input_sha256(prompt,r):r for r in allowed if input_sha256(prompt,r) not in pred}
    a.dump(OUT/'gemini_sample_projection.json',dict(registered_dialogues=reg['n'],logical_inputs=len(allowed),pending_unique=len(pending),reused_logical=sum(input_sha256(prompt,r) in pred for r in allowed),projected_new_usd=len(pending)*a.read(OUT/'smoke_check.json')['usd_per_new_judgment'],historical_out_of_sample='retained for provenance, never requested again'))
    adoption_gate()
    if len(pending)*a.read(OUT/'smoke_check.json')['usd_per_new_judgment'] > 3:
        raise RuntimeError('Gemini sample projection exceeds $3; stop')
    registration_path=OUT/'preregistration_ppi.json'
    proof=dict(sha256=hashlib.sha256(registration_path.read_bytes()).hexdigest(),
        registration_birthtime=registration_path.stat().st_birthtime)
    if (OUT/'gemini_sample.jsonl').exists():
        assert registration_path.stat().st_birthtime <= (OUT/'gemini_sample.jsonl').stat().st_birthtime
    a.frozen(OUT/'preregistration_before_paid.json',proof)
    initial=a.meter(OUT)
    if not (OUT/'gemini_sample_billing.json').exists():
        a.frozen(OUT/'gemini_sample_billing.json',dict(start_total_usd=initial['current_total_usd'],shared_account=True))
    previous=a.lines(OUT/'judgments.jsonl')+a.lines(OUT/'gemini_sample.jsonl')
    client=SampleJudge(sha_allowed,{r['id'] for r in allowed})
    try:
        with a.ThreadPoolExecutor(max_workers=concurrency) as pool:
            iterator=iter(pending.values());active=set()
            def submit():
                item=next(iterator,None)
                if item is not None: active.add(pool.submit(client.evaluate_sample,prompt,item,previous))
            for _ in range(concurrency):submit()
            done_count=0
            try:
                while active:
                    done,_=a.wait(active,return_when=a.FIRST_COMPLETED);active.difference_update(done)
                    for future in done: future.result()
                    done_count+=len(done)
                    if done_count%20<len(done): print(f'Gemini sample: {done_count}/{len(pending)}',flush=True)
                    for _ in done:submit()
            except BaseException:
                for future in active:future.cancel()
                raise
    finally:client.close()
    final=a.meter(OUT)
    bill=a.read(OUT/'gemini_sample_billing.json')
    bill.update(current_total_usd=final['current_total_usd'],spent_usd=final['current_total_usd']-bill['start_total_usd'])
    a.dump(OUT/'gemini_sample_billing.json',bill)


def paired_data():
    reg,todo,eligible,prompt=prepare()
    support,spark=support_and_spark(todo,prompt)
    g=gemini_predictions();by_id={r['id']:r for r in todo};plans=a.read(OUT/'plans.json')
    result=[]
    for row in support:
        rid=row['request_id']; gemini={}
        if row['dialogue_id'] in reg['sample_ids']:
            for c in ('human',)+a.SOURCES:
                sha=input_sha256(prompt,by_id[c+':'+rid])
                if sha not in g:raise RuntimeError('Registered Gemini sample incomplete; refuse full PPI report')
                gemini[c]=g[sha]
        result.append(dict(row,group=plans[rid]['group'],targets={c:plans[rid][c] for c in a.CONDITIONS},
            spark={c:spark[c+':'+rid]['prediction'] for c in ('human',)+a.SOURCES},gemini=gemini))
    return reg,result


def compute(reg,pairs):
    M=len(reg['population_ids']); N=len(pairs); sample=reg['sample_ids']; n=len(sample)
    # Indicator numerator estimates; fixed known denominators for population/targets.
    functions={}
    for c in ('human',)+a.SOURCES:
        for metric in ('continue','emotion'):
            functions[f'{c}/{metric}']=(N,lambda p,labels,c=c,m=metric:int(labels[c]['topic']=='continue') if m=='continue' else int(labels[c]['emotion']))
        for topic in TARGETS:
            functions[f'{c}/topic/{topic}']=(N,lambda p,labels,c=c,t=topic:int(labels[c]['topic']==t))
    for c in a.CONDITIONS:
        for topic in TARGETS:
            denom=sum(p['targets'][c]==topic for p in pairs)
            functions[f'{c}/adherence/{topic}']=(denom,lambda p,labels,c=c,t=topic:int(p['targets'][c]==t and labels[c]['topic']==t))
    draws_indices=[random.Random(SEED+i).choices(sample,k=n) for i in range(2000)]
    rates={method:{} for method in ('ppi','gemini_only','spark_only')}; all_draws={m:{} for m in rates}
    for key,(denom,hit) in functions.items():
        if not denom:
            for method in rates:rates[method][key]=None;all_draws[method][key]=None
            continue
        full=sum(hit(p,p['spark']) for p in pairs)/denom
        residual={d:0 for d in sample};only={d:0 for d in sample}
        for p in pairs:
            if p['dialogue_id'] in residual:
                gv=hit(p,p['gemini']);sv=hit(p,p['spark'])
                residual[p['dialogue_id']]+=gv-sv;only[p['dialogue_id']]+=gv
        values={'ppi':(full,residual),'gemini_only':(0,only)}
        for method,(base,sums) in values.items():
            point=ppi.estimate(base,list(sums.values()),M,denom)
            draws=[ppi.estimate(base,[sums[d] for d in draw],M,denom) for draw in draws_indices]
            rates[method][key]=dict(estimate=point,ci95=[ppi.quantile(draws,.025),ppi.quantile(draws,.975)],denominator=denom)
            all_draws[method][key]=draws
        rates['spark_only'][key]=dict(estimate=full,ci95=None,denominator=denom)
        all_draws['spark_only'][key]=[full]*2000
    stats={}
    for method in rates:
        stats[method]={}
        for metric in ('continue','emotion'):
            stats[method][metric]={}
            for c in a.SOURCES:
                h=rates[method][f'human/{metric}']['estimate'];s=rates[method][f'{c}/{metric}']['estimate']
                base=rates[method][f'baseline/{metric}']['estimate']-h
                gap_draw=[x-y for x,y in zip(all_draws[method][f'{c}/{metric}'],all_draws[method][f'human/{metric}'])]
                reductions=[abs(b-h)-abs(x-h) for b,x,h in zip(all_draws[method][f'baseline/{metric}'],all_draws[method][f'{c}/{metric}'],all_draws[method][f'human/{metric}'])]
                ci=lambda xs:[ppi.quantile(xs,.025),ppi.quantile(xs,.975)] if method!='spark_only' else None
                stats[method][metric][c]=dict(human=h,simulator=s,gap=s-h,gap_ci95=ci(gap_draw),absolute_gap_reduction=abs(base)-abs(s-h),absolute_gap_reduction_ci95=ci(reductions))
    return dict(statistics=stats,rates=rates,n_dialogues=M,paired_turns=N,sample_dialogues=n,sample_turns=sum(bool(p['gemini']) for p in pairs),seed=SEED,bootstrap_replicates=2000,decision_target_distribution={t:dict(count=sum(p['targets']['decision']==t for p in pairs),fraction=sum(p['targets']['decision']==t for p in pairs)/N) for t in TARGETS})


def report():
    reg,pairs=paired_data();result=compute(reg,pairs)
    result['c_adoption']=a.read(OUT/'ppi_adoption.json')
    result['billing_snapshot']=a.read(OUT/'budget.json')
    result['gemini_sample_projection']=a.read(OUT/'gemini_sample_projection.json')
    result['usage']={}
    for name in ('judgments.jsonl','gemini_sample.jsonl','spark_judgments.jsonl'):
        rows=a.lines(OUT/name); responses=[r for r in rows if r['event']=='response']
        result['usage'][name]=dict(responses=len(responses),status_counts=dict(Counter(r['status'] for r in responses)),
            reused_inputs=sum(r['event']=='reuse' for r in rows),
            tokens={k:sum((r.get('usage') or {}).get(k,0) for r in responses) for k in ('prompt_tokens','completion_tokens','total_tokens')},
            unknown_usage=sum(r.get('usage') is None for r in responses))
    result['sample_billing']=a.read(OUT/'gemini_sample_billing.json')
    support=a.read(OUT/'ppi_support.json')
    result['excluded_spark_judgments']=dict(moderated_pairs=len(support['moderated']),parse_error_pairs=len(support.get('parse_error',[])),unique_excluded_pairs=len({r['request_id'] for kind in ('moderated','parse_error') for r in support.get(kind,[])}))
    a.dump(OUT/'paired_judgments_500.json',pairs);a.dump(OUT/'summary_500.json',result)
    lines=['# P2 决策层控制：PPI / Gemini子样本 / Spark全量','','PPI口径为用户事后采纳，依据 docs/architecture.md ADR-018。C预先登记标准未达标：reaction/full 非负面组 emotion 的区间宽度仅缩小16.7%，低于20%（覆盖率全部达标）。授权见 ppi_adoption_authorization.json；不将事后采纳表述为预登记通过。oracle故意违反ADR-001，只作诊断，**不是修正方法**。',
        f'500段固定总体；{len(pairs)}共同有效轮次；固定随机抽100段（{result["sample_turns"]}轮）。抽样名单在preregistration_ppi.json，先登记后请求。',
        'PPI=Spark全量比例 + M/(nN)×样本对话Gemini−Spark指标和；使用usersim/ppi.py。差距=模拟器−真人；缩小=|基线差距|−|本组差距|。分母含unclear。',
        '按100个抽样对话共同bootstrap 2000次，Spark总体固定；95%百分位区间。目标和遵守率分母是全量已知计数。PPI不裁剪到[0,1]。Spark-only为固定总体描述性估计，不给推断区间。所有比较均报告，不按效果重跑。','']
    for method,metrics in result['statistics'].items():
        lines += [f'## {method}','','|指标|组|真人|模拟器|差距及95%区间|绝对差距缩小及95%区间|','|---|---|---:|---:|---|---|']
        for m,values in metrics.items():
            for c,v in values.items():lines.append(f'|{m}|{c}|{v["human"]:.6f}|{v["simulator"]:.6f}|{v["gap"]:+.6f} {v["gap_ci95"]}|{v["absolute_gap_reduction"]:+.6f} {v["absolute_gap_reduction_ci95"]}|')
    lines += ['','## 按目标遵守率（PPI口径）','','|组|目标|已知分母|估计|95%区间|','|---|---|---:|---:|---|']
    for c in a.CONDITIONS:
        for t in TARGETS:
            v=result['rates']['ppi'][f'{c}/adherence/{t}']
            lines.append(f'|{c}|{t}|{v["denominator"]}|{v["estimate"]:.6f}|{v["ci95"]}|' if v else f'|{c}|{t}|0|NA|NA|')
    lines += ['','## decision目标/实际比例（PPI口径）','','|目标|目标比例|实际估计|95%区间|','|---|---:|---:|---|']
    for t in TARGETS:
        v=result['rates']['ppi'][f'decision/topic/{t}'];target=sum(p['targets']['decision']==t for p in pairs)/len(pairs)
        lines.append(f'|{t}|{target:.6f}|{v["estimate"]:.6f}|{v["ci95"]}|')
    lines += ['','## 目标原文','']+[f'- {k}：{v}' for k,v in TARGETS.items()]
    lines += ['','## 排除、费用与限制','',f'生成前沿用v2排除；Spark判断审核共同排除{result["excluded_spark_judgments"]["moderated_pairs"]}轮、解析失败缺失共同剔除{result["excluded_spark_judgments"]["parse_error_pairs"]}轮（去重共{result["excluded_spark_judgments"]["unique_excluded_pairs"]}轮）；解析失败不重试内容，累计比例超过2%才停止；无未来用户文本进入生成提示词，oracle仅加标签对应目标句。',
        '真人/基线Gemini按完整输入指纹从v2复用；Spark按输入指纹从C复用，缺失在A缓存补判。Spark thinking enabled，冻结prompt，来源标签不进入请求。',
        '历史全量Gemini请求保留，当前只对预登记100段的decision/oracle补判；8条字段名换行仅离线重解析，无新请求。',
        f'中断在飞请求abandoned_in_flight共{result["usage"]["gemini_sample.jsonl"]["status_counts"].get("abandoned_in_flight",0)}条；usage未知(null)，可能已收费；重发使用递增attempt，已有ok不重发。共享账户账单差额含其他任务，不能精确归因。实际token与usage在逐请求日志。',json.dumps(result['billing_snapshot'],ensure_ascii=False),json.dumps(result['gemini_sample_projection'],ensure_ascii=False),
        '新增子样本账单：'+json.dumps(result['sample_billing'],ensure_ascii=False),'逐阶段实际用量（缺失usage不冒充零）：'+json.dumps(result['usage'],ensure_ascii=False),
        'C证据：'+json.dumps(result['c_adoption'],ensure_ascii=False)]
    (OUT/'report_500.md').write_text('\n'.join(lines)+'\n')
    return result


def run_phase(phase,concurrency=8):
    OUT.mkdir(exist_ok=True)
    if phase=='reparse':print(json.dumps(reparse(),ensure_ascii=False));return
    if phase=='ppi-plan':
        reg,todo,eligible,prompt=prepare(); print(f'preregistered={len(reg["sample_ids"])}; api_calls=0');return
    a.v2.credentials()
    if phase in ('spark','ppi-full'):spark_stage(concurrency)
    if phase in ('gemini-sample','ppi-full'):gemini_stage(concurrency);report()
