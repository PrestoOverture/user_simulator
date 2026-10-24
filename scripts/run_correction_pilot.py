"""Run the contracted correction pilot without writing to baseline artifacts."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_pilot as replay
from usersim.anchors import pool, select, anchored_prompt, anchor_template
from usersim.csds import load


async def run(args):
    rows = load()
    baseline = json.loads(Path('outputs/extended/sampled_ids.json').read_text())
    ids = baseline['ids'][:args.n]
    by_id = {r['DialogueID']: r for r in rows}
    chosen = [by_id[i] for i in ids]
    excluded = set(baseline['ids']) | set(json.loads(Path('outputs/sampled_ids.json').read_text())['ids'])
    items = pool(rows, excluded)
    for condition in ('C1', 'C2'):
        out = Path(args.out)/condition
        out.mkdir(parents=True, exist_ok=True)
        anchors = dict(seed=args.seed, condition=condition, excluded_ids=sorted(excluded),
                       dialogues=[select(items, r, condition, args.seed) for r in chosen])
        path = out/'anchors.json'
        if path.exists() and json.loads(path.read_text()) != anchors:
            raise ValueError('Existing anchors differ')
        replay.dump(path, anchors)
        lookup = {a['dialogue_id']: a for a in anchors['dialogues']}
        replay.sample = lambda *a: chosen
        replay.prompt = lambda row, turn: anchored_prompt(row, turn, lookup[row['DialogueID']])
        replay.template = anchor_template
        await replay.main(SimpleNamespace(out=str(out), n=len(chosen), seed=args.seed, exclude=None,
                         concurrency=args.concurrency, refresh_only=args.refresh_only, baseline=None))

    from check_correction_pilot import check
    check(Path(args.out), args.n)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--n', type=int, choices=(3,500), default=500)
    p.add_argument('--seed', type=int, default=20261025)
    p.add_argument('--out', default='outputs/correction_pilot')
    p.add_argument('--concurrency', type=int, default=8)
    p.add_argument('--refresh-only', action='store_true')
    asyncio.run(run(p.parse_args()))
