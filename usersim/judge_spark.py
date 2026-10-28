"""Source-blind Spark judge; strict parsing, durable cache, no content retries."""
import hashlib
import json
import os
from pathlib import Path
import time
import threading
import random
import fcntl
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import httpx

from usersim.judge import messages, parse as strict_parse, input_sha256
from usersim.spark import URL

MODEL = 'spark-x2.6'
PARSER_VERSION = 'outer_wrappers_v1'
_CACHE_LOCK = threading.RLock()


def parse(text):
    """Remove only complete outer wrappers, then enforce the frozen JSON schema."""
    if not isinstance(text, str):
        raise TypeError('Judge content must be text')
    text = text.strip()
    while True:
        if text.startswith('```json') and text.endswith('```'):
            text = text[len('```json'):-3].strip()
        elif len(text) >= 4 and text.startswith('**') and text.endswith('**'):
            text = text[2:-2].strip()
        elif len(text) >= 2 and text.startswith('*') and text.endswith('*'):
            text = text[1:-1].strip()
        else:
            return strict_parse(text)


def reparse_cached(row):
    if (row.get('event') != 'response' or row.get('status') not in ('ok','parse_error')
            or row.get('parser_version') == PARSER_VERSION):
        return row
    row = dict(row, cached_original_status=row['status'], parser_version=PARSER_VERSION)
    row.pop('prediction', None)
    try:
        row['prediction'] = parse(row['content'])
        row['status'] = 'ok'
    except (ValueError, TypeError):
        row['status'] = 'parse_error'
    return row


def frozen_prompt(root):
    root = Path(root)
    final = json.loads((root/'outputs/judge_validation/final_prompt.json').read_text())
    prompt = (root/'config/judge_prompt.md').read_bytes()
    if hashlib.sha256(prompt).hexdigest() != final['sha256']:
        raise RuntimeError('Frozen prompt mismatch')
    if (root/f"outputs/judge_validation/prompt_{final['version']}.md").read_bytes() != prompt:
        raise RuntimeError('Frozen prompt bytes differ')
    return prompt.decode()


def read_cache(path, *, allow_pending=False):
    path = Path(path)
    with _CACHE_LOCK:
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    dispatched = [(r['id'], r['attempt']) for r in rows if r['event']=='dispatch']
    responses = [(r['id'], r['attempt']) for r in rows if r['event']=='response']
    if (len(set(dispatched)) != len(dispatched) or len(set(responses)) != len(responses)
            or not set(responses)<=set(dispatched)
            or (not allow_pending and sorted(dispatched)!=sorted(responses))):
        raise RuntimeError('Unresolved or duplicate dispatch; stop, never resend blindly')
    return [reparse_cached(row) for row in rows]


RETRY_POLICY = 'adaptive_429_v1'
NO_SUCCESS_SECONDS = 3600


class JudgeStopped(RuntimeError):
    def __init__(self, reason, message=None):
        self.reason = reason
        super().__init__(message or reason)


def write_stop(cache, reason, message=None):
    _write_json(Path(str(cache)+'.stop.json'),
                dict(reason=reason,message=message or reason,observed_at=time.time()))


def _write_json(path, value):
    with _CACHE_LOCK:
        temporary = Path(str(path)+'.tmp')
        with temporary.open('w') as f:
            json.dump(value,f,ensure_ascii=False,indent=2,sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)


def _append(cache, row, secret=None):
    text = json.dumps(row,ensure_ascii=False)
    if secret:
        text = text.replace(secret,'[REDACTED]')
    with _CACHE_LOCK:
        with Path(cache).open('a') as f:
            f.write(text+'\n')
            f.flush()
            os.fsync(f.fileno())


