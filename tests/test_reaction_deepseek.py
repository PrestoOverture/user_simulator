import asyncio
from copy import deepcopy
import json
import gzip

import httpx
import pytest

from scripts import measure_reaction_deepseek as b
from usersim.spark import Spark


def test_full_manifest_and_identical_spark_prompts():
    manifest, plans, reuse, _, _ = b.prepare()
    b.validate_manifest(manifest, b.read(b.FULL/'run.json')['manifest'])
    assert len(plans) == 3000
    assert len(reuse) == 5999
    baseline = {r['request_id']:r for r in b.lines(b.ROOT/'outputs/full_csds/spark/generations.jsonl')}
    for p in plans:
        assert b.sha(p['messages']) == baseline[p['request_id']]['prompt_sha256']
    wrong = deepcopy(manifest)
    wrong['groups']['negative'][0]['turn'] += 1
    with pytest.raises(RuntimeError, match='differs'):
        b.validate_manifest(wrong,manifest)


def test_spark_prompt_mismatch_stops(monkeypatch):
    original = b.prompt
    monkeypatch.setattr(b,'prompt',lambda *a: original(*a)+[dict(role='user',content='changed')])
    with pytest.raises(RuntimeError,match='Spark prompt mismatch'):
        b.prepare()


def test_actual_deepseek_payload_thinking_disabled(tmp_path,monkeypatch):
    monkeypatch.setenv('SANDBOX_API_KEY','test-only')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://test.invalid')
    monkeypatch.setattr(b,'OUT',tmp_path)
    class Guard:
        lock = __import__('threading').Lock()
        stopped = False
        def check(self,model):
            assert model == b.MODEL
    def respond(request):
        payload = json.loads(request.content)
        assert payload['model'] == b.MODEL
        assert payload['thinking'] == {'type':'disabled'}
        assert payload['reasoning_effort'] == 'none'
        assert payload['messages'] == [{'role':'user','content':'原文'}]
        assert 'temperature' not in payload
        body = dict(usage=dict(prompt_tokens=1,completion_tokens=1,reasoning_tokens=0),choices=[dict(message=dict(content='好'))])
        return httpx.Response(200,headers={'content-encoding':'gzip'},content=gzip.compress(json.dumps(body).encode()))
    async def run():
        transport = b.AsyncPaidTransport(Guard())
        transport.inner.inner.close()
        transport.inner.inner = httpx.MockTransport(respond)
        client = Spark(tmp_path,provider='sandbox',model=b.MODEL,transport=transport)
        try:
            assert await client.generate([{'role':'user','content':'原文'}],'1:1') == '好'
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('usage,message,accepted',[
    ({'completion_tokens_details':{'reasoning_tokens':0}}, {'content':'好'}, True),
    ({}, {'content':'好'}, True),
    ({}, {'content':'好','reasoning_content':''}, True),
    ({'completion_tokens_details':{'reasoning_tokens':85}}, {'content':'好'}, False),
    ({}, {'content':'好','reasoning_content':'思考'}, False),
    ({'completion_tokens_details':{'reasoning_tokens':0}}, {'content':'好','reasoning_content':'思考'}, False),
    ({'completion_tokens_details':{}}, {'content':'好'}, False),
])
def test_transport_zero_reasoning_rules(usage,message,accepted,tmp_path,monkeypatch):
    monkeypatch.setattr(b,'OUT',tmp_path)
    class Guard:
        lock = __import__('threading').Lock()
        stopped = False
        def check(self, model):
            assert model == b.MODEL
    guard = Guard()
    transport = b.PaidTransport(guard)
    transport.inner.close()
    transport.inner = httpx.MockTransport(lambda r:httpx.Response(200,json=dict(usage=usage,choices=[dict(message=message)])))
    request = httpx.Request('POST','https://test.invalid',json={'model':b.MODEL})
    if accepted:
        assert transport.handle_request(request).status_code == 200
        assert not guard.stopped
    else:
        with pytest.raises(RuntimeError,match='zero-reasoning'):
            transport.handle_request(request)
        assert guard.stopped
    diagnostic = b.lines(tmp_path/'response_diagnostics.jsonl')[0]
    assert diagnostic['usage'] == usage
    assert diagnostic['reasoning_content_present'] == ('reasoning_content' in message)
    assert diagnostic['reasoning_content_nonempty'] == bool(message.get('reasoning_content'))


