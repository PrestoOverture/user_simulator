import hashlib
import json
from collections import Counter
from pathlib import Path
import httpx
import pytest
from usersim.judge import Judge, parse, messages
from scripts.build_standard_set import annotation_view
from scripts.validate_judge import metric, cached

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/judge_validation'


@pytest.mark.parametrize('text', ['```json\n{"topic":"close","emotion":false}\n```', '{"topic":"other","emotion":false}', '{"topic":"close","emotion":0}', '{"topic":"close","emotion":false,"extra":1}', '{"topic":"close","topic":"continue","emotion":false}', '[]', 'null'])
def test_strict_schema(text):
    with pytest.raises((ValueError, TypeError)):
        parse(text)


def test_client_model_retry_usage(monkeypatch, tmp_path):
    monkeypatch.setenv('SANDBOX_API_KEY','mock-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://mock.invalid')
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body['model'] == 'gemini-3.8-flash'
        assert str(request.url) == 'https://mock.invalid/v1/chat/completions'
        content = 'invalid' if len(calls)==1 else '{"topic":"close","emotion":false}'
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content=content))], usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15)))
    item = dict(id='x',goal='goal',history=[],previous_agent='客服',text='谢谢',source='hidden',annotation={'topic':'close'})
    path = tmp_path/'cache.jsonl'
    client = Judge(path,transport=httpx.MockTransport(handle))
    try:
        assert client.evaluate('prompt',item) == dict(topic='close',emotion=False)
        rows = cached(path)
        assert [r['status'] for r in rows] == ['parse_error','ok']
        assert sum(r['usage']['total_tokens'] for r in rows)==30
        assert client.evaluate('prompt',item,rows)['topic']=='close'
        assert len(calls)==2
    finally:
        client.close()
    assert 'mock-secret' not in path.read_text()
    assert 'source' not in calls[0]['messages'][1]['content']
    assert 'annotation' not in calls[0]['messages'][1]['content']


def test_parse_error_after_one_retry(monkeypatch,tmp_path):
    monkeypatch.setenv('SANDBOX_API_KEY','mock-secret')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://mock.invalid')
    def handle(request):
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content='invalid'))],usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2)))
    path=tmp_path/'cache.jsonl'
    c=Judge(path,transport=httpx.MockTransport(handle))
    item=dict(id='x',goal='',history=[],previous_agent='',text='')
    try:
        assert c.evaluate('prompt',item) is None
        rows=cached(path)
        assert len(rows)==2
        assert c.evaluate('prompt',item,rows) is None
        assert len(cached(path))==2
    finally:
        c.close()


def test_standard_set_blinding_split_and_freeze():
    items=json.loads((OUT/'standard_set.json').read_text())['items']
    meta=json.loads((OUT/'source_map.json').read_text())['items']
    assert len(items)==300 and len({r['id'] for r in items})==300
    assert Counter(r['split'] for r in items)=={'dev':100,'test':200}
    assert set(r['dialogue_id'] for r in meta if r['split']=='dev').isdisjoint(r['dialogue_id'] for r in meta if r['split']=='test')
    assert set(Counter((r['source'],r['negative']) for r in meta).values())=={50}
    views=json.loads((OUT/'annotation_view.json').read_text())
    assert views==[annotation_view(r) for r in items]
    for r,v in zip(items,views):
        assert set(v)=={'id','goal','history','previous_agent','text'}
        assert 0<len(v['history'])<=6
        assert r['annotation']['reason']
        assert type(r['annotation']['emotion']) is bool
        assert r['annotation']['topic'] in ('close','continue','unclear')
    manifest=json.loads((OUT/'annotation_freeze.json').read_text())
    for name,h in manifest['files'].items():
        assert hashlib.sha256((OUT/name).read_bytes()).hexdigest()==h


