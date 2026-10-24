"""Audit every recorded prompt against an independently constructed raw prefix.

Equality is positional/provenance based: greetings repeated later are not leakage.
Stored hashes attest reconstruction, not an independently captured HTTP payload.
Summary outcome semantics are reviewed separately in review.md.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim.csds import clean_summary, load
from usersim.replay import prompt


def normalize(text):
    return re.sub(r'(?<=[\u4e00-\u9fff])[^\S\r\n]+(?=[\u4e00-\u9fff])', '', text)


def audit_turn(row, start, messages, recorded_hash):
    goal, _ = clean_summary(row['UserSumm'], row['DialogueID'])
    history = '\n'.join(('顾客' if msg['speaker'] == 'Q' else '客服') + '：' + normalize(msg['utterance'])
                        for msg in row['Dialogue'][:start])
    expected = [dict(role='system', content='你是一位在京东购物后联系售后客服的普通顾客。'),
                dict(role='user', content=f'任务目标：{normalize(goal)}\n对话历史：\n{history}\n只输出顾客的下一句话；使用自然、口语化的中文聊天风格；如果要连发多条消息，用换行分隔。')]
    if messages != expected:
        raise ValueError(f"{row['DialogueID']}:{start}: prompt differs from raw-only strict prefix (future/simulator contamination or template drift)")
    digest = hashlib.sha256(json.dumps(expected, ensure_ascii=False).encode()).hexdigest()
    if digest != recorded_hash:
        raise ValueError(f"{row['DialogueID']}:{start}: recorded prompt fingerprint mismatch")


def main(paths):
    if not paths:
        raise ValueError('Provide output directories')
    raw = {row['DialogueID']: row for row in load()}
    total = 0
    for path in map(Path, paths):
        ids = json.loads((path/'sampled_ids.json').read_text())['ids']
        records = [json.loads(line) for line in (path/'generations.jsonl').read_text().splitlines()]
        by_key = {(g['dialogue_id'], g['turn']): g for g in records}
        if len(by_key) != len(records):
            raise ValueError(f'{path}: duplicate generation keys')
        expected_keys = set()
        for identifier in ids:
            row = raw[identifier]
            starts = [i for i, msg in enumerate(row['Dialogue'])
                      if msg['speaker'] == 'Q' and (i == 0 or row['Dialogue'][i-1]['speaker'] != 'Q')]
            for number, start in enumerate(starts, 1):
                key = (identifier, number)
                expected_keys.add(key)
                g = by_key[key]
                audit_turn(row, start, prompt(row, {'start': start}), g['prompt_sha256'])
        if set(by_key) != expected_keys:
            raise ValueError(f'{path}: missing/extra generation keys')
        total += len(expected_keys)
        print(f'{path}: checked {len(expected_keys)} turns (including moderated)')
    print(f'PASS: checked {total} turns; raw strict prefixes only; no simulator history')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (ValueError, KeyError, OSError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        sys.exit(1)
