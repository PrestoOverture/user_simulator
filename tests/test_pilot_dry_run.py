import argparse
import asyncio
import json

import pytest

from scripts import run_pilot
from usersim.csds import clean_summary, load, summary_audit, user_turns
from usersim.replay import prompt


def unreviewed_row():
    return dict(DialogueID=-1, QRole='用户', UserSumm=['用户要求退款，最终退款成功。'],
                Dialogue=[dict(speaker='Q', utterance='退款'),
                          dict(speaker='A', utterance='请提供订单号'),
                          dict(speaker='Q', utterance='123456')])


def args(out, allow=True, dry=True):
    return argparse.Namespace(out=str(out), n=1, seed=20261027, exclude=None,
                              ids_from=None, provider='spark', model=None,
                              concurrency=1, refresh_only=False, baseline=None,
                              allow_unreviewed_summaries=allow, dry_run=dry)


def test_unreviewed_requires_opt_in():
    row = unreviewed_row()
    with pytest.raises(KeyError):
        summary_audit(row)
    audit = summary_audit(row, allow_unreviewed=True)
    assert audit['reviewed'] is False
    assert audit['goal'] == audit['rule_goal'] == '用户要求退款'
    assert audit['removed'] == ['最终退款成功']
    assert audit['override'] is False
    assert audit['reviewer'] is None


def test_reviewed_and_overridden_summaries_unchanged():
    reviews = json.loads((run_pilot.Path('config/summary_reviews.json')).read_text())
    overrides = json.loads((run_pilot.Path('config/summary_overrides.json')).read_text())
    rows = {str(r['DialogueID']): r for r in load()}
    for identifier in reviews:
        row = rows[identifier]
        assert summary_audit(row, True) == summary_audit(row)
        assert summary_audit(row, True)['reviewed'] is True
    for identifier, goal in overrides.items():
        assert clean_summary(rows[identifier]['UserSumm'], int(identifier))[0] == goal
    row = dict(rows[next(iter(reviews))], UserSumm=['变更后的摘要'])
    with pytest.raises(ValueError, match='does not match source'):
        summary_audit(row, True)


@pytest.mark.parametrize('allow', [True, False])
def test_dry_run_never_constructs_client(tmp_path, monkeypatch, capsys, allow):
    row = unreviewed_row()
    monkeypatch.setattr(run_pilot, 'load', lambda: [row])
    def forbidden(*a, **kw):
        pytest.fail('dry run must not construct an API client')
    monkeypatch.setattr(run_pilot, 'Spark', forbidden)
    if not allow:
        with pytest.raises(KeyError):
            asyncio.run(run_pilot.main(args(tmp_path, allow=False)))
        return
    asyncio.run(run_pilot.main(args(tmp_path)))
    usage = json.loads((tmp_path/'token_usage.json').read_text())
    assert usage['dialogues'] == 1
    assert usage['user_turns'] == 2
    assert usage['unreviewed_summaries'] == 1
    assert usage['request_count'] == 0
    assert json.loads(capsys.readouterr().out) == usage
    assert '未核对摘要段数：1' in (tmp_path/'report.md').read_text()
    assert json.loads((tmp_path/'summary_audit.json').read_text())[0]['reviewed'] is False
    prompts = [json.loads(line) for line in (tmp_path/'dry_run_prompts.jsonl').read_text().splitlines()]
    assert [r['messages'] for r in prompts] == [prompt(row, t) for t in user_turns(row)]
    assert not (tmp_path/'request_usage.jsonl').exists()
    assert not (tmp_path/'generations.jsonl').exists()


def test_generation_reports_unreviewed_count_without_network(tmp_path, monkeypatch):
    row = unreviewed_row()
    monkeypatch.setattr(run_pilot, 'load', lambda: [row])
    class FakeSpark:
        def __init__(self, *a, **kw):
            pass
        async def generate(self, messages, rid):
            with (tmp_path/'request_usage.jsonl').open('a') as f:
                f.write(json.dumps(dict(request_id=rid, input_tokens=1, output_tokens=1,
                                        status='ok', usage_known=True, elapsed_seconds=0))+'\n')
            return '好的'
        async def close(self):
            pass
    monkeypatch.setattr(run_pilot, 'Spark', FakeSpark)
    asyncio.run(run_pilot.main(args(tmp_path, dry=False)))
    usage = json.loads((tmp_path/'token_usage.json').read_text())
    assert usage['unreviewed_summaries'] == 1
    report = (tmp_path/'report.md').read_text()
    assert '未核对摘要段数：1' in report
    assert '抽中摘要已逐条人工核对' not in report


def test_unreviewed_override_is_preserved(monkeypatch):
    from pathlib import Path
    read_text = Path.read_text
    def configured_read(path, *a, **kw):
        if path.name == 'summary_overrides.json':
            return json.dumps({'-1': '覆盖后的诉求'})
        return read_text(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', configured_read)
    row = unreviewed_row()
    audit = summary_audit(row, True)
    assert audit['reviewed'] is False
    assert audit['override'] is True
    assert audit['goal'] == '覆盖后的诉求'
    assert '覆盖后的诉求' in prompt(row, user_turns(row)[0])[1]['content']
