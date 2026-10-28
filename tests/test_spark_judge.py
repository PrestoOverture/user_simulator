import json
from pathlib import Path
import httpx
import pytest
from usersim.judge_spark import SparkJudge, frozen_prompt, read_cache

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_prompt():
    import hashlib
    prompt = frozen_prompt(ROOT)
    expected = json.loads((ROOT/'outputs/judge_validation/final_prompt.json').read_text())['sha256']
    assert hashlib.sha256(prompt.encode()).hexdigest()==expected


@pytest.mark.parametrize('thinking',['disabled','enabled'])
def test_source_blind_and_no_content_retry(tmp_path,monkeypatch,thinking):
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload['thinking']=={'type':thinking}
        view = json.loads(payload['messages'][1]['content'])
        assert set(view)=={'goal','history','previous_agent','text'}
        assert 'SOURCE_SENTINEL' not in request.content.decode()
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),choices=[dict(message=dict(content='bad json'))]))
    path = tmp_path/'cache.jsonl'
    judge = SparkJudge(path,thinking,httpx.MockTransport(handler))
    item = dict(id='SOURCE_SENTINEL',source='SOURCE_SENTINEL',goal='g',history=[],previous_agent='a',text='t')
    try:
        assert judge.evaluate(frozen_prompt(ROOT),item)['status']=='parse_error'
        assert judge.evaluate(frozen_prompt(ROOT),item,read_cache(path))['status']=='parse_error'
        assert len(calls)==1
    finally:
        judge.close()


def test_unresolved_dispatch(tmp_path):
    p = tmp_path/'cache.jsonl'
    p.write_text(json.dumps(dict(id='a',attempt=1,event='dispatch'))+'\n')
    with pytest.raises(RuntimeError,match='Unresolved'):
        read_cache(p)


def test_moderated_not_retried(tmp_path,monkeypatch):
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400,json={'error':{'code':10013,'message':'moderated'}})
    p = tmp_path/'cache.jsonl'
    judge = SparkJudge(p,'disabled',httpx.MockTransport(handler))
    item = dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    try:
        assert judge.evaluate('prompt',item)['status']=='moderated'
        assert len(calls)==1
    finally:
        judge.close()


def test_cached_input_cannot_change(tmp_path,monkeypatch):
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    def handler(request):
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    p = tmp_path/'cache.jsonl'
    judge = SparkJudge(p,'disabled',httpx.MockTransport(handler))
    item = dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    try:
        judge.evaluate('prompt',item)
        with pytest.raises(RuntimeError,match='changed'):
            judge.evaluate('different',item,read_cache(p))
    finally:
        judge.close()


def test_offline_partial_report_is_deterministic_without_client(tmp_path,monkeypatch):
    from scripts import validate_spark_judge as validation
    def forbidden(*args,**kwargs):
        raise AssertionError('Offline report must not construct an API client')
    monkeypatch.setattr(validation,'OUT',tmp_path)
    monkeypatch.setattr(validation,'SparkJudge',forbidden)
    validation.initialize()
    items = validation.frozen()
    validation.report(items)
    before = [(tmp_path/name).read_bytes() for name in ('report.md','summary.json')]
    validation.report(items)
    assert before==[(tmp_path/name).read_bytes() for name in ('report.md','summary.json')]
    assert json.loads((tmp_path/'summary.json').read_text())['status']=='incomplete'


def test_parse_stop_gate_blocks_later_phases(tmp_path,monkeypatch):
    from scripts import validate_spark_judge as validation
    monkeypatch.setattr(validation,'OUT',tmp_path)
    rows = [dict(status='ok') for _ in range(171)]+[dict(status='parse_error') for _ in range(4)]
    monkeypatch.setattr(validation,'responses',lambda:rows)
    monkeypatch.setattr('sys.argv',['validate_spark_judge.py','--phase','test'])
    def forbidden(*args,**kwargs):
        raise AssertionError('Stopped run must not construct an API client')
    monkeypatch.setattr(validation,'SparkJudge',forbidden)
    with pytest.raises(RuntimeError,match='4/175 exceed 2%'):
        validation.main()


