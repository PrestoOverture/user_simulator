import copy
import json
from pathlib import Path
import pytest
from usersim.correction_decision import DecisionPool, TARGETS, corrected_prompt, budget_guard
from usersim.csds import load, user_turns
from usersim.replay import prompt
from scripts import run_correction_decision as run


def test_exact_target_wording():
    assert TARGETS == {
        'close':'本轮你决定就此结束这个问题：接受或确认客服的答复（可以致谢），不再在这个问题上追问或提出新要求。',
        'continue':'本轮你决定在这个问题上继续：追问、表达不接受，或提出新的要求。',
        'unclear':'本轮只提供客服需要的信息或直接回答客服的问题，不表态是否接受。',
    }


def test_pool_excludes_all_500_and_20():
    root=Path(__file__).resolve().parents[1]
    ids=json.loads((root/'outputs/extended/sampled_ids.json').read_text())['ids']
    pilot=json.loads((root/'outputs/sampled_ids.json').read_text())['ids']
    excluded=set(ids)|set(pilot)
    assert len(ids)==500 and len(pilot)==20
    items=[dict(dialogue_id=i,turn=1,group=g,topic='continue') for i in excluded for g in ('negative','nonnegative')]
    items += [dict(dialogue_id=-1,turn=1,group=g,topic='close') for g in ('negative','nonnegative')]
    pool=DecisionPool(items,excluded)
    assert len(pool.items)==2
    assert not excluded & {r['dialogue_id'] for r in pool.items}
    assert all(v=={'close':1} for v in pool.counts.values())


@pytest.mark.parametrize('condition', ['decision','oracle'])
@pytest.mark.parametrize('target',list(TARGETS))
def test_prompt_strict_prefix_and_only_label(condition,target):
    row=next(r for r in load() if len(user_turns(r))>=3)
    u=user_turns(row)[1]
    changed=copy.deepcopy(row)
    for m in changed['Dialogue'][u['start']:]: m['utterance']='FUTURE_SECRET_SENTINEL'
    a=corrected_prompt(row,u,target)
    b=corrected_prompt(changed,u,target)
    assert a==b
    base=prompt(row,u)
    assert a[0]==base[0]
    assert a[1]['content']==TARGETS[target]+'\n'+base[1]['content']
    assert 'FUTURE_SECRET_SENTINEL' not in json.dumps(b)


@pytest.mark.parametrize('cents,requests', [(4900,0),(5000,0),(0,10000),(4899,10001)])
def test_budget_stop(cents,requests):
    with pytest.raises(RuntimeError): budget_guard(cents,requests)


def test_budget_boundary():
    budget_guard(4899.99,9999)


def test_parse_failure_stops_before_retry(tmp_path,monkeypatch):
    monkeypatch.setenv('SANDBOX_API_KEY','test-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://example.invalid')
    client=run.MeteredJudge(tmp_path)
    try:
        with pytest.raises(RuntimeError,match='parse failure'):
            client.record(dict(event='response',status='parse_error'))
    finally: client.close()


def test_every_dispatch_checks_budget(tmp_path,monkeypatch):
    monkeypatch.setenv('SANDBOX_API_KEY','test-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://example.invalid')
    calls=[]
    def stop(out):
        calls.append(out)
        raise RuntimeError('budget stop')
    monkeypatch.setattr(run,'meter',stop)
    client=run.MeteredJudge(tmp_path)
    try:
        with pytest.raises(RuntimeError,match='budget stop'): client.record(dict(event='dispatch'))
        assert calls==[tmp_path]
        assert not (tmp_path/'judgments.jsonl').exists()
    finally: client.close()


def test_decision_draw_depends_only_on_prefix():
    row=next(r for r in load() if len(user_turns(r))>=3)
    u=user_turns(row)[1]
    changed=copy.deepcopy(row)
    for m in changed['Dialogue'][u['start']:]: m['utterance']='FUTURE_USER_TEXT'
    pool=DecisionPool([dict(dialogue_id=-1,turn=i,group=g,topic=t)
                       for g in ('negative','nonnegative') for i,t in enumerate(TARGETS)],set())
    assert pool.draw(row,u,2)==pool.draw(changed,u,2)
    assert corrected_prompt(row,u,pool.draw(row,u,2))==corrected_prompt(changed,u,pool.draw(changed,u,2))