@pytest.mark.parametrize('failures',[2,5])
def test_billing_transient_failure_retries_then_paid_request(tmp_path,monkeypatch,failures):
    monkeypatch.setattr(b,'OUT',tmp_path)
    monkeypatch.setenv('SANDBOX_BASE_URL','https://test.invalid')
    monkeypatch.setenv('SANDBOX_API_KEY','test-only')
    calls, sleeps, paid = [], [], []
    def billing(*args,**kwargs):
        calls.append(1)
        if len(calls) <= failures:
            raise httpx.ReadTimeout('transient billing failure')
        return httpx.Response(200,json={'total_usage':2773},request=httpx.Request('GET',args[0]))
    monkeypatch.setattr(b.httpx,'get',billing)
    monkeypatch.setattr(b.time,'sleep',sleeps.append)
    guard=b.Guard()
    transport=b.PaidTransport(guard)
    transport.inner.close()
    def complete(request):
        paid.append(1)
        return httpx.Response(200,json=dict(usage={'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},choices=[dict(message={'content':'好'})]))
    transport.inner=httpx.MockTransport(complete)
    try:
        response=transport.handle_request(httpx.Request('POST','https://test.invalid',json={'model':b.MODEL}))
        assert response.status_code == 200
        assert len(calls) == failures+1
        assert sleeps == [2,4,8,16,30][:failures]
        assert paid == [1]
        assert not guard.stopped
        assert sum(r['event']=='before_request' for r in b.lines(tmp_path/'billing.jsonl')) == 1
    finally:
        transport.close()


def test_billing_persistent_failure_stops_without_paid_request(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'OUT',tmp_path)
    monkeypatch.setenv('SANDBOX_BASE_URL','https://test.invalid')
    monkeypatch.setenv('SANDBOX_API_KEY','test-only')
    calls,sleeps,paid=[],[],[]
    def billing(*args,**kwargs):
        calls.append(1)
        return httpx.Response(503,request=httpx.Request('GET',args[0]))
    monkeypatch.setattr(b.httpx,'get',billing)
    monkeypatch.setattr(b.time,'sleep',sleeps.append)
    guard=b.Guard()
    transport=b.PaidTransport(guard)
    transport.inner.close()
    transport.inner=httpx.MockTransport(lambda r:paid.append(1))
    request=httpx.Request('POST','https://test.invalid',json={'model':b.MODEL})
    try:
        with pytest.raises(RuntimeError,match='after 6 attempts'):
            transport.handle_request(request)
        assert len(calls) == 6
        assert sleeps == [2,4,8,16,30]
        assert guard.stopped
        assert not paid
        assert all(r['event']=='billing_retry_error' for r in b.lines(tmp_path/'billing.jsonl'))
        with pytest.raises(RuntimeError,match='Stopped'):
            transport.handle_request(request)
        assert len(calls) == 6
    finally:
        transport.close()


@pytest.mark.parametrize('text',[
    '{"topic\\n":"close","emotion":false}',
    '{" topic ":"continue","emotion\\t":true}',
])
def test_b_parser_strips_keys(text):
    assert b.parse_b(text)['topic'] in ('close','continue')


@pytest.mark.parametrize('text',[
    '{"topic\\n":"invalid","emotion":false}',
    '{"topic":"close","emotion":"false"}',
    '{"topic":"close"," topic ":"continue","emotion":false}',
    '{"topic":"close","emotion":false,"extra":0}',
])
def test_b_parser_keeps_strict_schema(text):
    with pytest.raises(ValueError):
        b.parse_b(text)


def b_judge_fixture(tmp_path,monkeypatch,handler,prior=200):
    monkeypatch.setenv('SANDBOX_API_KEY','test-only')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://test.invalid')
    monkeypatch.setattr(b,'OUT',tmp_path)
    guard=b.Guard()
    previous=[dict(id=f'old:{i}',attempt=1,event='response',status='ok') for i in range(prior)]
    client=b.BJudge(tmp_path/'judgments.jsonl',guard,previous,transport=httpx.MockTransport(handler))
    item=dict(id='deepseek:1:1',goal='目标',history=[],previous_agent='好的',text='好')
    sleeps=[]
    monkeypatch.setattr(b.time,'sleep',sleeps.append)
    return client,guard,item,sleeps


def judge_ok():
    return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),choices=[dict(message=dict(content='{"topic\\n":"close","emotion":false}'))]))


