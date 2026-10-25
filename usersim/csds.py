"""CSDS adaptation; raw messages remain untouched."""
import json
import random
import re
from pathlib import Path

RESULT = re.compile('最终|问题解决了|问题已解决|退款成功|退货成功|用户(?:表示)?同意|用户(?:表示)?接受|用户表示可以接收|不再退货|用户表示已经退款了|用户(?:表示)?满意|用户表示感谢|用户决定自行解决')
QUESTION = re.compile('询问|是否|能否|希望|要求|想|未|没有|不成功|之前|此前')


def clean_summary(items, dialogue_id=None):
    kept, removed = [], []
    for part in re.split(r'[。；;，,]', ''.join(items)):
        if not part.strip():
            continue
        (removed if RESULT.search(part) and not QUESTION.search(part) else kept).append(part)
    goal = '。'.join(kept) or '咨询购物售后问题'
    overrides_path = Path(__file__).resolve().parents[1] / 'config/summary_overrides.json'
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    return overrides.get(str(dialogue_id), goal), removed


def load(root='data/raw/csds/CSDS'):
    rows = []
    for p in sorted(Path(root).glob('*.json')):
        for row in json.loads(p.read_text()):
            rows.append(dict(row, split=p.stem))
    if not rows:
        raise ValueError('No CSDS data')
    return rows


def user_turns(row):
    turns = []
    for i, msg in enumerate(row['Dialogue']):
        if msg['speaker'] != 'Q':
            continue
        if i == 0 or row['Dialogue'][i-1]['speaker'] != 'Q':
            turns.append({'start': i, 'messages': []})
        turns[-1]['messages'].append(msg['utterance'])
    return turns


def sample(rows, n, seed, excluded=()):
    pool = [r for r in rows if r['QRole'] == '用户' and r['DialogueID'] not in excluded]
    preferred = [r for r in pool if 6 <= len(user_turns(r)) <= 30]
    rng = random.Random(seed)
    rng.shuffle(preferred)
    rest = [r for r in pool if r not in preferred]
    rng.shuffle(rest)
    if len(pool) < n:
        raise ValueError('Insufficient eligible dialogues')
    return (preferred + rest)[:n]


def summary_audit(row, allow_unreviewed=False):
    reviews = json.loads((Path(__file__).resolve().parents[1] / 'config/summary_reviews.json').read_text())
    rule_goal, removed = clean_summary(row['UserSumm'])
    goal, _ = clean_summary(row['UserSumm'], row['DialogueID'])
    if allow_unreviewed and str(row['DialogueID']) not in reviews:
        return dict(dialogue_id=row['DialogueID'], original=row['UserSumm'], rule_goal=rule_goal,
                    goal=goal, removed=removed, override=goal != rule_goal,
                    reviewed=False, reviewer=None, review_note=None)
    review = reviews[str(row['DialogueID'])]
    if review['original'] != row['UserSumm']:
        raise ValueError('Summary review does not match source')
    return dict(dialogue_id=row['DialogueID'], original=row['UserSumm'], rule_goal=rule_goal,
                goal=goal, removed=removed, override=goal != rule_goal,
                reviewed=True, reviewer='Codex manual reading; no model API', review_note=review['note'])
