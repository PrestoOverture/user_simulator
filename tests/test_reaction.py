import json
import httpx
from scripts.measure_reaction import sample_manifest, judge_item, summarize, decision
from usersim.csds import load
from usersim.judge import Judge, messages
from scripts.validate_judge import cached


def test_full_sampling_reproducible_disjoint():
    rows = load()
    a = sample_manifest(rows)
    assert a == sample_manifest(list(reversed(rows)))
    groups = [{(r['dialogue_id'],r['turn']) for r in v} for v in a['groups'].values()]
    assert all(len(g)==1500 for g in groups)
    assert groups[0].isdisjoint(groups[1])


def test_blind_prefix_and_mock_resume(monkeypatch, tmp_path):
    row = dict(DialogueID=999999, UserSumm=['询问退款'], Dialogue=[
        dict(speaker='A',utterance='前文'+str(i)) for i in range(8)] + [
        dict(speaker='Q',utterance='真 人'),dict(speaker='A',utterance='未来秘密')])
    item = dict(id='human:999999:1', source='human', **judge_item(row,1))
    view = json.loads(messages('prompt',item)[1]['content'])
    assert set(view)=={'goal','history','previous_agent','text'}
    assert len(view['history'])==6
    assert view['history'][0]['text']=='前文2'
    assert view['text']=='真人'
    assert '未来秘密' not in json.dumps(view,ensure_ascii=False)
    monkeypatch.setenv('SANDBOX_API_KEY','mock-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://mock.invalid')
    calls=[]
    def handle(req):
        calls.append(json.loads(req.content))
        assert calls[-1]['messages'][1]['content']==messages('prompt',item)[1]['content']
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))],usage=dict(prompt_tokens=3,completion_tokens=2,total_tokens=5)))
    path=tmp_path/'cache.jsonl'
    client=Judge(path,transport=httpx.MockTransport(handle))
    try:
        client.evaluate('prompt',item)
        client.evaluate('prompt',item,cached(path))
    finally:
        client.close()
    assert len(calls)==1


def test_interaction_and_preregistered_rule():
    pairs=[]
    for did in range(5):
        for group in ('negative','nonnegative'):
            pairs.append(dict(dialogue_id=did,group=group,human=dict(topic='close',emotion=False),
                simulator=dict(topic='continue' if group=='negative' else 'close',emotion=True)))
    result=summarize(pairs)
    assert result['continue']['interaction']==1
    assert result['continue']['interval']==[1,1]
    assert result['emotion']['interaction']==0
    assert result['decision']=='限定在被拒之后'
    assert decision(.2,.1,[-.1,.3])=='用普遍定义'
    assert decision(.1,.2,[-.2,-.01])=='用普遍定义'
    assert decision(-.5,-.1,[-.6,-.2])=='限定在被拒之后'
    assert decision(.5,.1,[0,.8])=='用普遍定义'


def test_report_only_never_constructs_judge(monkeypatch, tmp_path):
    import hashlib
    import scripts.measure_reaction as m
    root=tmp_path
    (root/'config').mkdir()
    (root/'config/judge_prompt.md').write_text('frozen')
    validation=root/'outputs/judge_validation'
    validation.mkdir(parents=True)
    (validation/'final_prompt.json').write_text(json.dumps(dict(sha256=hashlib.sha256(b'frozen').hexdigest())))
    sim=root/'sim'
    sim.mkdir()
    (sim/'generations.jsonl').write_text('')
    row=dict(DialogueID=1,QRole='用户',UserSumm=['咨询'],Dialogue=[dict(speaker='A',utterance='不能'),dict(speaker='Q',utterance='谢谢')])
    monkeypatch.setattr(m,'ROOT',root)
    monkeypatch.setattr(m,'load',lambda:[row])
    monkeypatch.setattr(m,'sample_manifest',lambda rows:dict(groups=dict(negative=[dict(dialogue_id=1,turn=1)],nonnegative=[])))
    def forbidden(*args,**kwargs):
        raise AssertionError('report-only attempted API client')
    monkeypatch.setattr(m,'Judge',forbidden)
    out=root/'report'
    monkeypatch.setattr('sys.argv',['measure_reaction','--sim-dir',str(sim),'--out',str(out),'--report-only'])
    m.main()
    assert (out/'report.md').exists()


