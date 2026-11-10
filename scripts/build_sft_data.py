"""Build deterministic target-only SFT examples with the existing replay prompt."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import string
import unicodedata
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.csds import load, user_turns
from usersim.replay import prompt, unsegment

SEED = 20261109
INPUTS = ['outputs/correction_v2/inputs.json', 'outputs/correction_decision/inputs.json']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluation_ids():
    groups = [set(json.loads((ROOT / p).read_text())['ids']) for p in INPUTS]
    if any(len(g) != 500 for g in groups) or groups[0] != groups[1]:
        raise ValueError('Expected the same frozen 500 evaluation dialogues')
    return groups[0]


def examples(rows):
    for row in rows:
        for t, turn in enumerate(user_turns(row), 1):
            yield dict(request_id=f'{row["DialogueID"]}:{t}', dialogue_id=row['DialogueID'],
                       turn=t, start=turn['start'], messages=prompt(row, turn),
                       target=unsegment('\n'.join(turn['messages'])))


def build(out, seed=SEED):
    ids = evaluation_ids()
    rows = load(ROOT / 'data/raw/csds/CSDS')
    eligible = [r for r in rows if r['QRole'] == '用户']
    train_rows = [r for r in eligible if r['split'] == 'train' and r['DialogueID'] not in ids]
    evaluation = list(examples([r for r in eligible if r['DialogueID'] in ids]))
    if {r['dialogue_id'] for r in evaluation} != ids:
        raise ValueError('Evaluation dialogue missing from eligible CSDS')
    sample = random.Random(seed).sample(evaluation, 50)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stats = {}
    for name, records in [('train', examples(train_rows)), ('eval_sample', sample)]:
        n = pc = tc = 0
        with (out / f'{name}.jsonl').open('w') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
                n += 1
                pc += sum(len(m['content']) for m in record['messages'])
                tc += len(record['target'])
        stats[name] = dict(samples=n, prompt_chars=pc, target_chars=tc,
                           total_chars=pc+tc, sha256=sha(out / f'{name}.jsonl'))
    dependencies = INPUTS + ['usersim/replay.py', 'usersim/csds.py', 'config/summary_overrides.json']
    dependencies += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'data/raw/csds/CSDS').glob('*.json'))]
    manifest = dict(schema_version=1, seed=seed, train_dialogues=len(train_rows),
                    excluded_dialogue_ids=sorted(ids), excluded_dialogue_count=len(ids),
                    evaluation_turns=len(evaluation), **stats,
                    token_estimate=dict(method='1 token per Unicode character; excludes chat template; estimate only',
                                        train_total=stats['train']['total_chars'],
                                        train_target=stats['train']['target_chars']),
                    source_sha256={p: sha(ROOT/p) for p in dependencies})
    (out/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
    return manifest


# Unicode Chinese punctuation plus ASCII punctuation; horizontal whitespace only.
PUNCTUATION = string.punctuation + ''.join(
    chr(i) for lo, hi in ((0x2000, 0x2070), (0x3000, 0x3040), (0xFF00, 0xFF66))
    for i in range(lo, hi) if unicodedata.category(chr(i)).startswith('P'))
HAN = r'[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0003134f]'
PUNCT = '[' + re.escape(PUNCTUATION) + ']'
PUNCT_SPACE = re.compile(r'(?<=' + HAN + r')[^\S\r\n]+(?=' + PUNCT + r')|(?<='
                         + PUNCT + r')[^\S\r\n]+(?=' + HAN + ')')


def clean_target(text):
    return PUNCT_SPACE.sub('', unsegment(text))


def sampled_examples(rows, seed=SEED):
    rng = random.Random(seed)
    for row in rows:
        records = list(examples([row]))
        if not records:
            raise ValueError(f'No user turn: {row["DialogueID"]}')
        record = rng.choice(records)
        record['target'] = clean_target(record['target'])
        yield record


def build_v2(out, seed=SEED):
    ids = evaluation_ids()
    rows = load(ROOT / 'data/raw/csds/CSDS')
    eligible = [r for r in rows if r['QRole'] == '用户']
    train_rows = [r for r in eligible if r['split'] == 'train' and r['DialogueID'] not in ids]
    records = list(sampled_examples(train_rows, seed))
    before = {r['request_id']: r['target'] for row in train_rows for r in examples([row])}
    dirty_before = sum(bool(PUNCT_SPACE.search(before[r['request_id']])) for r in records)
    dirty_after = sum(bool(PUNCT_SPACE.search(r['target'])) for r in records)
    evaluation = list(examples([r for r in eligible if r['DialogueID'] in ids]))
    for record in evaluation:
        record['target'] = clean_target(record['target'])
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stats = {}
    for name, items in [('train', records), ('eval_sample', random.Random(seed).sample(evaluation, 50))]:
        path = out / f'{name}.jsonl'
        path.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in items))
        pc = sum(len(m['content']) for r in items for m in r['messages'])
        tc = sum(len(r['target']) for r in items)
        stats[name] = dict(samples=len(items), prompt_chars=pc, target_chars=tc,
                           total_chars=pc+tc, sha256=sha(path))
    dependencies = INPUTS + ['usersim/replay.py', 'usersim/csds.py', 'config/summary_overrides.json']
    dependencies += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'data/raw/csds/CSDS').glob('*.json'))]
    manifest = dict(schema_version=2, seed=seed, train_dialogues=len(train_rows),
                    excluded_dialogue_ids=sorted(ids), excluded_dialogue_count=len(ids),
                    evaluation_turns=len(evaluation), **stats,
                    target_punctuation_spaces=dict(before_count=dirty_before, after_count=dirty_after,
                        before_ratio=dirty_before/len(records), after_ratio=dirty_after/len(records),
                        denominator=len(records)),
                    token_estimate=dict(method='1 token per Unicode character; excludes chat template; estimate only',
                        train_total=stats['train']['total_chars'], train_target=stats['train']['target_chars']),
                    source_sha256={p: sha(ROOT/p) for p in dependencies})
    (out/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=['1', '2'], default='1')
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    out = args.out or ROOT/('kaggle/sft_data_v2' if args.version == '2' else 'kaggle/sft_data')
    print(json.dumps((build_v2 if args.version == '2' else build)(out, args.seed), ensure_ascii=False, indent=2))
