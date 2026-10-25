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