@pytest.mark.parametrize('wrapper',[
    '{}', ' \n{}\t ', '**{}**', '*{}*',
    '```json\n{}\n```', '**\n```json\n{}\n```\n**',
])
def test_outer_wrapper_parser_accepts_only_wrapped_object(wrapper):
    from usersim.judge_spark import parse
    value = {'topic':'continue','emotion':False}
    assert parse(wrapper.format(json.dumps(value)))==value


@pytest.mark.parametrize('content',[
    'prefix {"topic":"close","emotion":false}',
    '{"topic":"close","emotion":false} suffix',
    '**prefix {"topic":"close","emotion":false}**',
    '```json\n{"topic":"close","emotion":false}\n``` suffix',
    '**{"topic":"invalid","emotion":false}**',
    '```json\n{"topic":"close","emotion":"false"}\n```',
    '*{"topic":"close","emotion":false,"extra":1}*',
    '**{"topic":"close","topic":"continue","emotion":false}**',
    '**{"topic":"close","emotion":false} {"topic":"close","emotion":false}**',
    '**{"topic":"close","emotion":false}*',
    '```python\n{"topic":"close","emotion":false}\n```',
    '```json\n[{"topic":"close","emotion":false}]\n```',
])
def test_outer_wrapper_parser_rejects_prose_or_invalid_schema(content):
    from usersim.judge_spark import parse
    with pytest.raises((ValueError,TypeError)):
        parse(content)


@pytest.mark.parametrize('thinking',['disabled','enabled'])
def test_old_cache_is_reparsed_without_rewrite_or_request(tmp_path,monkeypatch,thinking):
    from usersim.judge import input_sha256
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    item = dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    base = dict(id='a',attempt=1,thinking=thinking,input_sha256=input_sha256('prompt',item))
    path = tmp_path/'old.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in [dict(base,event='dispatch'),
        dict(base,event='response',status='parse_error',content='**{"topic":"close","emotion":false}**')])+'\n')
    before = path.read_bytes()
    previous = read_cache(path)
    assert previous[-1]['status']=='ok'
    assert previous[-1]['cached_original_status']=='parse_error'
    assert path.read_bytes()==before
    def forbidden(request):
        raise AssertionError('Cached response must never be requested again')
    judge = SparkJudge(path,thinking,httpx.MockTransport(forbidden))
    try:
        row = judge.evaluate('prompt',item,previous)
        assert row['prediction']=={'topic':'close','emotion':False}
        assert path.read_bytes()==before
    finally:
        judge.close()


def test_verified_process_interruption_uses_remaining_transport_budget(tmp_path,monkeypatch):
    from usersim.judge import input_sha256
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    item = dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    base = dict(id='a',attempt=1,thinking='enabled',input_sha256=input_sha256('prompt',item))
    p = tmp_path/'interrupted.jsonl'
    p.write_text('\n'.join(json.dumps(r) for r in [dict(base,event='dispatch'),
        dict(base,event='response',status='request_error',error_type='InterruptedProcess',
             process_exit_observed=True,usage=None)])+'\n')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='**{"topic":"close","emotion":false}**'))]))
    judge = SparkJudge(p,'enabled',httpx.MockTransport(handler))
    try:
        row = judge.evaluate('prompt',item,read_cache(p))
        assert row['attempt']==2 and row['status']=='ok'
        assert len(calls)==1
        assert [r['attempt'] for r in read_cache(p) if r['event']=='dispatch']==[1,2]
    finally:
        judge.close()