def test_full_projection_stops_above_14(tmp_path,monkeypatch):
    run.dump(tmp_path/'smoke_check.json',dict(usd_per_new_judgment=1))
    monkeypatch.setattr(run,'meter',lambda out:dict(spent_usd=0))
    chosen=[dict(DialogueID=i) for i in range(21)]
    plans={str(i):dict(dialogue_id=20) for i in range(10)}
    with pytest.raises(RuntimeError,match=r'exceeds \$14'):
        run.full_cost_gate(tmp_path,chosen,plans)
    assert run.read(tmp_path/'cost_projection.json')['projected_total_usd']>14


def test_resume_projection_does_not_count_paid_inputs_again(tmp_path,monkeypatch):
    from usersim.judge import input_sha256
    run.dump(tmp_path/'smoke_check.json',dict(usd_per_new_judgment=1))
    monkeypatch.setattr(run,'meter',lambda out:dict(spent_usd=1))
    item=dict(goal='goal',history=[],previous_agent='agent',text='cached text')
    monkeypatch.setattr(run,'judge_item',lambda *args:item)
    sha=input_sha256((run.ROOT/'config/judge_prompt.md').read_text(),item)
    run.dump(tmp_path/'reused_judgments.json',{sha:dict(prediction=dict(topic='close',emotion=False))})
    for c in run.CONDITIONS:
        (tmp_path/c).mkdir()
        (tmp_path/c/'generations.jsonl').write_text(json.dumps(dict(request_id='20:1',status='ok',text='cached text'))+'\n')
    run.full_cost_gate(tmp_path,[dict(DialogueID=i) for i in range(21)],{'20:1':dict(dialogue_id=20,turn=1)})
    result=run.read(tmp_path/'cost_projection.json')
    assert result['remaining_upper_bound']==0 and result['projected_total_usd']==1


def test_billing_transient_failure_retries_and_continues(tmp_path,monkeypatch):
    attempts=[]
    sleeps=[]
    def billing():
        attempts.append(1)
        if len(attempts)<3:
            raise RuntimeError('temporary billing error')
        return 30
    monkeypatch.setattr(run.v2,'billing',billing)
    monkeypatch.setattr(run.time,'sleep',sleeps.append)
    result=run.meter(tmp_path)
    assert result['current_total_usd']==30
    assert result['in_flight_reserve_usd']==4
    assert len(attempts)==3 and sleeps==[2,4]
    assert (tmp_path/'budget.json').exists()


def test_billing_persistent_failure_stops_after_six_attempts(tmp_path,monkeypatch):
    attempts=[]
    sleeps=[]
    def billing():
        attempts.append(1)
        raise RuntimeError('billing unavailable')
    monkeypatch.setattr(run.v2,'billing',billing)
    monkeypatch.setattr(run.time,'sleep',sleeps.append)
    with pytest.raises(RuntimeError,match='after 6 attempts'):
        run.meter(tmp_path)
    assert len(attempts)==6 and sleeps==[2,4,8,16,30]
    assert not (tmp_path/'budget.json').exists()


@pytest.mark.parametrize('total',[49,50])
def test_billing_over_cap_stops_without_retry(tmp_path,monkeypatch,total):
    attempts=[]
    monkeypatch.setattr(run.v2,'billing',lambda:attempts.append(1) or total)
    monkeypatch.setattr(run.time,'sleep',lambda _:pytest.fail('Budget stop must not retry'))
    with pytest.raises(RuntimeError,match='4900 cents'):
        run.meter(tmp_path)
    assert len(attempts)==1


def test_billing_reserves_all_in_flight_requests(tmp_path,monkeypatch):
    monkeypatch.setattr(run.v2,'billing',lambda:45)
    with pytest.raises(RuntimeError,match='in-flight reserve'):
        run.meter(tmp_path)
    monkeypatch.setattr(run.v2,'billing',lambda:44.999)
    assert run.meter(tmp_path)['current_total_usd']==44.999


