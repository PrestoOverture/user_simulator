"""Rerun only stale prompts; retain superseded text and costs as review evidence.

Final output ledgers describe the current paired run. Superseded requests remain
in the review ledger, whose known costs must be added for cumulative spending.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim.csds import load, user_turns
from usersim.replay import prompt
from usersim.spark import Spark


def fingerprint(messages):
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()


async def main(paths):
    raw = {r['DialogueID']: r for r in load()}
    evidence = Path('outputs/review_evidence')
    rerun = evidence/'rerun'
    rerun.mkdir(exist_ok=True)
    cache = rerun/'generations.jsonl'
    completed = {g['request_id']: g for g in (map(json.loads, cache.read_text().splitlines()) if cache.exists() else [])}
    jobs = []
    datasets = {}
    for directory in paths:
        root = Path(directory)
        records = list(map(json.loads, (root/'generations.jsonl').read_text().splitlines()))
        datasets[directory] = records
        for old in records:
            row = raw[old['dialogue_id']]
            turn = user_turns(row)[old['turn']-1]
            messages = prompt(row, turn)
            digest = fingerprint(messages)
            if old['prompt_sha256'] != digest:
                jobs.append((directory, old, messages, digest))
    if not jobs:
        print('No stale prompts; no API calls')
        return
    print(f'Rerunning {len(jobs)} stale turns', flush=True)
    if (rerun/'STOPPED.md').exists():
        raise RuntimeError('Previous rerun stopped; do not bypass Spark failure')
    start = time.monotonic()
    client = Spark(rerun, 4)
    async def generate(directory, old, messages, digest):
        rid = old['request_id']
        if rid in completed:
            if completed[rid]['prompt_sha256'] != digest:
                raise ValueError('Review cache has stale prompt')
            return
        text = await client.generate(messages, rid)
        g = dict(old, text=text, status='moderated' if text is None else 'ok', prompt_sha256=digest)
        completed[rid] = g
        with cache.open('a') as stream:
            stream.write(json.dumps(g, ensure_ascii=False)+'\n')
    tasks = [asyncio.create_task(generate(*job)) for job in jobs]
    try:
        await asyncio.gather(*tasks)
    except Exception:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        (rerun/'STOPPED.md').write_text('Spark rerun failed. Stopped; no fallback. Inspect request_usage.jsonl.\n')
        raise
    finally:
        await client.close()
    elapsed = time.monotonic()-start
    new_usage = list(map(json.loads, (rerun/'request_usage.jsonl').read_text().splitlines()))
    # Replace only after all necessary generations have completed successfully.
    ledger = []
    for directory, records in datasets.items():
        root = Path(directory)
        changed = {old['request_id'] for d, old, _, _ in jobs if d == directory}
        if not changed:
            continue
        old_usage = list(map(json.loads, (root/'request_usage.jsonl').read_text().splitlines()))
        superseded = dict(dataset=directory, generations=[g for g in records if g['request_id'] in changed],
                          requests=[r for r in old_usage if r['request_id'] in changed])
        ledger.append(superseded)
        for name, rows in [('generations.jsonl', [completed[g['request_id']] if g['request_id'] in changed else g for g in records]),
                           ('request_usage.jsonl', [r for r in old_usage if r['request_id'] not in changed]+[r for r in new_usage if r['request_id'] in changed])]:
            (root/name).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
        usage = json.loads((root/'token_usage.json').read_text())
        usage['elapsed_seconds'] += elapsed  # wall-clock rerun batch, shared by affected directories
        (root/'token_usage.json').write_text(json.dumps(usage, ensure_ascii=False, indent=2)+'\n')
    (evidence/'superseded_generations_and_requests.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2)+'\n')
    (rerun/'elapsed.json').write_text(json.dumps(dict(elapsed_seconds=elapsed, changed_turns=len(jobs)))+'\n')
    print(f'Completed {len(jobs)} corrected-input turns; rebuild reports with --refresh-only', flush=True)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