def test_concurrent_batch_records_complete_lines_and_resumes_without_requests(tmp_path,monkeypatch):
    import threading
    from collections import Counter
    from usersim.judge_spark import judge_batch
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    barrier = threading.Barrier(8)
    calls = []
    lock = threading.Lock()
    def handler(request):
        with lock:
            calls.append(request)
        barrier.wait(timeout=10)
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content=' '*20000+'{"topic":"close","emotion":false}'))]))
    items = [dict(id=f'item-{i}',goal='g',history=[],previous_agent='a',text=str(i)) for i in range(32)]
    path = tmp_path/'batch.jsonl'
    result = judge_batch(path,items,8,prompt='prompt',transport=httpx.MockTransport(handler))
    assert set(result)=={item['id'] for item in items}
    assert len(calls)==32
    rows = read_cache(path)
    assert len(rows)==64
    assert Counter(r['event'] for r in rows)=={'dispatch':32,'response':32}
    assert len({(r['id'],r['attempt']) for r in rows if r['event']=='dispatch'})==32
    before = path.read_bytes()
    monkeypatch.delenv('SPARK_API_KEY')
    # Different input IDs and reversed order still reuse fingerprints, with no client.
    renamed = [dict(item,id='alias-'+item['id']) for item in reversed(items)]
    again = judge_batch(path,renamed,8,prompt='prompt')
    assert len(again)==32 and all(r['status']=='ok' for r in again.values())
    assert path.read_bytes()==before


def test_batch_reuses_other_cache_by_fingerprint_and_persists_provenance(tmp_path,monkeypatch):
    from usersim.judge_spark import judge_batch
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    donor = tmp_path/'donor.jsonl'
    item = dict(id='source-a',goal='g',history=[],previous_agent='a',text='t')
    def handler(request):
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='{"topic":"continue","emotion":true}'))]))
    judge_batch(donor,[item],1,prompt='prompt',transport=httpx.MockTransport(handler))
    before = donor.read_bytes()
    monkeypatch.delenv('SPARK_API_KEY')
    destination = tmp_path/'destination.jsonl'
    alias = dict(item,id='source-b',source='SOURCE_SENTINEL')
    result = judge_batch(destination,[alias],8,prompt='prompt',reuse_caches=[donor])
    assert result['source-b']['prediction']=={'topic':'continue','emotion':True}
    rows = read_cache(destination)
    assert len(rows)==1 and rows[0]['event']=='reuse'
    assert rows[0]['reused_from']==str(donor)
    assert donor.read_bytes()==before
    # Imported prediction is reusable on the next invocation without the donor.
    assert judge_batch(destination,[alias],8,prompt='prompt')['source-b']['status']=='ok'


def test_rate_limit_reduces_shared_request_limit_to_four(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading,time
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    monkeypatch.setattr('usersim.judge_spark.time.sleep',lambda delay:None)
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if count==1:return httpx.Response(429,json={'error':{'code':'rate_limit_exceeded'}})
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    judge = SparkJudge(tmp_path/'rate.jsonl','enabled',httpx.MockTransport(handler),concurrency=8)
    try:
        item = dict(id='a',goal='g',history=[],previous_agent='a',text='t')
        assert judge.evaluate('prompt',item)['attempt']==2
        assert judge.concurrency==4 and judge.rate_limit_hits==1
        active = peak = 0
        lock = threading.Lock()
        pause = threading.Event()
        def slow_handler(request):
            nonlocal active,peak
            with lock:
                active += 1
                peak = max(peak,active)
            pause.wait(.01)
            with lock:
                active -= 1
            return httpx.Response(200,json={})
        judge.client.close()
        judge.client = httpx.Client(transport=httpx.MockTransport(slow_handler))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:judge.post({}),range(24)))
        assert peak<=4
    finally:
        judge.close()


def test_batch_resumes_transport_error_under_new_alias(tmp_path,monkeypatch):
    from usersim.judge import input_sha256
    from usersim.judge_spark import judge_batch
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    item = dict(id='new-alias',goal='g',history=[],previous_agent='a',text='t')
    base = dict(id='old-alias',attempt=1,thinking='enabled',model='spark-x2.6',provider='spark',
                input_sha256=input_sha256('prompt',item))
    path = tmp_path/'retry.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in [dict(base,event='dispatch'),
        dict(base,event='response',status='request_error',error_type='ReadTimeout',usage=None)])+'\n')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    result = judge_batch(path,[item],8,prompt='prompt',transport=httpx.MockTransport(handler))
    assert result['new-alias']['attempt']==2
    assert result['new-alias']['id']=='old-alias'
    assert len(calls)==1
    monkeypatch.delenv('SPARK_API_KEY')
    assert judge_batch(path,[item],8,prompt='prompt')['new-alias']['status']=='ok'
    assert len(read_cache(path))==4