@pytest.mark.parametrize('failure',['timeout','proxy','connect','502','503','504'])
def test_b_judge_transient_retry_success(tmp_path,monkeypatch,failure):
    calls=[]
    def handler(request):
        calls.append(1)
        if len(calls)==1:
            if failure.isdigit():
                return httpx.Response(int(failure))
            error={'timeout':httpx.ReadTimeout,'proxy':httpx.ProxyError,'connect':httpx.ConnectError}[failure]
            raise error('transient',request=request)
        return judge_ok()
    client,guard,item,sleeps=b_judge_fixture(tmp_path,monkeypatch,handler)
    try:
        assert client.evaluate('frozen',item)==dict(topic='close',emotion=False)
        assert len(calls)==2 and sleeps==[2] and not guard.stopped
        rows=b.cached(tmp_path/'judgments.jsonl',allow_request_errors=True)
        assert [r['status'] for r in rows]==['request_error','ok']
        assert [r['attempt'] for r in rows]==[1,2]
    finally:
        client.close()


def test_b_judge_three_consecutive_errors_stop(tmp_path,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(504)
    client,guard,item,sleeps=b_judge_fixture(tmp_path,monkeypatch,handler)
    try:
        with pytest.raises(RuntimeError,match='3 consecutive'):
            client.evaluate('frozen',item)
        assert len(calls)==3 and sleeps==[2,4] and guard.stopped
        assert len(b.cached(tmp_path/'judgments.jsonl',allow_request_errors=True))==3
    finally:
        client.close()


def test_b_judge_failure_rate_stop(tmp_path,monkeypatch):
    client,guard,item,sleeps=b_judge_fixture(tmp_path,monkeypatch,lambda r:httpx.Response(503),prior=0)
    try:
        with pytest.raises(RuntimeError,match='exceeds 2%'):
            client.evaluate('frozen',item)
        assert guard.stopped and not sleeps
    finally:
        client.close()


def test_b_judge_legacy_failure_keeps_attempt_budget(tmp_path,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls)==1 else judge_ok()
    client,guard,item,sleeps=b_judge_fixture(tmp_path,monkeypatch,handler)
    legacy=dict(id=item['id'],event='response',attempt=1,status='request_error',error_type='RuntimeError',usage=None)
    try:
        assert client.evaluate('frozen',item,[legacy])['topic']=='close'
        rows=b.lines(tmp_path/'judgments.jsonl')
        assert [r['attempt'] for r in rows if r['event']=='dispatch']==[2,3]
        assert len(calls)==2 and sleeps==[4] and not guard.stopped
    finally:
        client.close()


def test_b_judge_invalid_value_stops_and_saves_response(tmp_path,monkeypatch):
    def handler(request):
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),choices=[dict(message=dict(content='{"topic":"invalid","emotion":false}'))]))
    client,guard,item,sleeps=b_judge_fixture(tmp_path,monkeypatch,handler)
    try:
        with pytest.raises(RuntimeError,match='parse/non-retryable'):
            client.evaluate('frozen',item)
        rows=b.cached(tmp_path/'judgments.jsonl',allow_request_errors=True)
        assert len(rows)==1 and rows[0]['status']=='parse_error'
        assert rows[0]['usage']['total_tokens']==2
        assert guard.stopped and not sleeps
    finally:
        client.close()
