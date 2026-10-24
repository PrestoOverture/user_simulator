import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from check_correction_pilot import common_valid, evaluate


def test_three_condition_intersection_with_different_moderation():
    row = dict(DialogueID=1, Dialogue=[dict(speaker='Q', utterance='你好'),
               dict(speaker='A', utterance='好'), dict(speaker='Q', utterance='你好'),
               dict(speaker='A', utterance='好'), dict(speaker='Q', utterance='你好')])
    conditions = {}
    for name, moderated in [('baseline', {1}), ('C1', {2}), ('C2', set())]:
        conditions[name] = {f'1:{t}': dict(request_id=f'1:{t}', status='moderated' if t in moderated else 'ok', text=None if t in moderated else '你好') for t in (1, 2, 3)}
    common = common_valid(conditions)
    assert common == {'1:3'}
    for gens in conditions.values():
        result = evaluate([row], gens, common)
        assert len(result) == 2
        assert all(r['turn'] == 3 and r['repeated'] == 0 for r in result)