def test_dispatch_continues_after_billing_recovery(tmp_path,monkeypatch):
    monkeypatch.setenv('SANDBOX_API_KEY','test-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://example.invalid')
    readings=iter([RuntimeError('temporary'),30])
    def billing():
        value=next(readings)
        if isinstance(value,Exception): raise value
        return value
    monkeypatch.setattr(run.v2,'billing',billing)
    monkeypatch.setattr(run.time,'sleep',lambda _:None)
    client=run.MeteredJudge(tmp_path)
    try:
        client.record(dict(event='dispatch',id='input-sha',attempt=1))
    finally:
        client.close()
    assert run.lines(tmp_path/'judgments.jsonl')==[dict(event='dispatch',id='input-sha',attempt=1)]


@pytest.mark.parametrize('text', ['{"topic\\n":"close","emotion":false}', '{"topic\n":"close"," emotion ":false}', '{" topic ":"continue","emotion\t":true}'])
def test_ppi_parser_strips_field_whitespace(text):
    from usersim.correction_decision import parse_judgment
    assert parse_judgment(text)['topic'] in TARGETS


@pytest.mark.parametrize('text', ['{"topic":"close ","emotion":false}', '{"topic":"close","emotion":0}', '{"topic":"close","emotion":false,"extra":1}', '{"topic":"close"," topic ":"continue","emotion":false}'])
def test_ppi_parser_keeps_strict_schema_and_values(text):
    from usersim.correction_decision import parse_judgment
    with pytest.raises((ValueError,TypeError)):parse_judgment(text)


def test_ppi_subsample_is_reproducible():
    from usersim.correction_decision import sample_dialogues,SEED
    ids=list(range(500))
    chosen=sample_dialogues(ids)
    assert len(chosen)==len(set(chosen))==100
    assert chosen==sample_dialogues(list(reversed(ids)))==sample_dialogues(ids,seed=SEED)
    assert chosen!=sample_dialogues(ids,seed=SEED+1)


def test_no_gemini_request_outside_registered_sample(tmp_path,monkeypatch):
    from scripts import run_correction_decision_ppi as p
    from usersim.judge import input_sha256
    monkeypatch.setenv('SANDBOX_API_KEY','test-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://example.invalid')
    monkeypatch.setattr(p,'OUT',tmp_path)
    item=dict(id='decision:999:1',goal='g',history=[],previous_agent='a',text='t')
    sha=input_sha256('prompt',item)
    # Even byte-identical content cannot authorize an unregistered dialogue ID.
    client=p.SampleJudge({sha},{'decision:1:1'})
    monkeypatch.setattr(client.client,'post',lambda *a,**k:pytest.fail('Out-of-sample paid request'))
    monkeypatch.setattr(p.a,'meter',lambda *a,**k:pytest.fail('Out-of-sample dispatch'))
    try:
        with pytest.raises(RuntimeError,match='outside preregistered'):client.evaluate_sample('prompt',item,[])
    finally:client.close()
    assert not (tmp_path/'gemini_sample.jsonl').exists()


@pytest.mark.parametrize('always_fail',[False,True])
def test_sample_transient_transport_retry_is_bounded(tmp_path,monkeypatch,always_fail):
    import httpx
    from scripts import run_correction_decision_ppi as p
    from usersim.judge import input_sha256
    monkeypatch.setenv('SANDBOX_API_KEY','test-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://example.invalid')
    monkeypatch.setattr(p,'OUT',tmp_path)
    monkeypatch.setattr(p.a,'meter',lambda *a,**k:None)
    monkeypatch.setattr(p.time,'sleep',lambda _:None)
    calls=[]
    def handler(request):
        calls.append(1)
        if always_fail or len(calls)==1:raise httpx.ProxyError('temporary')
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),choices=[dict(message=dict(content='{"topic\n":"close","emotion":false}'))]))
    item=dict(id='decision:1:1',goal='g',history=[],previous_agent='a',text='t')
    client=p.SampleJudge({input_sha256('p',item)},{item['id']})
    client.client.close();client.client=httpx.Client(transport=httpx.MockTransport(handler))
    try:
        if always_fail:
            with pytest.raises(RuntimeError,match='retry budget exhausted'):client.evaluate_sample('p',item,[])
            assert len(calls)==3
        else:
            assert client.evaluate_sample('p',item,[])==dict(topic='close',emotion=False)
            assert len(calls)==2
    finally:client.close()