def test_metric_known_example_and_parse_error():
    items=[dict(id=str(i),annotation=dict(topic=t)) for i,t in enumerate(['close','close','continue','continue'])]
    pred={'0':dict(topic='close'),'1':dict(topic='continue'),'2':dict(topic='continue'),'3':None}
    m=metric(items,pred,'topic')
    assert m['accuracy']==.5
    assert m['kappa']==pytest.approx(.2)
    assert m['matrix']==[[1,1,0,0],[0,1,0,1],[0,0,0,0]]


def test_report_only_no_client(monkeypatch):
    from scripts import validate_judge as v
    if not (OUT/'test_v1_summary.json').exists() and not list(OUT.glob('test_v*_summary.json')):
        pytest.skip('Live validation not yet complete')
    def forbidden(*a,**kw):
        raise AssertionError('report-only must never call API')
    monkeypatch.setattr(v,'Judge',forbidden)
    monkeypatch.setattr('sys.argv',['validate_judge.py','--report-only'])
    v.main()
    assert '测试集门槛' in (OUT/'report.md').read_text()


def test_standard_set_exact_real_prefix_and_paired_text():
    from usersim.csds import load, user_turns, clean_summary
    from usersim.behaviors import normalize, negative_agent
    raw={r['DialogueID']:r for r in load()}
    items={r['id']:r for r in json.loads((OUT/'standard_set.json').read_text())['items']}
    paths={'spark':ROOT/'outputs/extended/generations.jsonl',
           'deepseek':ROOT/'outputs/model_b/deepseek-v4.1-flash/full500/generations.jsonl'}
    generated={s:{r['request_id']:r for r in map(json.loads,p.read_text().splitlines()) if r['status']=='ok'} for s,p in paths.items()}
    for meta in json.loads((OUT/'source_map.json').read_text())['items']:
        item=items[meta['id']]
        row=raw[meta['dialogue_id']]
        turn=user_turns(row)[meta['turn']-1]
        prefix=row['Dialogue'][:turn['start']]
        assert item['history']==[dict(role='user' if m['speaker']=='Q' else 'assistant',text=normalize(m['utterance'])) for m in prefix[-6:]]
        agent=next(m['utterance'] for m in reversed(prefix) if m['speaker']!='Q')
        assert item['previous_agent']==normalize(agent)
        assert meta['negative']==negative_agent(agent)
        assert item['goal']==clean_summary(row['UserSumm'],row['DialogueID'])[0]
        rid=f"{meta['dialogue_id']}:{meta['turn']}"
        assert all(rid in pool for pool in generated.values())
        expected=normalize('\n'.join(turn['messages'])) if meta['source']=='human' else generated[meta['source']][rid]['text']
        assert item['text']==expected


def test_completed_validation_request_limits():
    final_path=OUT/'final_prompt.json'
    if not final_path.exists():
        pytest.skip('Validation not finalized')
    final=json.loads(final_path.read_text())
    if not (OUT/f"test_{final['version']}_summary.json").exists():
        pytest.skip('Test evaluation still running')
    versions=sorted(OUT.glob('prompt_v*.md'))
    assert 1<=len(versions)<=3
    assert hashlib.sha256((OUT/f"prompt_{final['version']}.md").read_bytes()).hexdigest()==final['sha256']
    for name,n in [(p.stem,100) for p in versions]+[(f"test_{final['version']}",200)]:
        rows=cached(OUT/f'{name}.jsonl')
        counts=Counter(r['id'] for r in rows)
        assert len(counts)==n
        assert all(1<=c<=2 for c in counts.values())
        assert all(r['provider']=='sandbox' and r['model']=='gemini-3.8-flash' for r in rows)
        assert all(set(('prompt_tokens','completion_tokens','total_tokens'))<=r['usage'].keys() for r in rows)
    pilot=json.loads((OUT/'pilot_check.json').read_text())
    assert pilot['parsed']==5 and len(set(pilot['ids']))==5 and pilot['usage_verified']
