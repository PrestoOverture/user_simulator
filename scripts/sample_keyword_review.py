"""Freeze reproducible pre-fix keyword samples for direct reading; no model judge."""
import csv
import json
import random
from pathlib import Path

METRICS = ('politeness', 'escalation', 'refusal', 'urgency', 'correction')
SEED = 20261025


def main():
    target = Path('outputs/review_evidence/keyword_samples.json')
    if target.exists():
        raise SystemExit('Samples already frozen; refusing to overwrite judgments')
    rows = []
    for directory in ('outputs', 'outputs/extended'):
        root = Path(directory)
        with (root/'side_by_side.csv').open(newline='') as stream:
            pairs = {(r['dialogue_id'], r['turn']): r for r in csv.DictReader(stream)}
        with (root/'metrics_by_turn.csv').open(newline='') as stream:
            for r in csv.DictReader(stream):
                p = pairs[r['dialogue_id'], r['turn']]
                rows.append(dict(r, dataset=directory, text=p[r['source']], context=p['context_last_two']))
    samples = []
    for metric in METRICS:
        for source in ('human', 'simulator'):
            for hit, limit in ((1, 20), (0, 50)):
                pool = [r for r in rows if r['source'] == source and int(r[metric]) == hit]
                rng = random.Random(f'{SEED}:{metric}:{source}:{hit}')
                for r in rng.sample(pool, min(limit, len(pool))):
                    samples.append(dict(sample_id=len(samples), metric=metric, source=source, hit=hit,
                                        dataset=r['dataset'], dialogue_id=int(r['dialogue_id']), turn=int(r['turn']),
                                        text=r['text'], context=r['context'], reasonable=None, note=''))
    target.write_text(json.dumps(dict(seed=SEED, sampling='pooled 20+500, per metric/source/hit; uniform without replacement; independent string-seeded RNG',
                                     keywords=json.loads(Path('config/keywords.json').read_text()), samples=samples), ensure_ascii=False, indent=2)+'\n')
    print(f'{len(samples)} samples frozen at {target}')


if __name__ == '__main__':
    main()