def test_measurement_cache_resume_504(monkeypatch,tmp_path):
    import pytest
    from scripts.measure_reaction import cached, predictions
    monkeypatch.setenv('SANDBOX_API_KEY','mock-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://mock.invalid')
    path=tmp_path/'judgments.jsonl'
    rid='simulator:7918:4'
    rows=[dict(id=rid,attempt=1,event='dispatch'),dict(id=rid,attempt=1,event='response',status='request_error',error_type='HTTPStatusError',http_status=504,usage=None)]
    original=''.join(json.dumps(r)+'\n' for r in rows)
    path.write_text(original)
    with pytest.raises(RuntimeError,match='Request error in cache'):
        cached(path)
    previous=cached(path,allow_request_errors=True)
    assert rid not in predictions(previous)
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))],usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2)))
    client=Judge(path,transport=httpx.MockTransport(handle))
    try:
        client.evaluate('fixed',dict(id=rid,goal='',history=[],previous_agent='',text=''),previous)
    finally:
        client.close()
    assert len(calls)==1
    assert path.read_text().startswith(original)
    updated=cached(path,allow_request_errors=True)
    assert updated[-1]['attempt']==2 and updated[-1]['status']=='ok'
    assert predictions(updated)[rid]==dict(topic='close',emotion=False)
    failed=[dict(id='x',event='response',attempt=i,status='request_error') for i in (1,2)]
    assert 'x' not in predictions(failed)
    assert predictions(failed+[dict(id='x',event='response',attempt=3,status='request_error')])=={'x':None}


