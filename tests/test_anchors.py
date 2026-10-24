from usersim.anchors import pool, select, anchored_prompt, intent
from usersim.csds import load, user_turns
from usersim.replay import prompt, unsegment
import json
from pathlib import Path


def test_real_anchor_invariants():
    rows = load()
    ids = json.loads(Path('outputs/extended/sampled_ids.json').read_text())['ids']
    excluded = set(ids) | set(json.loads(Path('outputs/sampled_ids.json').read_text())['ids'])
    items = pool(rows, excluded)
    assert not excluded.intersection(a['dialogue_id'] for a in items)
    source_ids = {a['dialogue_id'] for a in items}
    assert all(r['split'] == 'train' for r in rows if r['DialogueID'] in source_ids)
    for row in rows:
        if row['DialogueID'] not in ids:
            continue
        texts = {unsegment('\n'.join(t['messages'])) for t in user_turns(row)}
        for c in ('C1', 'C2'):
            anchor = select(items, row, c, 20261025)
            assert anchor == select(items, row, c, 20261025)
            assert len(anchor['examples']) == 5
            assert all(a['text'] not in texts and a['dialogue_id'] != row['DialogueID'] for a in anchor['examples'])
            if c == 'C2':
                assert all(a['intent'] == intent(row) or a['fallback'] for a in anchor['examples'])
            for t in user_turns(row):
                base = prompt(row, t)
                actual = anchored_prompt(row, t, anchor)
                assert actual[0] == base[0]
                assert actual[1]['content'].endswith(base[1]['content'])


def test_insufficient_intent_records_fallback():
    target = dict(DialogueID=1, QA=[dict(intent='a')], Dialogue=[dict(speaker='Q', utterance='目标')])
    items = [dict(dialogue_id=i+2, turn=1, intent='a' if i == 0 else 'b', text=str(i)) for i in range(8)]
    result = select(items, target, 'C2', 1)['examples']
    assert sum(a['fallback'] for a in result) == 4
    assert result[0]['intent'] == 'a' and not result[0]['fallback']