def test_ppi_uses_dialogue_ht_correction_with_unequal_clusters():
    from scripts.run_correction_decision_ppi import compute
    close=dict(topic='close',emotion=False);cont=dict(topic='continue',emotion=False)
    pairs=[]
    for did,size in ((1,2),(2,1),(3,1),(4,2)):
        for _ in range(size):
            pairs.append(dict(dialogue_id=did,targets=dict(decision='close',oracle='close'),
                spark=dict(human=close,baseline=cont,decision=cont if did!=4 else close,oracle=close),
                gemini=dict(human=close,baseline=cont,decision=close,oracle=close) if did in (1,3) else {}))
    result=compute(dict(population_ids=[1,2,3,4],sample_ids=[1,3]),pairs)
    assert result['statistics']['ppi']['continue']['decision']['gap']==pytest.approx(-1/3)
    assert result['statistics']['ppi']['continue']['decision']['absolute_gap_reduction']==pytest.approx(2/3)
    assert result['statistics']['spark_only']['continue']['decision']['gap']==pytest.approx(2/3)


def test_retired_full_gemini_entry_cannot_call_models(tmp_path,monkeypatch):
    import sys
    monkeypatch.setattr(run,'OUT',tmp_path)
    monkeypatch.setattr(sys,'argv',['run_correction_decision.py','--phase','full'])
    monkeypatch.setattr(run.v2,'credentials',lambda:pytest.fail('Retired full Gemini must not initialize credentials'))
    with pytest.raises(RuntimeError,match='retired'):run.main()


def test_ppi_c_adoption_pending_does_not_pass(tmp_path,monkeypatch):
    from scripts import run_correction_decision_ppi as p
    monkeypatch.setattr(p,'C',tmp_path)
    monkeypatch.setattr(p,'OUT',tmp_path)
    run.dump(tmp_path/'preregistration.json',dict(preregistration=dict(adoption_n=100)))
    run.dump(tmp_path/'summary.json',dict(preregistration=dict(adoption_n=100),status='incomplete',adoption_passed=None))
    with pytest.raises(RuntimeError,match='no new paid'):p.adoption_gate()


def test_ppi_report_is_offline_and_deterministic(tmp_path,monkeypatch):
    import hashlib
    from scripts import run_correction_decision_ppi as p
    monkeypatch.setattr(p,'OUT',tmp_path)
    close=dict(topic='close',emotion=False);cont=dict(topic='continue',emotion=False)
    pairs=[dict(dialogue_id=d,request_id=f'{d}:1',targets=dict(decision=t,oracle=t),
        spark=dict(human=close,baseline=cont,decision=close,oracle=close),
        gemini=dict(human=close,baseline=cont,decision=close,oracle=close)) for d,t in enumerate(TARGETS)]
    monkeypatch.setattr(p,'paired_data',lambda:(dict(population_ids=list(range(500)),sample_ids=list(range(100))),pairs))
    for name,value in {'ppi_adoption.json':dict(adoption_passed=True),'budget.json':dict(spent_usd=1),
                       'gemini_sample_projection.json':dict(pending_unique=0),'gemini_sample_billing.json':dict(spent_usd=0),
                       'ppi_support.json':dict(moderated=[])}.items():run.dump(tmp_path/name,value)
    p.report()
    paths=[tmp_path/'summary_500.json',tmp_path/'report_500.md']
    before=[hashlib.sha256(x.read_bytes()).hexdigest() for x in paths]
    p.report()
    assert before==[hashlib.sha256(x.read_bytes()).hexdigest() for x in paths]
    text=paths[1].read_text()
    assert all(label in text for label in ['ppi','gemini_only','spark_only','baseline','decision','oracle','不是修正方法','费用'])