def test_plan_only_is_read_only(monkeypatch,tmp_path,capsys):
    import scripts.measure_reaction as m
    monkeypatch.setattr(m,'ROOT',tmp_path)
    (tmp_path/'config').mkdir()
    (tmp_path/'config/judge_prompt.md').write_text('fixed')
    import hashlib
    validation=tmp_path/'outputs/judge_validation'
    validation.mkdir(parents=True)
    (validation/'final_prompt.json').write_text(json.dumps(dict(sha256=hashlib.sha256(b'fixed').hexdigest())))
    sim=tmp_path/'sim'
    sim.mkdir()
    (sim/'generations.jsonl').write_text('')
    monkeypatch.setattr(m,'load',lambda:[])
    monkeypatch.setattr(m,'sample_manifest',lambda rows:dict(groups=dict(negative=[],nonnegative=[])))
    def forbidden(*args,**kwargs):
        raise AssertionError('plan-only constructed client')
    monkeypatch.setattr(m,'Judge',forbidden)
    before={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    monkeypatch.setattr('sys.argv',['measure_reaction','--sim-dir',str(sim),'--out',str(tmp_path/'absent'),'--plan-only'])
    m.main()
    assert json.loads(capsys.readouterr().out)['retry_ids']==[]
    assert not (tmp_path/'absent').exists()
    assert before=={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_snapshot_supersedes_legacy_and_preserves_new_inputs(monkeypatch,tmp_path):
    import scripts.measure_reaction as m
    from usersim.judge import input_sha256
    human=dict(id='human:1:1',goal='',history=[],previous_agent='',text='human')
    sim=dict(human,id='simulator:1:1',text='sim')
    path=tmp_path/'judgments.jsonl'
    rows=[]
    for item in (human,sim):
        rows += [dict(id=item['id'],attempt=1,event='dispatch'),dict(id=item['id'],attempt=1,event='response',status='ok',prediction=dict(topic='close',emotion=False))]
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    original=path.read_bytes()
    monkeypatch.setattr(m,'dependency_evidence',lambda out:dict(proven=True))
    check=m.accept_snapshot(tmp_path,[human,sim],'prompt','old','new')
    assert check['accepted_human']==1 and check['superseded_simulator']==1
    assert m.active_responses(tmp_path,[r for r in rows if r['event']=='response'])[0]['id']==human['id']
    assert path.read_bytes()==original
    with path.open('a') as f:
        f.write(json.dumps(dict(id=sim['id'],attempt=2,event='dispatch',input_sha256=input_sha256('prompt',sim)))+'\n')
    check=m.accept_snapshot(tmp_path,[human,sim],'prompt','new','new')
    assert check['accepted'] and len(check['superseded'])==1
    check=m.accept_snapshot(tmp_path,[human,dict(sim,text='changed')],'prompt','new','changed')
    assert not check['accepted'] and check['mismatch_ids']==[sim['id']]
    assert (tmp_path/'input_sha256.txt').read_text()=='new\n'
    monkeypatch.setattr(m,'dependency_evidence',lambda out:dict(proven=False))
    check=m.accept_snapshot(tmp_path,[human,sim],'prompt','new','new')
    assert check['superseded_human']==1 and check['accepted_human']==0


def test_dependency_proof_requires_complete_raw_hashes(monkeypatch,tmp_path):
    import hashlib
    import scripts.measure_reaction as m
    monkeypatch.setattr(m,'ROOT',tmp_path)
    raw=tmp_path/'data/raw/csds'
    raw.mkdir(parents=True)
    (raw/'data.json').write_text('[]')
    names=['config/summary_overrides.json','config/summary_reviews.json','usersim/csds.py','usersim/replay.py']
    for name in names:
        p=tmp_path/name
        p.parent.mkdir(exist_ok=True)
        p.write_text('content')
    hashes={str(p.relative_to(tmp_path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    out=tmp_path/'out'
    out.mkdir()
    (out/'run.json').write_text(json.dumps(dict(dependency_sha256=hashes)))
    assert m.dependency_evidence(out)['proven']
    (raw/'data.json').write_text('[1]')
    assert not m.dependency_evidence(out)['proven']


def test_summary_audit_rule_only_keeps_reviewed_strict():
    import pytest
    from scripts.check_outputs import check_summary_audits
    from usersim.csds import load, summary_audit
    rows={r['DialogueID']:r for r in load()}
    row=rows[8383]
    audit=summary_audit(row,allow_unreviewed=True)
    assert audit['reviewed'] is False
    check_summary_audits([audit],[8383],rows)
    with pytest.raises((KeyError,AssertionError)):
        check_summary_audits([dict(audit,reviewed=True)],[8383],rows)
    with pytest.raises(AssertionError):
        check_summary_audits([dict(audit,goal='tampered')],[8383],rows)


def test_trust_evidence_requires_run_time_and_clean_dependencies(monkeypatch,tmp_path):
    import hashlib
    import os
    from types import SimpleNamespace
    import scripts.measure_reaction as m
    monkeypatch.setattr(m,'ROOT',tmp_path)
    raw=tmp_path/'data/raw/csds/CSDS'
    raw.mkdir(parents=True)
    hashes={}
    for name in ('train.json','val.json','test.json'):
        p=raw/name
        p.write_text('[]')
        os.utime(p,(1630056603,1630056603))
        hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    monkeypatch.setattr(m,'RAW_HASHES',hashes)
    dirty=False
    def git(args,**kwargs):
        return SimpleNamespace(returncode=0,stdout=(' M usersim/csds.py\n' if dirty else '') if args[1]=='status' else m.HUMAN_COMMIT+' 1792945907\n')
    monkeypatch.setattr(m.subprocess,'run',git)
    out=tmp_path/'out'
    out.mkdir()
    rows=[dict(id='human:1:1',event='dispatch',attempt=1)]
    evidence=m.trust_human_evidence(out,rows)
    assert not evidence['accepted'] and 'no start timestamp' in evidence['failures'][0]
    (out/'run.json').write_text(json.dumps(dict(started_at='2026-10-26T00:52:00+08:00')))
    evidence=m.trust_human_evidence(out,rows)
    assert evidence['accepted'] and evidence['decision_by']=='Claude'
    dirty=True
    assert not m.trust_human_evidence(out,rows)['accepted']
    dirty=False
    (out/'run.json').write_text(json.dumps(dict(started_at='2026-10-26T00:00:00+08:00')))
    assert not m.trust_human_evidence(out,rows)['accepted']
    (raw/'train.json').write_text('[1]')
    assert any('hash or mtime mismatch' in reason for reason in m.trust_human_evidence(out,rows)['failures'])


def test_trust_restores_only_humans_after_all_checks(monkeypatch,tmp_path):
    import scripts.measure_reaction as m
    items=[dict(id=s+':1:1',goal='',history=[],previous_agent='',text=s) for s in ('human','simulator')]
    rows=[]
    for item in items:
        rows.extend([dict(id=item['id'],attempt=1,event='dispatch'),dict(id=item['id'],attempt=1,event='response',status='ok',prediction=dict(topic='close',emotion=False))])
    path=tmp_path/'judgments.jsonl'
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    original=path.read_bytes()
    monkeypatch.setattr(m,'dependency_evidence',lambda out:dict(proven=False))
    m.accept_snapshot(tmp_path,items,'fixed','old','new')
    assert len(m.superseded_keys(tmp_path))==2
    monkeypatch.setattr(m,'trust_human_evidence',lambda out,rows:dict(accepted=False,decision_by='Claude',failures=['missing time']))
    check=m.accept_snapshot(tmp_path,items,'fixed','new','new',trust_human_inputs=True)
    assert check['accepted_human']==0 and len(m.superseded_keys(tmp_path))==2
    monkeypatch.setattr(m,'trust_human_evidence',lambda out,rows:dict(accepted=True,decision_by='Claude',failures=[]))
    check=m.accept_snapshot(tmp_path,items,'fixed','new','new',trust_human_inputs=True)
    assert check['accepted_human']==1 and check['superseded_simulator']==1
    assert m.superseded_keys(tmp_path)=={('simulator:1:1',1)}
    assert path.read_bytes()==original


def test_concurrent_cache_no_duplicates_or_loss(monkeypatch,tmp_path):
    from collections import Counter
    from threading import Barrier, Lock
    from scripts.measure_reaction import evaluate_pending, cached
    monkeypatch.setenv('SANDBOX_API_KEY','mock-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://mock.invalid')
    barrier=Barrier(4)
    lock=Lock()
    calls=[]
    def handle(request):
        item=json.loads(json.loads(request.content)['messages'][1]['content'])
        barrier.wait(timeout=5)
        with lock:
            calls.append(item['text'])
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content='{"topic":"close","emotion":false}'))],usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2)))
    path=tmp_path/'judgments.jsonl'
    client=Judge(path,transport=httpx.MockTransport(handle))
    items=[dict(id=str(i),goal='',history=[],previous_agent='',text=str(i)) for i in range(20)]
    try:
        evaluate_pending(client,'fixed',items,[],[],set(),4)
    finally:
        client.close()
    rows=[json.loads(s) for s in path.read_text().splitlines()]
    assert Counter(r['id'] for r in rows if r['event']=='dispatch')==Counter(str(i) for i in range(20))
    assert len(cached(path))==20
    assert Counter(calls)==Counter(str(i) for i in range(20))
    assert all('input_sha256' in r for r in rows if r['event']=='dispatch')


def test_concurrent_fatal_error_stops_submissions(monkeypatch,tmp_path):
    import pytest
    from threading import Barrier, Lock
    from scripts.measure_reaction import evaluate_pending
    monkeypatch.setenv('SANDBOX_API_KEY','mock-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://mock.invalid')
    barrier=Barrier(2)
    calls=[]
    lock=Lock()
    def handle(request):
        with lock:
            calls.append(request)
        barrier.wait(timeout=5)
        return httpx.Response(401)
    client=Judge(tmp_path/'cache.jsonl',transport=httpx.MockTransport(handle))
    items=[dict(id=str(i),goal='',history=[],previous_agent='',text=str(i)) for i in range(10)]
    try:
        with pytest.raises(RuntimeError):
            evaluate_pending(client,'fixed',items,[],[],set(),2)
    finally:
        client.close()
    assert len(calls)==2
