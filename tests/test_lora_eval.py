import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from scripts import build_lora_eval_inputs as builder
from scripts import judge_lora_eval as judge
from usersim.csds import load, user_turns
from usersim.replay import prompt

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_inputs_and_replay(tmp_path):
    first = builder.build(tmp_path)
    content = (tmp_path/'eval_turns.jsonl').read_bytes()
    assert builder.build(tmp_path) == first
    assert content == (tmp_path/'eval_turns.jsonl').read_bytes()
    records = [json.loads(x) for x in content.splitlines()]
    assert len(records) == 4710
    rows = {r['DialogueID']:r for r in load()}
    ids = json.loads((ROOT/'outputs/correction_decision/inputs.json').read_text())['ids']
    expected = [f'{d}:{t}' for d in ids for t,_ in enumerate(user_turns(rows[d]),1)]
    assert [r['request_id'] for r in records] == expected
    plans = json.loads((ROOT/'outputs/correction_decision/plans.json').read_text())
    assert set(plans) <= set(expected)
    for record in records:
        row = rows[record['dialogue_id']]
        turn = user_turns(row)[record['turn']-1]
        assert record['messages'] == prompt(row,turn)
        changed = dict(row, Dialogue=row['Dialogue'][:turn['start']]+[dict(speaker='Q',utterance='FUTURE_SENTINEL')])
        assert prompt(changed,turn) == record['messages']


def test_no_paid_without_authorization(tmp_path):
    factory = Mock()
    assert judge.paid_stage(tmp_path,[{'id':'base:1:1'}],'prompt',False,factory) == {}
    factory.assert_not_called()
    factory.return_value.client.post.assert_not_called()


def test_mock_report(tmp_path):
    eligible = json.loads((ROOT/'outputs/correction_decision/ppi_eligible.json').read_text())
    for variant in ('base','lora'):
        (tmp_path/f'{variant}.jsonl').write_text(''.join(json.dumps(dict(request_id=r['request_id'],variant=variant,text='好的'))+'\n' for r in eligible))
    judge.run(SimpleNamespace(output=tmp_path,base=tmp_path/'base.jsonl',lora=tmp_path/'lora.jsonl',plan=False,mock=True,run_spark=False,allow_paid=False,max_paid_usd=7))
    report = (tmp_path/'report.md').read_text()
    assert 'P2 baseline' in report and '|ppi|base|' in report and '|ppi|lora|' in report


def test_shards_partition():
    spec=importlib.util.spec_from_file_location('eval_gen',ROOT/'kaggle/eval_gen/gen.py')
    gen=importlib.util.module_from_spec(spec);spec.loader.exec_module(gen)
    records=[dict(request_id=str(i)) for i in range(9)]
    a,b=gen.select(records,'0/2'),gen.select(records,'1/2')
    assert {r['request_id'] for r in a}.isdisjoint(r['request_id'] for r in b)
    assert sorted(a+b,key=lambda r:int(r['request_id'])) == records


def test_resume_complete_and_interrupted_append(tmp_path):
    spec=importlib.util.spec_from_file_location('eval_gen',ROOT/'kaggle/eval_gen/gen.py')
    gen=importlib.util.module_from_spec(spec);spec.loader.exec_module(gen)
    records=[dict(request_id=str(i)) for i in range(9)]
    path=tmp_path/'generations_base.jsonl'
    path.write_text(json.dumps(records[0])+'\n'+json.dumps(records[2])+'\n'+ '{"request_id":')
    done=gen.read_records(path,repair_tail=True)
    pending=gen.remaining(records,{r['request_id'] for r in done},'0/2')
    with path.open('a') as f:
        for r in pending:
            f.write(json.dumps(r)+'\n')
    result=gen.read_records(path)
    assert len(result) == len({r['request_id'] for r in result}) == 5
    assert set(r['request_id'] for r in result) == set(r['request_id'] for r in gen.select(records,'0/2'))
    assert gen.remaining(records,{r['request_id'] for r in result},'0/2') == []


def test_transport_failed_pair_excluded_and_counted_without_paid_request(tmp_path,monkeypatch):
    eligible = json.loads((ROOT/'outputs/correction_decision/ppi_eligible.json').read_text())
    sample = set(json.loads((ROOT/'outputs/correction_decision/preregistration_ppi.json').read_text())['sample_ids'])
    failed = next(r['request_id'] for r in eligible if r['dialogue_id'] in sample)
    for variant in ('base','lora'):
        (tmp_path/f'{variant}.jsonl').write_text(''.join(json.dumps(dict(request_id=r['request_id'],variant=variant,text='好的'))+'\n' for r in eligible))
    gemini={}
    monkeypatch.setattr(judge.prior,'gemini_predictions',lambda:gemini)
    def batch(cache,items,**kwargs):
        label=dict(topic='close',emotion=False)
        gemini.update({judge.input_sha256(kwargs['prompt'],r):label for r in items})
        return {r['id']:dict(status='transport_failed' if r['id']=='lora:'+failed else 'ok',prediction=label) for r in items}
    monkeypatch.setattr(judge,'judge_batch',batch)
    def paid(out,pending,*args,**kwargs):
        assert pending == []
        return {}
    monkeypatch.setattr(judge,'paid_stage',paid)
    judge.run(SimpleNamespace(output=tmp_path,base=tmp_path/'base.jsonl',lora=tmp_path/'lora.jsonl',plan=False,mock=False,run_spark=True,allow_paid=True,max_paid_usd=7))
    summary=json.loads((tmp_path/'summary.json').read_text())
    assert summary['paired_turns']==len(eligible)-1
    assert summary['excluded']['judge_transport_failed']==1
    assert 'judge_transport_failed' in (tmp_path/'report.md').read_text()
