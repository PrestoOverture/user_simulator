"""Offline behavior measurement. Validation must precede comparative summaries."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.behaviors import contexts, measure, validation_samples, RULES, summarize, validation_summary, report
from usersim.csds import load

OUT = ROOT / 'outputs/behaviors'


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def collect(rules=None):
    ids = json.loads((ROOT/'outputs/extended/sampled_ids.json').read_text())['ids']
    folders = dict(baseline='outputs/extended', C1='outputs/correction_pilot/C1', C2='outputs/correction_pilot/C2')
    generations = {name: {g['request_id']: g for g in map(json.loads, (ROOT/folder/'generations.jsonl').read_text().splitlines())} for name, folder in folders.items()}
    common = set.intersection(*({rid for rid,g in gs.items() if g['status']=='ok'} for gs in generations.values()))
    records, full = [], []
    lookup = {}
    for row in load(ROOT/'data/raw/csds/CSDS'):
        if row['QRole'] != '用户':
            continue
        lookup[row['DialogueID']] = row
        for ctx in contexts(row):
            full.append(dict(dialogue_id=ctx['dialogue_id'], turn=ctx['turn'], values=measure(ctx, rules=rules)))
    for did in ids:
        for ctx in contexts(lookup[did]):
            if ctx['request_id'] not in common:
                continue
            for source in ('human', *folders):
                text = ctx['text'] if source == 'human' else generations[source][ctx['request_id']]['text']
                records.append(dict(ctx, text=text, source=source, values=measure(ctx,text,rules)))
    return records, full


def supplement_human(samples, frozen_rules):
    """Top up rare human hits from raw CSDS; keep the original frozen audit."""
    import random
    from usersim.behaviors import LABELS
    additions = []
    needed = {k: 30 - sum(s['metric']==k and s['side']=='human' and s['predicted']==1 for s in samples)
              for k in LABELS}
    needed = {k:n for k,n in needed.items() if n > 0}
    pools = {k:[] for k in needed}
    existing = {(s['metric'], s['request_id']) for s in samples if s['side']=='human'}
    for row in load(ROOT/'data/raw/csds/CSDS'):
        if row['QRole'] != '用户':
            continue
        for ctx in contexts(row):
            values = measure(ctx, rules=frozen_rules)
            for metric in needed:
                if values[metric] and (metric,ctx['request_id']) not in existing:
                    pools[metric].append(ctx)
    for metric,n in needed.items():
        pool = pools[metric]
        rng = random.Random(f"{frozen_rules['seed']}:supplement:{metric}:human:1")
        for ctx in rng.sample(pool, min(n, len(pool))):
            additions.append(dict(sample_id=f"{metric}/human/1/human/{ctx['request_id']}",
                metric=metric, side='human', predicted=1, pool_size=len(pool), source='human',
                **{k:ctx[k] for k in ('request_id','text','previous_agent','goal','history')},
                judgment=None, reason=None, sampling_stage='full_csds_hit_supplement'))
    return additions


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prepare-validation', action='store_true')
    args = p.parse_args()
    OUT.mkdir(exist_ok=True)
    records, full = collect()
    frozen_rules = json.loads((OUT/'rules_v1.json').read_text()) if (OUT/'rules_v1.json').exists() else RULES
    frozen_records, _ = collect(frozen_rules)
    samples = validation_samples(frozen_records)
    samples += supplement_human(samples, frozen_rules)
    path = OUT/'validation_samples.json'
    if args.prepare_validation:
        if path.exists():
            raise ValueError('Refusing to overwrite existing judgments')
        write('rules_v1.json', RULES)
        write('validation_samples.json', samples)
        write('rule_changes.json', [])
        print(f'Prepared {len(samples)} samples; no comparative statistics computed')
        return
    reviewed = json.loads(path.read_text())
    assert len(reviewed) == len(samples)
    for expected, actual in zip(samples, reviewed):
        assert all(actual[k] == v for k,v in expected.items() if k not in ('judgment','reason'))
        assert type(actual['judgment']) is bool and actual['reason']
    for sample in reviewed:
        context = dict(sample, turn=int(sample['request_id'].split(':')[1]))
        sample['revised_prediction'] = measure(context)[sample['metric']]
    write('validation_samples.json', reviewed)
    validation = validation_summary(reviewed)
    summary = summarize(records, full)
    write('summary.json', summary)
    write('validation_summary.json', validation)
    write('metrics_by_turn.json', records)
    (OUT/'report.md').write_text(report(summary, validation, reviewed))
    print(f'Wrote behavior artifacts: {len(records)} records, {len(reviewed)} reviewed samples')


if __name__ == '__main__':
    main()