class SimulatedClock:
    def __init__(self):
        import threading
        self.value = 0.
        self.lock = threading.Lock()
    def __call__(self):
        with self.lock:return self.value
    def sleep(self, seconds):
        with self.lock:self.value += seconds


def test_429_never_spends_transport_budget_and_other_errors_still_do(tmp_path,monkeypatch):
    from usersim.judge_spark import judge_batch
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    clock = SimulatedClock()
    responses = [429]*7+[503]*3+[200]
    def handler(request):
        status = responses.pop(0)
        if status!=200:return httpx.Response(status,json={'error':{'code':str(status)}})
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    path = tmp_path/'unlimited.jsonl'
    item = dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    result = judge_batch(path,[item],8,prompt='prompt',transport=httpx.MockTransport(handler),
        clock=clock,sleeper=clock.sleep,jitter=lambda base:base)
    row = result['a']
    assert row['attempt']==11 and row['transport_attempt']==4 and row['status']=='ok'
    limited = [r for r in read_cache(path) if r.get('status')=='rate_limited']
    assert len(limited)==7 and all(r['budget_consumed'] is False for r in limited)
    assert [r['rate_limit_backoff_seconds'] for r in limited]==[5,10,20,40,80,160,300]
    assert clock()<3600


def test_adaptive_limit_halves_to_one_and_recovers_after_200_successes(tmp_path,monkeypatch):
    from usersim.judge_spark import judge_batch
    import threading
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    clock = SimulatedClock()
    count = 0
    lock = threading.Lock()
    def handler(request):
        nonlocal count
        with lock:
            count += 1
            limited = count<=3
        if limited:return httpx.Response(429,json={'error':{}})
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    path = tmp_path/'adaptive.jsonl'
    items = [dict(id=str(i),goal='g',history=[],previous_agent='a',text=str(i)) for i in range(200)]
    # Establish the three reductions in response order, then resume the same
    # persisted controller through the public batch interface for success #2..200.
    first = SparkJudge(path,'enabled',httpx.MockTransport(handler),concurrency=8,
        clock=clock,sleeper=clock.sleep,jitter=lambda base:base)
    try:
        assert first.evaluate('prompt',items[0])['status']=='ok'
    finally:
        first.close()
    result = judge_batch(path,items,8,prompt='prompt',transport=httpx.MockTransport(handler),
        clock=clock,sleeper=clock.sleep,jitter=lambda base:base)
    assert len(result)==200
    limited = [r for r in read_cache(path) if r.get('status')=='rate_limited']
    assert sorted(r['current_concurrency'] for r in limited)==[1,2,4]
    runtime = json.loads(Path(str(path)+'.runtime.json').read_text())
    assert runtime['concurrency']==2 and runtime['batch_complete'] is True


def test_no_success_for_60_minutes_stops_with_simulated_clock(tmp_path,monkeypatch):
    from usersim.judge_spark import judge_batch,JudgeStopped
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    clock = SimulatedClock()
    path = tmp_path/'stalled.jsonl'
    item = dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    def handler(request):return httpx.Response(429,json={'error':{}})
    with pytest.raises(JudgeStopped,match='60 minutes'):
        judge_batch(path,[item],8,prompt='prompt',transport=httpx.MockTransport(handler),
            clock=clock,sleeper=clock.sleep,jitter=lambda base:base)
    assert clock()==3600
    rows = [r for r in read_cache(path) if r['event']=='response']
    assert len(rows)>4 and all(r['status']=='rate_limited' for r in rows)
    assert json.loads(Path(str(path)+'.stop.json').read_text())['reason']=='no_success_60_minutes'


