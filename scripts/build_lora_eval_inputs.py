"""Export every turn of the frozen P2 evaluation population, without sampling."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.csds import load, user_turns
from usersim.replay import prompt


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(out=ROOT/'kaggle/eval_data', source=ROOT/'outputs/correction_decision/inputs.json'):
    source = Path(source).resolve()
    out = Path(out)
    frozen = json.loads(source.read_text())
    ids = frozen['ids']
    if len(ids) != 500 or len(set(ids)) != 500:
        raise ValueError('Expected frozen 500 dialogues')
    rows = {r['DialogueID']: r for r in load(ROOT/'data/raw/csds/CSDS')}
    records = []
    for did in ids:
        row = rows[did]
        for t, turn in enumerate(user_turns(row), 1):
            records.append(dict(request_id=f'{did}:{t}', dialogue_id=did, turn=t,
                                start=turn['start'], messages=prompt(row, turn)))
    out.mkdir(parents=True, exist_ok=True)
    path = out/'eval_turns.jsonl'
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in records))
    deps = [source, ROOT/'usersim/replay.py', ROOT/'usersim/csds.py', ROOT/'config/summary_overrides.json']
    deps += sorted((ROOT/'data/raw/csds/CSDS').glob('*.json'))
    manifest = dict(dialogues=len(ids), turns=len(records), population_ids=ids,
                    eval_turns_sha256=sha(path), source_sha256={str(p.relative_to(ROOT)):sha(p) for p in deps})
    (out/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'kaggle/eval_data')
    parser.add_argument('--source', type=Path, default=ROOT/'outputs/correction_decision/inputs.json')
    args = parser.parse_args()
    print(json.dumps(build(args.out, args.source), ensure_ascii=False))