def test_single_spark_parse_failure_below_two_percent_continues():
    from scripts import run_correction_decision_ppi as p
    from collections import Counter
    p.enforce_spark_parse_rate(Counter(ok=99,parse_error=1))
    p.enforce_spark_parse_rate(Counter(ok=49,parse_error=1))  # exactly 2% continues


def test_spark_parse_failures_above_two_percent_stop():
    from scripts import run_correction_decision_ppi as p
    from collections import Counter
    from usersim.judge_spark import JudgeStopped
    with pytest.raises(JudgeStopped,match='exceed 2%'):
        p.enforce_spark_parse_rate(Counter(ok=48,parse_error=1))


def test_spark_parse_failure_is_joint_missing_and_never_special_parsed():
    from scripts import run_correction_decision_ppi as p
    from usersim.judge_spark import parse
    leaked='**{"topic":"continue","emotion":false}**</think>{"topic":"continue","emotion":false}'
    with pytest.raises((ValueError,TypeError)):parse(leaked)
    eligible=[dict(request_id=f'{i}:1',dialogue_id=i,turn=1) for i in range(3)]
    records={c+':'+r['request_id']:dict(status='ok') for r in eligible for c in ('human',)+run.SOURCES}
    records['oracle:0:1']=dict(status='parse_error',content=leaked)
    records['baseline:1:1']=dict(status='moderated')
    result=p.build_support(records,eligible)
    assert result==dict(items=[eligible[2]],moderated=[eligible[1]],parse_error=[eligible[0]])
    assert p.registered_inputs(dict(sample_ids=[0,1,2],sample_request_ids=[r['request_id'] for r in eligible]),
        [dict(id=c+':'+r['request_id']) for r in eligible for c in run.CONDITIONS],result['items'])==[dict(id='decision:2:1'),dict(id='oracle:2:1')]


def test_spark_callback_uses_cached_cumulative_rate(tmp_path,monkeypatch):
    from scripts import run_correction_decision_ppi as p
    monkeypatch.setattr(p,'OUT',tmp_path)
    eligible=[dict(request_id='1:1',dialogue_id=1,turn=1)]
    monkeypatch.setattr(p,'prepare',lambda:({},[],eligible,'frozen'))
    monkeypatch.setattr(p,'read_cache',lambda *a,**k:[dict(event='response',status='ok') for _ in range(100)])
    def batch(*args,**kwargs):
        kwargs['on_result'](dict(status='parse_error'))
        return {c+':1:1':dict(status='parse_error' if c=='decision' else 'ok') for c in ('human',)+run.SOURCES}
    monkeypatch.setattr(p,'judge_batch',batch)
    p.spark_stage(4)
    assert run.read(tmp_path/'ppi_support.json')==dict(items=[],moderated=[],parse_error=eligible)


def test_guard_restarts_unexpected_exit_and_then_verifies(tmp_path,monkeypatch):
    from scripts import supervise_correction_decision as guard
    monkeypatch.setattr(guard,'OUT',tmp_path)
    children=[]
    class Child:
        def __init__(self,*args,**kwargs):
            assert kwargs['start_new_session'] is True
            self.pid=100+len(children);children.append(self)
        def wait(self):return -9 if len(children)==1 else 0
    monkeypatch.setattr(guard.subprocess,'Popen',Child)
    monkeypatch.setattr(guard,'worker_alive',lambda pid:False)
    monkeypatch.setattr(guard.time,'sleep',lambda _:None)
    calls=[]
    monkeypatch.setattr(guard,'verify_delivery',lambda out:calls.append(out) or dict(passed=True))
    assert guard.supervise(4)==0 and len(children)==2 and calls==[tmp_path]
    assert run.read(tmp_path/'ppi_supervisor_state.json')['stage']=='complete'