def test_historical_exhausted_429_cache_is_exempt_without_rewriting(tmp_path,monkeypatch):
    from usersim.judge import input_sha256
    from usersim.judge_spark import judge_batch
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    path = tmp_path/'legacy429.jsonl'
    item = dict(id='new-id',goal='g',history=[],previous_agent='a',text='t')
    history=[]
    for attempt in range(1,5):
        base=dict(id='old-id',attempt=attempt,thinking='enabled',model='spark-x2.6',provider='spark',
                  input_sha256=input_sha256('prompt',item))
        history += [dict(base,event='dispatch'),dict(base,event='response',status='request_error',
                    http_status=429,error_type='HTTPStatusError',usage=None)]
    path.write_text(''.join(json.dumps(r)+'\n' for r in history))
    before=path.read_bytes()
    def handler(request):
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    row=judge_batch(path,[item],8,prompt='prompt',transport=httpx.MockTransport(handler))['new-id']
    assert row['attempt']==5 and row['transport_attempt']==1 and row['status']=='ok'
    assert path.read_bytes().startswith(before)


def test_exclusive_batch_recovers_dispatch_after_old_writer_exit(tmp_path,monkeypatch):
    from usersim.judge import input_sha256
    from usersim.judge_spark import judge_batch
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    item=dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    path=tmp_path/'orphan.jsonl'
    path.write_text(json.dumps(dict(id='a',attempt=1,event='dispatch',thinking='enabled',
        model='spark-x2.6',provider='spark',input_sha256=input_sha256('prompt',item)))+'\n')
    def handler(request):
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),
            choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    row=judge_batch(path,[item],8,prompt='prompt',transport=httpx.MockTransport(handler))['a']
    assert row['attempt']==2 and row['transport_attempt']==2
    assert read_cache(path)[1]['error_type']=='InterruptedProcess'


def test_supervisor_restarts_unexpected_exit_then_runs_acceptance(tmp_path,monkeypatch):
    from scripts import validate_spark_judge as validation
    monkeypatch.setattr(validation,'OUT',tmp_path)
    monkeypatch.setattr(validation,'safety',lambda **kwargs:None)
    monkeypatch.setattr('time.sleep',lambda _:None)
    calls=[]
    class Worker:
        pid=99999999
        def __init__(self, command, **kwargs):calls.append(command)
        def wait(self):
            if len(calls)==1:return -9
            (tmp_path/'summary.json').write_text(json.dumps({'status':'complete'}))
            return 0
    monkeypatch.setattr('subprocess.Popen',Worker)
    checked=[]
    def verify():checked.append(True);return {'passed':True}
    monkeypatch.setattr(validation,'verify_full_delivery',verify)
    assert validation.supervise_full(8)==0
    assert len(calls)==2 and len(checked)==1
    assert all('--phase' in command and 'full' in command for command in calls)
    assert json.loads((tmp_path/'supervisor_state.json').read_text())['stage']=='complete'


def test_supervisor_does_not_restart_terminal_stop(tmp_path,monkeypatch):
    from scripts import validate_spark_judge as validation
    monkeypatch.setattr(validation,'OUT',tmp_path)
    Path(str(tmp_path/'full.jsonl')+'.stop.json').write_text(json.dumps(
        {'reason':'no_success_60_minutes','message':'No success for 60 minutes'}))
    def forbidden(*a,**kw):raise AssertionError('Stop condition must not restart a worker')
    monkeypatch.setattr('subprocess.Popen',forbidden)
    assert validation.supervise_full(8)==3
    assert json.loads((tmp_path/'supervisor_state.json').read_text())['stage']=='stopped'


def test_no_success_watchdog_is_not_reset_by_process_restart(tmp_path,monkeypatch):
    import time
    from usersim.judge_spark import judge_batch,JudgeStopped,RETRY_POLICY
    path=tmp_path/'restart-stall.jsonl'
    Path(str(path)+'.runtime.json').write_text(json.dumps(dict(policy=RETRY_POLICY,
        concurrency=1,success_streak=0,last_success_wall_time=time.time()-3601,batch_complete=False)))
    monkeypatch.delenv('SPARK_API_KEY',raising=False)
    item=dict(id='a',goal='g',history=[],previous_agent='a',text='t')
    with pytest.raises(JudgeStopped,match='60 minutes'):
        judge_batch(path,[item],8,prompt='prompt',clock=SimulatedClock())
    assert not path.exists()  # Stop occurred before credentials/client/dispatch.
