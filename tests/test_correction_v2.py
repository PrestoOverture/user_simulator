import copy

import pytest

from usersim.correction_v2 import SituationPool, corrected_prompt


def row(did, split='train'):
    return dict(DialogueID=did, split=split, QRole='用户', UserSumm=['询问退款'],
                Dialogue=[dict(speaker='Q',utterance='退款'),dict(speaker='A',utterance='不能退款'),
                          dict(speaker='Q',utterance='那算了')])


def test_future_and_target_response_do_not_change_plan_or_prompt():
    pool=SituationPool([row(i) for i in range(10)],{9})
    target=row(9)
    turn=dict(start=2,messages=['那算了'])
    before=pool.plan(target,2,turn)
    modified=copy.deepcopy(target)
    modified['Dialogue'][2]['utterance']='未来答案绝不能影响选择'
    modified['Dialogue'].append(dict(speaker='A',utterance='未来客服内容'))
    after=pool.plan(modified,2,dict(start=2,messages=['完全不同']))
    assert before==after
    for c in ('situation','structure','combined'):
        assert corrected_prompt(target,turn,before,c)==corrected_prompt(modified,turn,after,c)
        assert '未来' not in str(corrected_prompt(modified,turn,after,c))


def test_pool_excludes_entire_held_out_dialogue_and_non_train():
    pool=SituationPool([row(i) for i in range(10)]+[row(100,'test')],{8,9})
    assert {a['dialogue_id'] for a in pool.items}==set(range(8))
    target=row(9)
    p=pool.plan(target,2,dict(start=2))
    assert all(a['dialogue_id']<8 for a in p['examples'])
    assert p['structure']['dialogue_id']<8
    assert p==pool.plan(target,2,dict(start=2))


def test_structure_sampling_does_not_depend_on_target_length():
    pool=SituationPool([row(i) for i in range(10)],{9})
    a=pool.plan(row(9),2,dict(start=2,messages=['短']))
    b=pool.plan(row(9),2,dict(start=2,messages=['长'*1000]))
    assert a['structure']==b['structure']
    with pytest.raises(ValueError):
        corrected_prompt(row(9),dict(start=2),a,'other')


def test_budget_tracks_delta_and_refuses_dispatch_reserve(tmp_path,monkeypatch):
    from scripts import run_correction_v2 as run
    monkeypatch.setattr(run,'billing',lambda:7.0)
    assert run.meter(tmp_path)['spent_usd']==0
    monkeypatch.setattr(run,'billing',lambda:26.6)
    assert run.meter(tmp_path)['spent_usd']==pytest.approx(19.6)
    with pytest.raises(RuntimeError,match='Budget reserve'):
        run.meter(tmp_path,reserve=.50)
    monkeypatch.setattr(run,'billing',lambda:27.1)
    with pytest.raises(RuntimeError,match='Budget reserve'):
        run.meter(tmp_path)


def test_full_cost_gate_stops_before_generation(tmp_path,monkeypatch):
    from scripts import run_correction_v2 as run
    (tmp_path/'smoke_check.json').write_text('{"usd_per_new_judgment": 0.1}')
    (tmp_path/'reused_judgments.json').write_text('{}')
    monkeypatch.setattr(run,'cached',lambda *a,**k:[])
    monkeypatch.setattr(run,'reusable',lambda *a:{})
    monkeypatch.setattr(run,'meter',lambda *a,**k:dict(spent_usd=19.9,limit_usd=20))
    target=row(9)
    baseline={'9:1':dict(status='ok',text='询问退款'),'9:2':dict(status='ok',text='继续要求退款')}
    with pytest.raises(RuntimeError,match='before full generation'):
        run.full_cost_gate(tmp_path,[target],baseline)
    assert (tmp_path/'preflight_cost_projection.json').exists()


def test_raised_budget_uses_recorded_limit(tmp_path,monkeypatch):
    from scripts import run_correction_v2 as run
    import json
    (tmp_path/'budget.json').write_text(json.dumps(dict(start_total_usd=7,limit_usd=50)))
    monkeypatch.setattr(run,'billing',lambda:40)
    assert run.meter(tmp_path,reserve=.5)['spent_usd']==33
    monkeypatch.setattr(run,'billing',lambda:56.8)
    with pytest.raises(RuntimeError,match='50'):
        run.meter(tmp_path,reserve=.5)


def test_metered_evaluation_caches_all_and_bounds_concurrency(tmp_path,monkeypatch):
    from scripts import run_correction_v2 as run
    import threading
    state=dict(active=0,maximum=0,calls=[])
    lock=threading.Lock()
    class Client:
        def evaluate(self,text,item,previous):
            with lock:
                state['active']+=1
                state['maximum']=max(state['maximum'],state['active'])
                state['calls'].append(item['id'])
            with lock:
                state['active']-=1
            return dict(topic='close',emotion=False)
    checks=[]
    def meter(*args,**kwargs):
        checks.append(kwargs.get('reserve'))
        return dict(spent_usd=1,limit_usd=50)
    monkeypatch.setattr(run,'meter',meter)
    run.evaluate_metered(Client(),'prompt',[dict(id=i) for i in range(20)],[],tmp_path,2,500)
    assert sorted(state['calls'])==list(range(20))
    assert state['maximum']<=2
    assert len(checks)>=2 and all(v==.5 for v in checks)


def test_metered_evaluation_does_not_dispatch_after_budget_failure(tmp_path,monkeypatch):
    from scripts import run_correction_v2 as run
    calls=[]
    class Client:
        def evaluate(self,text,item,previous):
            calls.append(item['id'])
            return dict(topic='close',emotion=False)
    checks=[]
    def meter(*args,**kwargs):
        checks.append(1)
        if len(checks)>1:
            raise RuntimeError('budget stop')
        return dict(spent_usd=1,limit_usd=50)
    monkeypatch.setattr(run,'meter',meter)
    with pytest.raises(RuntimeError,match='budget stop'):
        run.evaluate_metered(Client(),'prompt',[dict(id=i) for i in range(20)],[],tmp_path,1,500)
    assert calls==[0]


def test_empty_opening_preserved_but_empty_eligible_turn_stops(tmp_path,monkeypatch):
    from scripts import run_correction_v2 as run
    import json,hashlib
    messages=[dict(role='user',content='frozen')]
    sha=hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
    monkeypatch.setattr(run,'prompt',lambda *a:messages)
    monkeypatch.setattr(run,'corrected_prompt',lambda *a:messages)
    target=row(9)
    baseline={f'9:{t}':dict(request_id=f'9:{t}',text='好',status='ok',prompt_sha256=sha) for t in (1,2)}
    plans={f'9:{t}':dict(structure=dict(chars=1,messages=1)) for t in (1,2)}
    for c in run.CONDITIONS:
        (tmp_path/c).mkdir()
        records=[dict(r,text='' if c=='structure' and r['request_id']=='9:1' else r['text']) for r in baseline.values()]
        (tmp_path/c/'generations.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    audit=run.audit_generations(tmp_path,[target],plans,baseline,1)
    assert audit['structure']['empty_outside_judge']==['9:1']
    p=tmp_path/'structure/generations.jsonl'
    records=[json.loads(s) for s in p.read_text().splitlines()]
    records[1]['text']=''
    p.write_text(''.join(json.dumps(r)+'\n' for r in records))
    with pytest.raises(RuntimeError,match='judge-eligible'):
        run.audit_generations(tmp_path,[target],plans,baseline,1)