def test_guard_never_restarts_an_explicit_stop(tmp_path,monkeypatch):
    from scripts import supervise_correction_decision as guard
    monkeypatch.setattr(guard,'OUT',tmp_path)
    run.dump(tmp_path/'spark_judgments.jsonl.stop.json',dict(reason='no_success_60_minutes'))
    monkeypatch.setattr(guard.subprocess,'Popen',lambda *a,**k:pytest.fail('Must not restart stop condition'))
    assert guard.supervise(4)==3
    assert run.read(tmp_path/'ppi_supervisor_state.json')['stop']['reason']=='no_success_60_minutes'


def test_guard_restarts_unexpected_python_exception_but_preserves_evidence(tmp_path,monkeypatch):
    from scripts import supervise_correction_decision as guard
    monkeypatch.setattr(guard,'OUT',tmp_path)
    children=[]
    class Child:
        def __init__(self,*args,**kwargs):self.pid=200+len(children);children.append(self)
        def wait(self):
            if len(children)==1:
                (tmp_path/'STOPPED.md').write_text('MemoryError: unexpected allocation failure')
                return 1
            return 0
    monkeypatch.setattr(guard.subprocess,'Popen',Child)
    monkeypatch.setattr(guard,'worker_alive',lambda pid:False)
    monkeypatch.setattr(guard.time,'sleep',lambda _:None)
    monkeypatch.setattr(guard,'verify_delivery',lambda out:dict(passed=True))
    assert guard.supervise(4)==0 and len(children)==2
    assert len(list(tmp_path.glob('guard_unexpected_*.md')))==1


@pytest.mark.parametrize('message',['RuntimeError: Gemini sample parse failure; stop','RuntimeError: Cannot verify gateway billing after 6 attempts; stop'])
def test_guard_preserves_gemini_and_billing_stop_rules(tmp_path,message):
    from scripts import supervise_correction_decision as guard
    (tmp_path/'STOPPED.md').write_text(message)
    assert guard.stop_reason(tmp_path)['reason']=='pipeline_stop'


@pytest.mark.parametrize('complete,authorized',[(False,True),(True,False),(True,True)])
def test_post_hoc_adoption_gate(tmp_path,monkeypatch,complete,authorized):
    from scripts import run_correction_decision_ppi as p
    monkeypatch.setattr(p,'C',tmp_path);monkeypatch.setattr(p,'OUT',tmp_path)
    run.dump(tmp_path/'preregistration.json',dict(preregistration={}))
    run.dump(tmp_path/'summary.json',dict(preregistration={},status='complete' if complete else 'incomplete',adoption_passed=False))
    if authorized:run.dump(tmp_path/'ppi_adoption_authorization.json',dict(decision='adopt_ppi_post_hoc'))
    if complete and authorized:
        p.adoption_gate()
        assert run.read(tmp_path/'ppi_adoption.json')['adoption_passed'] is False
    else:
        with pytest.raises(RuntimeError,match='no new paid'):p.adoption_gate()


@pytest.mark.parametrize('complete,authorized',[(False,True),(True,False)])
def test_sample_stage_gate_prevents_paid_calls(tmp_path,monkeypatch,complete,authorized):
    from scripts import run_correction_decision_ppi as p
    monkeypatch.setattr(p,'C',tmp_path);monkeypatch.setattr(p,'OUT',tmp_path)
    run.dump(tmp_path/'preregistration.json',dict(preregistration={}))
    run.dump(tmp_path/'summary.json',dict(preregistration={},status='complete' if complete else 'incomplete'))
    run.dump(tmp_path/'smoke_check.json',dict(usd_per_new_judgment=.001))
    if authorized:run.dump(tmp_path/'ppi_adoption_authorization.json',dict(decision='adopt_ppi_post_hoc'))
    monkeypatch.setattr(p,'prepare',lambda:({'n':100},[],[],'prompt'))
    monkeypatch.setattr(p,'support_and_spark',lambda *args:([],{}))
    monkeypatch.setattr(p,'registered_inputs',lambda *args:[])
    monkeypatch.setattr(p,'gemini_predictions',lambda:{})
    monkeypatch.setattr(p.a,'meter',lambda *args:pytest.fail('Unauthorized billing/request'))
    monkeypatch.setattr(p,'SampleJudge',lambda *args:pytest.fail('Unauthorized paid client'))
    with pytest.raises(RuntimeError,match='no new paid'):p.gemini_stage(1)


