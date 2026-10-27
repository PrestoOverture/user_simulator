"""Contract B: frozen full manifest, identical replay prompts, three-way ADR-016."""
import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from scripts.measure_reaction import GROUPS, active_responses, judge_item, predictions, summarize
from scripts.validate_judge import cached
from usersim.csds import load, user_turns
from usersim.judge import Judge, input_sha256, parse, messages as judge_messages
from usersim.replay import prompt
from usersim.spark import Spark

FULL = ROOT/'outputs/reaction/full'
OUT = ROOT/'outputs/reaction/deepseek'
GEN = ROOT/'outputs/model_b/deepseek-v4.1-flash/reaction_full'
MODEL = 'deepseek-v4.1-flash'


def read(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def freeze(path, value):
    if path.exists() and read(path) != value:
        raise RuntimeError(f'Frozen input differs: {path}')
    if not path.exists():
        dump(path, value)


def validate_manifest(manifest, full):
    if manifest != full or any(len(full['groups'][g]) != 1500 for g in GROUPS):
        raise RuntimeError('DeepSeek manifest differs from full')
    ids = [f"{r['dialogue_id']}:{r['turn']}" for g in GROUPS for r in full['groups'][g]]
    if len(set(ids)) != 3000:
        raise RuntimeError('Duplicate selected turn')


def prepare():
    manifest = read(FULL/'run.json')['manifest']
    validate_manifest(manifest, manifest)
    if (GEN/'manifest.json').exists():
        validate_manifest(read(GEN/'manifest.json'), manifest)
    by_id = {r['DialogueID']: r for r in load()}
    baseline = {r['request_id']: r for r in lines(ROOT/'outputs/full_csds/spark/generations.jsonl')}
    jp = (ROOT/'config/judge_prompt.md').read_text()
    if hashlib.sha256(jp.encode()).hexdigest() != read(ROOT/'outputs/judge_validation/final_prompt.json')['sha256']:
        raise RuntimeError('Frozen judge prompt changed')
    todo, plans, reuse = [], [], []
    responses = active_responses(FULL, cached(FULL/'judgments.jsonl', allow_request_errors=True))
    pred = predictions(responses)
    dispatch = {(r['id'],r['attempt']):r for r in lines(FULL/'judgments.jsonl') if r['event']=='dispatch'}
    snapshot = read(FULL/'snapshot_check.json')
    for g in GROUPS:
        for selected in manifest['groups'][g]:
            row, t = by_id[selected['dialogue_id']], selected['turn']
            rid = f"{selected['dialogue_id']}:{t}"
            messages = prompt(row, user_turns(row)[t-1])
            if sha(messages) != baseline[rid]['prompt_sha256']:
                raise RuntimeError(f'Spark prompt mismatch: {rid}')
            plans.append(dict(selected, group=g, request_id=rid, messages=messages, prompt_sha256=sha(messages)))
            for source in ('human','simulator'):
                if source == 'simulator' and baseline[rid]['status'] == 'moderated':
                    continue
                item = dict(id=f'{source}:{rid}', **judge_item(row,t,baseline[rid]['text'] if source=='simulator' else None))
                todo.append(item)
                digest = input_sha256(jp,item)
                success = next((r for r in responses if r['id']==item['id'] and r['status']=='ok'), None)
                if success is None:
                    raise RuntimeError('Full judgment missing')
                old = dispatch[(item['id'],success['attempt'])].get('input_sha256')
                # The legacy human cache was explicitly accepted by Claude; bind it
                # to the complete frozen full snapshot before materializing fingerprints.
                if old is None:
                    if source != 'human' or not snapshot['accepted'] or not snapshot['trust_human_inputs']['accepted']:
                        raise RuntimeError('Unproven legacy judgment')
                    provenance = 'Claude-accepted human snapshot + full input_sha256.txt'
                else:
                    if old != digest:
                        raise RuntimeError('Full judgment fingerprint differs')
                    provenance = 'dispatch input_sha256'
                reuse.append(dict(id=item['id'],input_sha256=digest,prediction=pred[item['id']],attempt=success['attempt'],provenance=provenance))
    digest = hashlib.sha256(json.dumps(todo,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if digest != (FULL/'input_sha256.txt').read_text().strip():
        raise RuntimeError('Full aggregate input fingerprint differs')
    return manifest, plans, reuse, jp, by_id


class Guard:
    """Meter every POST, including client retries; ledger survives restarts."""
    def __init__(self):
        self.lock = threading.Lock()
        self.stopped = False

    def check(self, model):
        if self.stopped or (OUT/'STOPPED.json').exists():
            raise RuntimeError('Stopped; no further paid calls; review STOPPED.json')
        for attempt in range(6):
            if self.stopped or (OUT/'STOPPED.json').exists():
                raise RuntimeError('Stopped; no further paid calls; review STOPPED.json')
            try:
                r = httpx.get(os.environ['SANDBOX_BASE_URL'].rstrip('/')+'/v1/dashboard/billing/usage',
                              headers={'Authorization':'Bearer '+os.environ['SANDBOX_API_KEY']},timeout=30)
                r.raise_for_status()
                cents = r.json()['total_usage']
                if type(cents) not in (float,int) or cents < 0:
                    raise ValueError()
                break
            except Exception as exc:
                with self.lock:
                    with (OUT/'billing.jsonl').open('a') as f:
                        f.write(json.dumps(dict(event='billing_retry_error', model=model, attempt=attempt+1,
                                                error_type=type(exc).__name__,
                                                timestamp=datetime.now(timezone.utc).isoformat()))+'\n')
                if attempt == 5:
                    self.stopped = True
                    raise RuntimeError('Cannot verify billing after 6 attempts; stop') from None
                time.sleep((2,4,8,16,30)[attempt])
        with self.lock:
            ledger = lines(OUT/'billing.jsonl')
            count = sum(x.get('model')==model and x.get('event')=='before_request' for x in ledger)
            row = dict(event='before_request' if model else 'checkpoint', model=model,
                       total_usage_cents=cents, timestamp=datetime.now(timezone.utc).isoformat())
            with (OUT/'billing.jsonl').open('a') as f:
                f.write(json.dumps(row)+'\n')
            if cents >= 4900 or (model and count >= 3500):
                self.stopped = True
                raise RuntimeError('Gateway spending/request stop limit reached')
        return cents


class PaidTransport(httpx.BaseTransport):
    def __init__(self, guard):
        self.guard = guard
        self.inner = httpx.HTTPTransport()

    def handle_request(self, request):
        payload = json.loads(request.content)
        model = payload['model']
        if model == MODEL:
            payload['thinking'] = {'type':'disabled'}
            payload['reasoning_effort'] = 'none'
            request = httpx.Request(request.method, request.url,
                                    headers={k:v for k,v in request.headers.items() if k.lower() != 'content-length'},
                                    json=payload, extensions=request.extensions)
        self.guard.check(model)
        response = self.inner.handle_request(request)
        response.read()
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = (body.get('choices') or [{}])[0].get('message') or {}
        with self.guard.lock:
            with (OUT/'response_diagnostics.jsonl').open('a') as f:
                f.write(json.dumps(dict(model=model, request_sha256=hashlib.sha256(request.content).hexdigest(),
                                        http_status=response.status_code, usage=body.get('usage'),
                                        reasoning_content_present='reasoning_content' in message,
                                        reasoning_content_nonempty=bool(message.get('reasoning_content'))))+'\n')
        if response.status_code == 400:
            try:
                code = str(response.json().get('error',{}).get('code'))
            except (ValueError,AttributeError):
                code = None
            if code in ('10013','content_policy_violation','content_filter'):
                # Existing clients accept this successful sentinel; record separately.
                with self.guard.lock:
                    with (OUT/'moderation.jsonl').open('a') as f:
                        f.write(json.dumps(dict(model=model, request_sha256=hashlib.sha256(request.content).hexdigest(),code=code))+'\n')
                content = '' if model == MODEL else '{"topic":"unclear","emotion":false}'
                return httpx.Response(200,json=dict(choices=[dict(message=dict(content=content))],usage=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0,reasoning_tokens=0),moderated=True))
        if response.is_success:
            if model == MODEL:
                u = body['usage']
                details = u.get('completion_tokens_details')
                explicit_zero = isinstance(details,dict) and details.get('reasoning_tokens') == 0
                absent_details = 'completion_tokens_details' not in u
                if message.get('reasoning_content') or not (explicit_zero or absent_details):
                    self.guard.stopped = True
                    raise RuntimeError('DeepSeek zero-reasoning check failed; see response_diagnostics.jsonl')
        return response

    def close(self):
        self.inner.close()


class AsyncPaidTransport(httpx.AsyncBaseTransport):
    def __init__(self, guard):
        self.inner = PaidTransport(guard)

    async def handle_async_request(self, request):
        response = await asyncio.to_thread(self.inner.handle_request,request)
        return httpx.Response(response.status_code,
                              headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-encoding','content-length')},
                              content=response.content)

    async def aclose(self):
        self.inner.close()


def moderated_requests():
    return {r['request_sha256'] for r in lines(OUT/'moderation.jsonl')}


def payload_sha(model, messages):
    payload = dict(model=model,messages=messages,stream=False)
    if model == MODEL:
        payload['thinking'] = {'type':'disabled'}
        payload['reasoning_effort'] = 'none'
    return hashlib.sha256(json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


async def generate(plans, guard, concurrency):
    done = {r['request_id']:r for r in lines(GEN/'generations.jsonl')}
    client = Spark(GEN,concurrency,provider='sandbox',model=MODEL,transport=AsyncPaidTransport(guard))
    async def one(p):
        rid = p['request_id']
        if rid in done:
            if done[rid]['prompt_sha256'] != p['prompt_sha256']:
                raise RuntimeError('Cached generation fingerprint differs')
            return
        text = await client.generate(p['messages'],rid)
        status = 'moderated' if payload_sha(MODEL,p['messages']) in moderated_requests() else 'ok'
        item = {k:p[k] for k in ('request_id','dialogue_id','turn','prompt_sha256')}
        item.update(status=status,text=None if status=='moderated' else text)
        with (GEN/'generations.jsonl').open('a') as f:
            f.write(json.dumps(item,ensure_ascii=False)+'\n')
        done[rid] = item
        if len(done)%20 == 0:
            print(f'Generated {len(done)}/3000',flush=True)
    tasks = [asyncio.create_task(one(p)) for p in plans]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        guard.stopped = True
        await asyncio.gather(*tasks,return_exceptions=True)
        raise
    finally:
        await client.close()


def parse_b(text):
    """Claude-approved B-only key whitespace tolerance, strict schema unchanged."""
    def unique(pairs):
        result = {}
        for key,value in pairs:
            key = key.strip()
            if key in result:
                raise ValueError('Duplicate normalized JSON key')
            result[key] = value
        return result
    value = json.loads(text,object_pairs_hook=unique)
    return parse(json.dumps(value,ensure_ascii=False))


class BJudge(Judge):
    """B-only transport retries and cumulative failure-rate stop gate."""
    def __init__(self, cache, guard, previous=(), transport=None):
        super().__init__(cache,transport=transport)
        self.guard = guard
        self.stats_lock = threading.Lock()
        responses = [r for r in previous if r.get('event')=='response']
        self.responses = len(responses)
        self.failures = sum(r['status']!='ok' for r in responses)

    def evaluate(self, prompt, item, previous=(), *, attempt_offset=0):
        attempts = [r for r in previous if r['id']==item['id'] and r.get('event')=='response']
        success = next((r for r in attempts if r['status']=='ok'),None)
        if success is not None:
            return success['prediction']
        # Authorized legacy failures count toward the same three-attempt limit.
        consecutive = len(attempts)
        if consecutive >= 3:
            self.guard.stopped = True
            raise RuntimeError('B judge item already failed 3 times; stop')
        first = max((r['attempt'] for r in attempts),default=attempt_offset)+1
        for attempt in range(first,first+3-consecutive):
            if self.guard.stopped:
                raise RuntimeError('B judge stopped; no further requests')
            self.record(dict(id=item['id'],attempt=attempt,event='dispatch',model='gemini-3.8-flash',
                             provider='sandbox',input_sha256=input_sha256(prompt,item)))
            row = dict(id=item['id'],attempt=attempt,event='response',model='gemini-3.8-flash',
                       provider='sandbox',temperature='official default (omitted)',usage=None)
            retryable = False
            try:
                response = self.client.post(self.url,json=dict(model='gemini-3.8-flash',messages=judge_messages(prompt,item),stream=False))
                row['http_status'] = response.status_code
                response.raise_for_status()
                body = response.json()
                row['usage'] = body.get('usage')
                if not isinstance(row['usage'],dict) or not all(type(row['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens','total_tokens')):
                    raise RuntimeError('Missing actual token usage')
                row['content'] = body['choices'][0]['message']['content']
                try:
                    row['prediction'] = parse_b(row['content'])
                except (ValueError,TypeError):
                    row['status'] = 'parse_error'
                else:
                    row['status'] = 'ok'
            except (httpx.TransportError,httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc,httpx.TransportError) or 500 <= exc.response.status_code < 600
                row.update(status='request_error',error_type=type(exc).__name__,retryable=retryable)
            except Exception as exc:
                row.update(status='request_error',error_type=type(exc).__name__,retryable=False)
            finally:
                self.record(row)
            with self.stats_lock:
                self.responses += 1
                self.failures += row['status']!='ok'
                rate_exceeded = self.failures/self.responses > .02
                if rate_exceeded:
                    self.guard.stopped = True
            if rate_exceeded:
                raise RuntimeError('B judge cumulative attempt failure rate exceeds 2%; stop')
            if row['status']=='ok':
                return row['prediction']
            consecutive += 1
            if not retryable or consecutive >= 3:
                self.guard.stopped = True
                raise RuntimeError('B judge parse/non-retryable error or 3 consecutive failures; stop')
            time.sleep(2**consecutive)
        raise RuntimeError('B judge exhausted attempts')


def judge(plans, jp, by_id, guard, concurrency):
    generated = {r['request_id']:r for r in lines(GEN/'generations.jsonl')}
    previous = cached(OUT/'judgments.jsonl',allow_request_errors=True)
    pred = predictions(previous)
    client = BJudge(OUT/'judgments.jsonl',guard,previous,transport=PaidTransport(guard))
    def one(p):
        rid = p['request_id']
        gen = generated[rid]
        if gen['status']=='moderated':
            return
        item = dict(id='deepseek:'+rid,**judge_item(by_id[p['dialogue_id']],p['turn'],gen['text']))
        if pred.get(item['id']) is not None:
            return
        value = client.evaluate(jp,item,previous)
        if value is None:
            guard.stopped = True
            raise RuntimeError('Judge exhausted retries; stop')
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for _ in pool.map(one,plans):
                pass
    except BaseException:
        guard.stopped = True
        raise
    finally:
        client.close()


def report(plans, reuse, jp, by_id):
    generated = {r['request_id']:r for r in lines(GEN/'generations.jsonl')}
    if set(generated) != {p['request_id'] for p in plans}:
        raise RuntimeError('Generation turn list differs from full')
    old = {r['id']:r['prediction'] for r in reuse}
    rows = cached(OUT/'judgments.jsonl',allow_request_errors=True)
    pred = predictions(rows)
    dispatch = {r['id']:r for r in lines(OUT/'judgments.jsonl') if r['event']=='dispatch'}
    from usersim.judge import messages as judge_messages
    pairs, excluded = [], []
    for p in plans:
        rid = p['request_id']
        gen = generated[rid]
        if gen['prompt_sha256'] != p['prompt_sha256']:
            raise RuntimeError('Generation prompt differs')
        reason = None
        if gen['status']=='moderated':
            reason = 'deepseek_moderated'
        elif 'simulator:'+rid not in old:
            reason = 'spark_moderated'
        else:
            item = dict(id='deepseek:'+rid,**judge_item(by_id[p['dialogue_id']],p['turn'],gen['text']))
            if item['id'] not in pred or pred[item['id']] is None:
                raise RuntimeError('Missing DeepSeek judgment')
            if dispatch[item['id']]['input_sha256'] != input_sha256(jp,item):
                raise RuntimeError('DeepSeek judge fingerprint differs')
            if payload_sha('gemini-3.8-flash',judge_messages(jp,item)) in moderated_requests():
                reason = 'judge_moderated'
        if reason:
            excluded.append(dict(request_id=rid,group=p['group'],reason=reason))
        else:
            pairs.append(dict(dialogue_id=p['dialogue_id'],group=p['group'],human=old['human:'+rid],spark=old['simulator:'+rid],deepseek=pred['deepseek:'+rid]))
    stats = {s:summarize([dict(p,simulator=p[s]) for p in pairs]) for s in ('spark','deepseek')}
    usage = lines(GEN/'request_usage.jsonl')
    reasoning = sum(r['reasoning_tokens'] for r in usage)
    if reasoning != 0:
        raise RuntimeError('Nonzero reasoning tokens')
    billing = lines(OUT/'billing.jsonl')
    cost = dict(gateway_start_cents=billing[0]['total_usage_cents'],gateway_end_cents=billing[-1]['total_usage_cents'],
                gateway_delta_usd=(billing[-1]['total_usage_cents']-billing[0]['total_usage_cents'])/100,
                attribution='Shared account delta is an upper bound; concurrent task spending may be included.',
                pilot=read(OUT/'pilot.json'))
    paid_checks = Counter(r['model'] for r in billing if r['event']=='before_request')
    summary = dict(statistics=stats,selected={g:1500 for g in GROUPS},common_valid=len(pairs),
                   common_by_group=dict(Counter(p['group'] for p in pairs)),excluded=excluded,
                   deepseek_reasoning_tokens=reasoning,deepseek_requests=paid_checks[MODEL],
                   gemini_requests=paid_checks['gemini-3.8-flash'],cost=cost)
    dump(OUT/'summary.json',summary)
    text = ['# DeepSeek ADR-016 分组测量（合同 B）','',
            '差距=模拟器−真人；分母包含 unclear。Spark 与 DeepSeek 只用三方共同有效轮次。',
            '按对话配对 bootstrap：2000 次，种子 20261026，两组共同重采样对话，95% 百分位线性插值区间。',
            f'抽样：每组 1500；三方共同有效：{len(pairs)}；分组：{summary["common_by_group"]}；剔除数：{len(excluded)}（{dict(Counter(x["reason"] for x in excluded))}）。',
            f'DeepSeek 推理 token 总数：{reasoning}；DeepSeek 请求前计数：{summary["deepseek_requests"]}；Gemini 请求前计数：{summary["gemini_requests"]}（包含重试和原已放弃请求；按 billing.jsonl 计数，不将停止时未发送任务的 finally 日志算为请求）。',
            f'费用：共享网关累计差额 ${cost["gateway_delta_usd"]:.6f}（其他任务共用账户，属于本任务费用上界）；小批全量预测 ${cost["pilot"]["projected_usd"]:.6f}。','',
            '|组|指标|真人|Spark|Spark 差距 [95% CI]|DeepSeek|DeepSeek 差距 [95% CI]|轮次|',
            '|---|---|---:|---:|---|---:|---|---:|']
    for g in GROUPS:
        for m in ('continue','emotion'):
            s,d = (stats[x][m]['groups'][g] for x in ('spark','deepseek'))
            text.append(f'|{g}|{m}|{d["human"]:.6f}|{s["simulator"]:.6f}|{s["gap"]:+.6f} {s["interval"]}|{d["simulator"]:.6f}|{d["gap"]:+.6f} {d["interval"]}|{d["n"]}|')
    text += ['', '|模型|指标|交互项（负面差距−非负面差距）|95% CI|','|---|---|---:|---|']
    for source in ('spark','deepseek'):
        for m in ('continue','emotion'):
            v = stats[source][m]
            text.append(f'|{source}|{m}|{v["interaction"]:+.6f}|{v["interval"]}|')
    text += ['', '预先登记判定：仅当 continue 交互项区间不含零且负面组差距绝对值更大，限定在被拒之后；否则用普遍定义。情绪不参与判定。',
             f'Spark：{stats["spark"]["decision"]}；DeepSeek：{stats["deepseek"]["decision"]}。','',
             '真人与 Spark 不重判，按 full 冻结输入指纹复用；旧真人缓存采用 Claude 已批准的快照证据，并核对完整输入指纹。逐项来源见 reused_judgments.json。',
             '局限：只比较真人确实回复的轮次；审核剔除可能产生选择偏差。费用上界包含同时运行任务，无法仅凭共享账户累计值分离实际费用。',
             '剔除明细：'+json.dumps(excluded,ensure_ascii=False)]
    (OUT/'report.md').write_text('\n'.join(text)+'\n')
    print(json.dumps(dict(common_valid=len(pairs),excluded=len(excluded),decision=stats['deepseek']['decision']),ensure_ascii=False),flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report-only',action='store_true')
    ap.add_argument('--pilot-only',action='store_true')
    ap.add_argument('--concurrency',type=int,default=8)
    args = ap.parse_args()
    if args.concurrency < 1:
        ap.error('concurrency must be positive')
    manifest, plans, reuse, jp, by_id = prepare()
    if args.report_only:
        validate_manifest(read(GEN/'manifest.json'),manifest)
        if read(GEN/'prompts.json') != plans or read(OUT/'reused_judgments.json') != reuse:
            raise RuntimeError('Frozen plans/reuse differ')
        report(plans,reuse,jp,by_id)
        return
    OUT.mkdir(parents=True,exist_ok=True)
    GEN.mkdir(parents=True,exist_ok=True)
    freeze(GEN/'manifest.json',manifest)
    freeze(GEN/'prompts.json',plans)
    freeze(OUT/'reused_judgments.json',reuse)
    for line in (ROOT/'.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            k,v = line.removeprefix('export ').split('=',1)
            if k.strip() in ('SANDBOX_API_KEY','SANDBOX_BASE_URL'):
                os.environ.setdefault(k.strip(),shlex.split(v)[0])
    guard = Guard()
    pilot = plans[:10]+plans[1500:1510]
    if not (OUT/'pilot.json').exists():
        start = guard.check(None)
        asyncio.run(generate(pilot,guard,args.concurrency))
        judge(pilot,jp,by_id,guard,args.concurrency)
        end = guard.check(None)
        projection = (end-start)/100*150
        dump(OUT/'pilot.json',dict(turns=20,request_ids=[p['request_id'] for p in pilot],start_cents=start,end_cents=end,
                                  projected_usd=projection,method='20 paired generation+judge gateway delta × 3000/20; shared-account upper bound'))
    print('Pilot: '+json.dumps(read(OUT/'pilot.json')),flush=True)
    if read(OUT/'pilot.json')['projected_usd'] >= 7:
        raise RuntimeError('Pilot projects over $7; stop')
    if args.pilot_only:
        return
    print('Full generation started',flush=True)
    asyncio.run(generate(plans,guard,args.concurrency))
    judge(plans,jp,by_id,guard,args.concurrency)
    guard.check(None)
    report(plans,reuse,jp,by_id)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        if OUT.exists() and '--report-only' not in sys.argv:
            dump(OUT/'STOPPED.json',dict(reason=str(exc),timestamp=datetime.now(timezone.utc).isoformat(),
                                       instruction='No further paid calls until Claude reviews the stop evidence.'))
        raise
