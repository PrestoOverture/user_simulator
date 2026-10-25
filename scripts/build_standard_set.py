"""Build a reproducible, source-blind ADR-016 annotation set. No API calls."""
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.behaviors import negative_agent, normalize
from usersim.csds import load, user_turns, clean_summary

OUT = ROOT / 'outputs/judge_validation'
SEED = 20261026


def annotation_view(row):
    return {k: row[k] for k in ('id', 'goal', 'history', 'previous_agent', 'text')}


def build():
    paths = {'spark': ROOT / 'outputs/extended/generations.jsonl',
             'deepseek': ROOT / 'outputs/model_b/deepseek-v4.1-flash/full500/generations.jsonl'}
    generated = {s: {r['request_id']: r for r in map(json.loads, p.read_text().splitlines()) if r['status'] == 'ok'} for s, p in paths.items()}
    pool = []
    for row in load():
        if row['QRole'] != '用户':
            continue
        for t, turn in enumerate(user_turns(row), 1):
            rid = f"{row['DialogueID']}:{t}"
            prefix = row['Dialogue'][:turn['start']]
            agent = next((m['utterance'] for m in reversed(prefix) if m['speaker'] != 'Q'), '')
            if not agent:  # Only reactions after an actual service response.
                continue
            base = dict(dialogue_id=row['DialogueID'], turn=t,
                        goal=clean_summary(row['UserSumm'], row['DialogueID'])[0],
                        history=[dict(role='user' if m['speaker'] == 'Q' else 'assistant', text=normalize(m['utterance'])) for m in prefix[-6:]],
                        previous_agent=normalize(agent), negative=negative_agent(agent))
            for source in ('human', 'spark', 'deepseek'):
                if rid not in generated['spark'] or rid not in generated['deepseek']:
                    continue  # Same paired, successful-turn population for all sources.
                text = normalize('\n'.join(turn['messages'])) if source == 'human' else generated[source][rid]['text']
                pool.append(dict(base, source=source, text=text))
    rng = random.Random(SEED)
    ids = sorted({r['dialogue_id'] for r in pool})
    rng.shuffle(ids)
    dev_ids = set(ids[:len(ids)//3])
    chosen = []
    strata = [(s, n) for s in ('human', 'spark', 'deepseek') for n in (False, True)]
    for split, quotas in [('dev', [17,17,17,17,16,16]), ('test', [33,33,33,33,34,34])]:
        for (source, negative), n in zip(strata, quotas):
            eligible = [r for r in pool if r['source'] == source and r['negative'] == negative and (r['dialogue_id'] in dev_ids) == (split == 'dev')]
            chosen.extend(dict(r, split=split) for r in rng.sample(eligible, n))
    rng.shuffle(chosen)
    blind, metadata = [], []
    for i, row in enumerate(chosen, 1):
        row['id'] = f'J{i:03d}'
        blind.append(dict(annotation_view(row), split=row['split']))
        metadata.append({k: row[k] for k in ('id','dialogue_id','turn','source','negative','split')})
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in [('standard_set.json', dict(seed=SEED, items=blind)),
                          ('source_map.json', dict(seed=SEED, paired_population=True, items=metadata)),
                          ('annotation_view.json', [annotation_view(r) for r in blind])]:
        p = OUT / name
        if p.exists():
            raise RuntimeError(f'Refusing to overwrite {name}')
        p.write_text(json.dumps(content, ensure_ascii=False, indent=2)+'\n')
    print('Built 300 blind items; dev=100 test=200; six strata=50 each.')


if __name__ == '__main__':
    build()