def test_guard_waits_for_live_sample_before_resume(monkeypatch):
    from scripts import supervise_correction_decision as s
    from types import SimpleNamespace
    observations=iter([SimpleNamespace(returncode=0,stdout='python scripts/run_correction_decision.py --phase gemini-sample --concurrency 8'),SimpleNamespace(returncode=1,stdout='')])
    monkeypatch.setattr(s.subprocess,'run',lambda *args,**kwargs:next(observations))
    waits=[]
    monkeypatch.setattr(s.time,'sleep',waits.append)
    s.wait_for_sample(123)
    assert waits==[5]


@pytest.mark.parametrize('already_ok',[False,True])
def test_orphan_sample_recovery_and_monotonic_resend(tmp_path,monkeypatch,already_ok):
    import json,httpx
    from scripts import run_correction_decision_ppi as p
    from usersim.judge import input_sha256
    monkeypatch.setattr(p,'OUT',tmp_path)
    monkeypatch.setattr(p,'active_sample_writers',lambda:[])
    monkeypatch.setattr(p.a,'meter',lambda *args:None)
    monkeypatch.setenv('SANDBOX_API_KEY','test-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://example.invalid')
    item=dict(id='decision:1:1',goal='g',history=[],previous_agent='a',text='t')
    sha=input_sha256('p',item)
    dispatch=dict(event='dispatch',id=sha,attempt=1,input_sha256=sha,model='gemini-3.8-flash',provider='sandbox')
    rows=[dispatch]
    if already_ok:
        rows=[dict(dispatch,attempt=0),dict(event='response',id=sha,attempt=0,status='ok',content='{"topic":"close","emotion":false}',model='gemini-3.8-flash',provider='sandbox',usage={}),dispatch]
    cache=tmp_path/'gemini_sample.jsonl'
    cache.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with p.sample_writer(tmp_path):
        recovered=p.abandon_sample_dispatches(tmp_path)
        assert len(recovered)==1 and recovered[0]['usage'] is None
        assert recovered[0]['status']=='abandoned_in_flight'
        assert p.abandon_sample_dispatches(tmp_path)==[]
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2),choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))]))
    client=p.SampleJudge({sha},{item['id']});client.client.close()
    client.client=httpx.Client(transport=httpx.MockTransport(handler))
    try:assert client.evaluate_sample('p',item,run.lines(cache))==dict(topic='close',emotion=False)
    finally:client.close()
    assert len(calls)==(0 if already_ok else 1)
    if not already_ok:assert run.lines(cache)[-1]['attempt']==2


def test_orphan_sample_with_live_writer_stops_without_mutation(tmp_path,monkeypatch):
    import json
    from scripts import supervise_correction_decision as guard
    row=dict(event='dispatch',id='sha',attempt=1,model='gemini-3.8-flash',provider='sandbox')
    cache=tmp_path/'gemini_sample.jsonl';cache.write_text(json.dumps(row)+'\n')
    before=cache.read_bytes()
    monkeypatch.setattr(guard.p,'active_sample_writers',lambda:[123])
    assert guard.stop_reason(tmp_path)['reason']=='unresolved_gemini_dispatch'
    assert cache.read_bytes()==before
    monkeypatch.setattr(guard.p,'active_sample_writers',lambda:[])
    assert guard.stop_reason(tmp_path) is None
    assert run.lines(cache)[-1]['status']=='abandoned_in_flight'


def test_sample_writer_lock_rejects_second_writer(tmp_path,monkeypatch):
    from scripts import run_correction_decision_ppi as p
    monkeypatch.setattr(p,'active_sample_writers',lambda:[])
    with p.sample_writer(tmp_path):
        with pytest.raises(RuntimeError,match='Active Gemini'):
            with p.sample_writer(tmp_path):pytest.fail('Concurrent writer acquired lock')