@contextmanager
def cache_writer(cache):
    """Exclusive process ownership makes crash recovery safe for batch callers."""
    with Path(str(cache)+'.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Spark cache already has an active batch writer') from None
        try:
            yield
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def _recover_interrupted(cache):
    # Called only while holding exclusive process ownership, never by a reader.
    try:
        rows = read_cache(cache,allow_pending=True)
    except (ValueError, RuntimeError) as exc:
        write_stop(cache,'cache_integrity',str(exc))
        raise JudgeStopped('cache_integrity',str(exc)) from None
    responded = {(r['id'],r['attempt']) for r in rows if r['event']=='response'}
    for row in rows:
        if row['event']=='dispatch' and (row['id'],row['attempt']) not in responded:
            _append(cache,dict(row,event='response',status='request_error',usage=None,
                error_type='InterruptedProcess',process_exit_observed=True,
                retry_policy=RETRY_POLICY,budget_consumed=True,
                error_reason='Prior writer exited; exclusive cache writer lock acquired for recovery.'))
    return read_cache(cache)


class SparkJudge:
    def __init__(self, path, thinking, transport=None, *, concurrency=1,
                 clock=None, sleeper=None, jitter=None):
        if thinking not in ('disabled','enabled'):
            raise ValueError('Invalid thinking variant')
        if not 1 <= concurrency <= 8:
            raise ValueError('Concurrency must be between 1 and 8')
        self.path = Path(path)
        self.thinking = thinking
        self.concurrency = concurrency
        self.rate_limit_hits = 0
        self._active = 0
        self._condition = threading.Condition()
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._jitter = jitter
        self._stopped = None
        self._success_streak = 0
        self._last_success = self._clock()
        self._last_success_wall = time.time()
        self._runtime_path = Path(str(path)+'.runtime.json')
        stop = Path(str(path)+'.stop.json')
        if stop.exists():
            state = json.loads(stop.read_text())
            raise JudgeStopped(state['reason'],state['message'])
        if self._runtime_path.exists():
            state = json.loads(self._runtime_path.read_text())
            if state.get('policy')==RETRY_POLICY and not state.get('batch_complete'):
                self.concurrency = min(8,max(1,state['concurrency']))
                self._success_streak = state.get('success_streak',0)
                self._last_success_wall = state['last_success_wall_time']
                self._last_success -= max(0,time.time()-self._last_success_wall)
        self.ensure_progress()
        self.secret = os.environ['SPARK_API_KEY']
        self.client = httpx.Client(timeout=120,transport=transport,
            headers={'Authorization':'Bearer '+self.secret})
        self._save_state()

    def _save_state(self, *, complete=False):
        _write_json(self._runtime_path,dict(policy=RETRY_POLICY,concurrency=self.concurrency,
            success_streak=self._success_streak,last_success_wall_time=self._last_success_wall,
            rate_limit_hits=self.rate_limit_hits,batch_complete=complete,jitter_seed=20261027))

    def stop(self, reason, message=None, *, persist=True):
        with self._condition:
            if self._stopped is None:
                self._stopped = JudgeStopped(reason,message)
                if persist:
                    write_stop(self.path,reason,str(self._stopped))
            self._condition.notify_all()
        return self._stopped

    def ensure_progress(self):
        with self._condition:
            if self._stopped is not None:
                raise self._stopped
            if self._clock()-self._last_success >= NO_SUCCESS_SECONDS:
                raise self.stop('no_success_60_minutes','No successful Spark response for 60 minutes')

    def _wait(self, delay):
        remaining = delay
        while remaining > 0:
            self.ensure_progress()
            part = min(1,remaining)
            self._sleep(part)
            remaining -= part
        self.ensure_progress()

    def _success(self):
        with self._condition:
            self.ensure_progress()
            self._last_success = self._clock()
            self._last_success_wall = time.time()
            self._success_streak += 1
            if self._success_streak >= 200:
                self.concurrency = min(8,self.concurrency+1)
                self._success_streak = 0
                print(f'Spark 200 consecutive successes: concurrency={self.concurrency}',flush=True)
            self._save_state()
            self._condition.notify_all()

    def _failure(self):
        with self._condition:
            self._success_streak = 0
            self._save_state()

    def close(self):
        self.client.close()

    def record(self, row):
        _append(self.path,row,self.secret)

    def post(self, payload):
        with self._condition:
            while self._active >= self.concurrency:
                self.ensure_progress()
                self._condition.wait(timeout=1)
            self.ensure_progress()
            self._active += 1
        try:
            response = self.client.post(URL,json=payload)
            if response.status_code==429:
                with self._condition:
                    self.rate_limit_hits += 1
                    self.concurrency = max(1,self.concurrency//2)
                    adjusted = self.concurrency
                    response.extensions['spark_concurrency_after_429'] = adjusted
                    self._success_streak = 0
                    self._save_state()
                    self._condition.notify_all()
                print(f'Spark HTTP 429: concurrency={adjusted}; unlimited jittered backoff',flush=True)
            return response
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def evaluate(self, prompt, item, previous=()):
        digest = input_sha256(prompt,item)
        old = [r for r in previous if r['id']==item['id']]
        for row in old:
            if row['input_sha256']!=digest or row['thinking']!=self.thinking:
                raise RuntimeError('Cached input or variant changed')
        responses = [r for r in old if r['event']=='response']
        for row in responses:
            if row['status'] in ('ok','moderated','parse_error'):
                return row
        # Legacy HTTP429 records are retroactively exempt; their bytes stay intact.
        failures = sum(r['status']=='request_error' and r.get('http_status')!=429 for r in responses)
        sequence = max((r['attempt'] for r in responses),default=0)
        rate_retries = 0
        while True:
            self.ensure_progress()
            if failures >= 4:
                raise self.stop('transport_budget_exhausted','Spark exhausted four non-429 transport attempts')
            sequence += 1
            base = dict(id=item['id'],input_sha256=digest,thinking=self.thinking,
                attempt=sequence,transport_attempt=failures+1,model=MODEL,provider='spark',retry_policy=RETRY_POLICY)
            self.record(dict(base,event='dispatch'))
            row = dict(base,event='response',usage=None,temperature='official default (omitted)',
                       parser_version=PARSER_VERSION,budget_consumed=False)
            delay = None
            try:
                response = self.post(dict(model=MODEL,messages=messages(prompt,item),stream=False,
                                          thinking={'type':self.thinking}))
                row['http_status'] = response.status_code
                if response.status_code==429:
                    backoff = min(300,5*2**min(rate_retries,6))
                    delay = (self._jitter(backoff) if self._jitter else
                        min(300,backoff+random.Random(f'20261027:{digest}:{rate_retries}').uniform(0,backoff*.2)))
                    delay = min(300,max(0,delay))
                    rate_retries += 1
                    row.update(status='rate_limited',rate_limit_backoff_seconds=delay,
                               current_concurrency=response.extensions['spark_concurrency_after_429'])
                elif response.status_code==400 and str(response.json().get('error',{}).get('code'))=='10013':
                    row['status'] = 'moderated'
                    self._failure()
                else:
                    response.raise_for_status()
                    body = response.json()
                    row['usage'] = body['usage']
                    if not all(type(row['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens','total_tokens')):
                        raise ValueError('Missing usage')
                    row['content'] = body['choices'][0]['message']['content']
                    try:
                        row['prediction'] = parse(row['content'])
                        row['status'] = 'ok'
                    except (ValueError,TypeError):
                        row['status'] = 'parse_error'
                    if row['status']=='ok':
                        self._success()
                    else:
                        self._failure()
            except JudgeStopped:
                row.setdefault('status','cancelled')
                raise
            except (httpx.HTTPError,KeyError,ValueError,IndexError) as exc:
                failures += 1
                row.update(status='request_error',error_type=type(exc).__name__,budget_consumed=True)
                self._failure()
            finally:
                self.record(row)
            if row['status'] in ('ok','moderated','parse_error'):
                return row
            if row['status']=='rate_limited':
                self._wait(delay)
            elif failures < 4:
                self._wait(2**(failures-1))


def judge_batch(cache, items, concurrency=8, *, prompt=None, thinking='enabled',
                reuse_caches=(), transport=None, on_result=None, clock=None, sleeper=None, jitter=None):
    """Concurrent Spark judgments; original arguments remain compatible.

    Concurrency starts at the supplied value (1..8), halves on HTTP429, and rises
    by one after 200 consecutive successful new judgments, up to eight. HTTP429
    never spends the four-error transport budget; it backs off 5..300 seconds with
    deterministic seeded jitter. A shared 60-minute no-success watchdog persists
    across restarts. Input-fingerprint reuse and donor-cache immutability remain.
    """
    with cache_writer(cache):
        return _judge_batch_locked(cache,items,concurrency,prompt=prompt,thinking=thinking,
            reuse_caches=reuse_caches,transport=transport,on_result=on_result,
            clock=clock,sleeper=sleeper,jitter=jitter)


def _judge_batch_locked(cache, items, concurrency=8, *, prompt=None, thinking='enabled',
                reuse_caches=(), transport=None, on_result=None, clock=None, sleeper=None, jitter=None):
    """Lock-owned implementation of judge_batch; callbacks run serially."""
    if not 1 <= concurrency <= 8:
        raise ValueError('Concurrency must be between 1 and 8')
    if thinking not in ('enabled','disabled'):
        raise ValueError('Invalid thinking variant')
    cache = Path(cache)
    if prompt is None:
        prompt = frozen_prompt(Path(__file__).resolve().parents[1])
    items = list(items)
    if len({item['id'] for item in items}) != len(items):
        raise ValueError('Duplicate input ids')
    previous = _recover_interrupted(cache)
    by_id = {}
    for row in previous:
        by_id.setdefault(row['id'], []).append(row)
    histories = {}
    terminal = {}
    for row in previous:
        if (row.get('model') != MODEL or row.get('provider') != 'spark'
                or row.get('thinking') != thinking):
            continue
        digest = row['input_sha256']
        histories.setdefault(digest, []).append(row)
        if row.get('status') in ('ok','moderated','parse_error'):
            terminal[digest] = row
    donors = {}
    for path in reuse_caches:
        for row in read_cache(path, allow_pending=True):
            if (row.get('status')=='ok' and row.get('model')==MODEL
                    and row.get('provider')=='spark' and row.get('thinking')==thinking):
                donors.setdefault(row['input_sha256'], (str(path), row))
    fingerprints = {item['id']:input_sha256(prompt,item) for item in items}
    unique = {fingerprints[item['id']]:item for item in items}
    pending = []
    client = None
    try:
        for digest,item in unique.items():
            if digest in terminal:
                continue
            if digest in donors:
                path,row = donors[digest]
                imported = dict(id=digest,event='reuse',input_sha256=digest,
                    thinking=thinking,model=MODEL,provider='spark',status='ok',
                    prediction=row['prediction'],parser_version=PARSER_VERSION,
                    reused_from=path,reused_id=row['id'])
                # Reuse-only batches do not require credentials or an API client.
                with _CACHE_LOCK:
                    with cache.open('a') as f:
                        f.write(json.dumps(imported,ensure_ascii=False)+'\n')
                        f.flush()
                        os.fsync(f.fileno())
                terminal[digest] = imported
                continue
            history = histories.get(digest, [])
            request_id = history[-1]['id'] if history else digest
            if any(row.get('input_sha256')!=digest or row.get('thinking')!=thinking
                   or row.get('model')!=MODEL or row.get('provider')!='spark'
                   for row in by_id.get(request_id, [])):
                raise RuntimeError('Cached request id has incompatible input or model settings')
            pending.append((digest,dict(item,id=request_id),history))
        if pending:
            client = SparkJudge(cache,thinking,transport,concurrency=concurrency,clock=clock,sleeper=sleeper,jitter=jitter)
            todo = iter(pending)
            with ThreadPoolExecutor(max_workers=8) as pool:
                active = {}
                def submit():
                    entry = next(todo,None)
                    if entry is not None:
                        digest,item,history = entry
                        active[pool.submit(client.evaluate,prompt,item,history)] = digest
                for _ in range(8):
                    submit()
                try:
                    while active:
                        client.ensure_progress()
                        done,_ = wait(active,timeout=1,return_when=FIRST_COMPLETED)
                        for future in done:
                            digest = active.pop(future)
                            row = future.result()
                            terminal[digest] = row
                            if on_result is not None:
                                on_result(row)
                        for _ in done:
                            submit()
                except BaseException as exc:
                    client.stop(exc.reason if isinstance(exc,JudgeStopped) else 'interrupted_batch',
                                str(exc),persist=isinstance(exc,JudgeStopped))
                    for future in active:
                        future.cancel()
                    raise
        if client is not None:
            client._save_state(complete=True)
        return {item['id']:terminal[fingerprints[item['id']]] for item in items}
    finally:
        if client is not None:
            client.close()
