"""Pilot first; full run resumes, reports and verifies offline reproducibility."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from usersim.csds import load
from usersim.leave import (SEED, select, write_json, read_generations, build_report)
from usersim.judge_spark import SparkJudge, cache_writer


class Generator(SparkJudge):
    def generate(self, point, previous):
        history = [r for r in previous if r['id']==point['id']]
        if any(r['input_sha256']!=point['input_sha256'] for r in history):
            raise RuntimeError('Cached prompt differs')
        responses = [r for r in history if r['event']=='response']
        for r in responses:
            if r['status'] in ('ok','moderated'):
                return r
        failures = sum(r['status']=='request_error' for r in responses)
        attempt = max((r['attempt'] for r in history),default=0)
        limits = 0
        while failures < 4:
            self.ensure_progress()
            attempt += 1
            base = dict(id=point['id'],attempt=attempt,input_sha256=point['input_sha256'],
                        model='spark-x2.6',provider='spark',thinking='disabled')
            self.record(dict(base,event='dispatch'))
            row = dict(base,event='response',usage=None,text=None)
            delay = None
            try:
                response = self.post(dict(model='spark-x2.6',messages=point['messages'],
                                          stream=False,thinking={'type':'disabled'}))
                row['http_status'] = response.status_code
                if response.status_code == 429:
                    delay = min(300,5*2**min(limits,6))
                    delay = min(300,delay+random.Random(f'{SEED}:{point["id"]}:{limits}').uniform(0,delay*.2))
                    limits += 1
                    row.update(status='rate_limited',budget_consumed=False)
                elif response.status_code==400 and str(response.json().get('error',{}).get('code'))=='10013':
                    row['status']='moderated'
                    self._failure()
                else:
                    response.raise_for_status()
                    body=response.json()
                    row['usage']=body['usage']
                    if not all(type(row['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens','total_tokens')):
                        raise ValueError('Missing usage')
                    text=body['choices'][0]['message']['content']
                    if not isinstance(text,str):
                        raise ValueError('Invalid content')
                    if (row['usage'].get('reasoning_tokens',0) or (row['usage'].get('completion_tokens_details') or {}).get('reasoning_tokens',0)):
                        raise RuntimeError('Unexpected reasoning tokens; stop')
                    row.update(status='ok',text=text)
                    self._success()
            except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
                failures += 1
                row.update(status='request_error',error_type=type(exc).__name__,budget_consumed=True)
                self._failure()
            finally:
                self.record(row)
            if row['status'] in ('ok','moderated'):
                return row
            self._wait(delay if delay is not None else 2**(failures-1))
        raise self.stop('transport_budget_exhausted','Spark failed four non-429 attempts; no fallback')


def recover_requests(cache):
    """Recover generation requests without invoking the judge JSON parser."""
    rows = [json.loads(line) for line in cache.read_text().splitlines()] if cache.exists() else []
    dispatched = [(r['id'],r['attempt']) for r in rows if r['event']=='dispatch']
    responded = [(r['id'],r['attempt']) for r in rows if r['event']=='response']
    if len(set(dispatched))!=len(dispatched) or len(set(responded))!=len(responded) or not set(responded)<=set(dispatched):
        raise RuntimeError('Duplicate or inconsistent request cache')
    for r in list(rows):
        if r['event']=='dispatch' and (r['id'],r['attempt']) not in set(responded):
            recovered=dict(r,event='response',status='request_error',usage=None,text=None,
                           error_type='InterruptedProcess',budget_consumed=True)
            with cache.open('a') as f:
                f.write(json.dumps(recovered,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
            rows.append(recovered)
    return rows


def load_env():
    # Read only Spark credential; never access paid provider configuration.
    if not os.environ.get('SPARK_API_KEY'):
        for line in Path('.env').read_text().splitlines():
            if line.startswith('SPARK_API_KEY='):
                os.environ['SPARK_API_KEY']=line.split('=',1)[1].strip().strip('\"\'')


def main(args):
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    if args.phase=='report':
        print(json.dumps(build_report(out),sort_keys=True))
        return
    manifest=select(load(), n=10 if args.phase=='pilot' else 2000)
    manifest['phase']=args.phase
    path=out/'manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest:
        raise RuntimeError('Manifest differs; use separate output directory for full run')
    write_json(path,manifest)
    cache=out/'requests.jsonl'
    with cache_writer(cache):
        previous=recover_requests(cache)
        completed=read_generations(out/'generations.jsonl')
        points=manifest['points']
        for p in points:
            if p['id'] in completed and completed[p['id']]['input_sha256']!=p['input_sha256']:
                raise RuntimeError('Stale generation')
        pending=[p for p in points if p['id'] not in completed]
        if pending:
            load_env()
            client=Generator(cache,'disabled',concurrency=args.concurrency)
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    try:
                        for p,r in zip(pending,pool.map(lambda p:client.generate(p,previous),pending)):
                            item=dict(id=p['id'],input_sha256=p['input_sha256'],status=r['status'],text=r['text'])
                            with (out/'generations.jsonl').open('a') as f:
                                f.write(json.dumps(item,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
                            print(f'completed {len(completed)+1}/{len(points)}',flush=True)
                            completed[p['id']]=item
                    except BaseException:
                        client.stop('interrupted_batch',persist=False)
                        raise
                client._save_state(complete=True)
            except BaseException:
                client.stop('interrupted_batch',persist=False)
                raise
            finally:
                client.close()
    first=build_report(out)
    second=build_report(out)
    if first!=second:
        raise RuntimeError('Offline rebuild SHA-256 mismatch')
    write_json(out/'offline_acceptance.json',dict(first=first,second=second,identical=True))
    print(json.dumps(dict(completed=len(points),sha256=second),sort_keys=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['pilot','full','report'],required=True)
    parser.add_argument('--out',default='outputs/leave')
    parser.add_argument('--concurrency',type=int,choices=range(1,9),default=4)
    main(parser.parse_args())
