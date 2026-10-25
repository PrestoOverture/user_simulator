"""ADR-016 source-blind, cached reaction measurement."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.behaviors import negative_agent, normalize
from usersim.csds import load, user_turns, clean_summary
from usersim.judge import Judge
from scripts.validate_judge import cached

SEED = 20261026
GROUPS = ('negative', 'nonnegative')


def predictions(rows):
    """Measurement completion follows Judge's response and parse budgets."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['id']].append(row)
    result = {}
    for rid, attempts in grouped.items():
        success = next((r for r in attempts if r['status']=='ok'), None)
        if success is not None:
            result[rid] = success['prediction']
        elif len(attempts) >= 3 or sum(r['status']=='parse_error' for r in attempts) >= 2:
            result[rid] = None
    return result


def dependency_evidence(out):
    """Compare complete dependency inventory with run metadata or committed files."""
    paths = sorted(p for p in (ROOT/'data/raw/csds').rglob('*') if p.is_file())
    paths += [ROOT/p for p in ('config/summary_overrides.json', 'config/summary_reviews.json',
                               'usersim/csds.py', 'usersim/replay.py')]
    current = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    run = json.loads((out/'run.json').read_text()) if (out/'run.json').exists() else {}
    recorded = run.get('dependency_sha256')
    if recorded is not None:
        return dict(proven=bool(current) and recorded==current, method='run.json', current_sha256=current,
                    unproven_paths=sorted(k for k in set(recorded)|set(current) if recorded.get(k)!=current.get(k)))
    unproven = []
    for name, digest in current.items():
        committed = subprocess.run(['git','show',f'HEAD:{name}'],cwd=ROOT,capture_output=True)
        if committed.returncode or hashlib.sha256(committed.stdout).hexdigest()!=digest:
            unproven.append(name)
    return dict(proven=bool(paths) and any(name.startswith('data/raw/csds/') for name in current) and not unproven,
                method='git HEAD', current_sha256=current, unproven_paths=unproven)


RAW_HASHES = {
    'test.json': '7284125cbed0fb5beb7cd4526f3c1fd7b7a14add55862743bbd25d81a8ae86eb',
    'train.json': '737f9be2c591b021dbfa1ff177b4ff0e5d8f81f03d3158ab9ea9a2fdaeed8909',
    'val.json': '2a2a806146b5b6f39aaa280531ca067a02a98bdb4092f63de4f808024d141c61',
}
HUMAN_DEPENDENCIES = ('usersim/csds.py', 'usersim/replay.py',
                      'config/summary_overrides.json', 'config/summary_reviews.json')
HUMAN_COMMIT = 'a13dd2e6c2d32c618582442de1d28f7325f62708'


def trust_human_evidence(out, rows):
    """Record Claude's explicit exception, only when every supplied condition holds."""
    failures = []
    raw = {}
    for name, expected in RAW_HASHES.items():
        path = ROOT/'data/raw/csds/CSDS'/name
        if not path.exists():
            failures.append(f'missing raw file: {name}')
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        raw[name] = dict(sha256=digest, expected_sha256=expected, mtime=modified.isoformat())
        if digest != expected or modified.date().isoformat() != '2021-08-27':
            failures.append(f'raw hash or mtime mismatch: {name}')
    state = subprocess.run(['git','status','--porcelain','--',*HUMAN_DEPENDENCIES], cwd=ROOT,capture_output=True,text=True)
    last = subprocess.run(['git','log','-1','--format=%H %ct','--',*HUMAN_DEPENDENCIES],cwd=ROOT,capture_output=True,text=True)
    commit = None
    committed_at = None
    if state.returncode or state.stdout.strip():
        failures.append('human dependency working tree is not clean')
    try:
        commit, seconds = last.stdout.strip().split()
        committed_at = datetime.fromtimestamp(int(seconds),timezone.utc)
        if last.returncode or commit != HUMAN_COMMIT:
            failures.append('human dependency last commit mismatch')
    except (ValueError, OverflowError):
        failures.append('human dependency commit time unavailable')
    run_path = out/'run.json'
    run = json.loads(run_path.read_text()) if run_path.exists() else {}
    first = next((r for r in rows if r['event']=='dispatch'), {})
    start = next(((f'first dispatch.{k}', first[k]) for k in ('timestamp','created_at','dispatched_at') if k in first), None)
    if start is None:
        start = next(((f'run.json.{k}',run[k]) for k in ('started_at','start_time','created_at') if k in run), None)
    started_at = None
    if start is None:
        failures.append('first dispatch and run.json contain no start timestamp')
    else:
        try:
            value = start[1]
            started_at = datetime.fromtimestamp(value,timezone.utc) if type(value) in (int,float) else datetime.fromisoformat(value.replace('Z','+00:00'))
            if started_at.tzinfo is None:
                raise ValueError('timestamp lacks timezone')
            if committed_at is None or not committed_at < started_at:
                failures.append('last commit does not predate first run')
            if any(datetime.fromisoformat(r['mtime']) >= started_at for r in raw.values()):
                failures.append('raw mtime does not predate first run')
        except (ValueError,TypeError,OverflowError,OSError):
            failures.append('first-run timestamp is invalid')
    return dict(decision_by='Claude', accepted=not failures, failures=failures, raw_files=raw,
                git_status=state.stdout.strip(), last_commit=commit,
                last_commit_time=committed_at.isoformat() if committed_at else None,
                start_time_source=start[0] if start else None,
                first_run_time=started_at.isoformat() if started_at else None)


def superseded_keys(out):
    path = out/'snapshot_check.json'
    check = json.loads(path.read_text()) if path.exists() else {}
    return {(r['id'],r['attempt']) for r in check.get('superseded', [])}


def active_responses(out, rows):
    excluded = superseded_keys(out)
    return [r for r in rows if (r['id'],r['attempt']) not in excluded]


def accept_snapshot(out, todo, prompt, old_hash, new_hash, *, trust_human_inputs=False):
    from usersim.judge import input_sha256
    current = {item['id']:item for item in todo}
    path = out/'judgments.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    dependencies = dependency_evidence(out)
    excluded = superseded_keys(out)
    trust = trust_human_evidence(out, rows) if trust_human_inputs else None
    if trust is not None and trust['accepted']:
        excluded = {key for key in excluded if not key[0].startswith('human:')}
    accepted_human = set()
    mismatches = set()
    for row in rows:
        if row['event']!='dispatch':
            continue
        key = row['id'],row['attempt']
        if key in excluded:
            continue
        rid = row['id']
        if 'input_sha256' in row:
            if rid not in current or row['input_sha256']!=input_sha256(prompt,current[rid]):
                mismatches.add(rid)
            elif rid.startswith('human:'):
                accepted_human.add(rid)
        elif rid.startswith('human:') and (dependencies['proven'] or trust is not None and trust['accepted']) and rid in current:
            accepted_human.add(rid)
        else:
            excluded.add(key)
    result = dict(accepted=not mismatches, old_sha256=old_hash, new_sha256=new_hash,
        dependencies=dependencies, trust_human_inputs=trust, accepted_human=len(accepted_human), mismatch_ids=sorted(mismatches),
        superseded=[dict(id=rid,attempt=attempt) for rid,attempt in sorted(excluded)],
        superseded_simulator=len({r['id'] for r in rows if r['event']=='response' and r.get('status')=='ok'
                                  and r['id'].startswith('simulator:') and (r['id'],r['attempt']) in excluded}),
        superseded_simulator_errors=sum(r['event']=='response' and r.get('status')=='request_error'
                                       and r['id'].startswith('simulator:') and (r['id'],r['attempt']) in excluded for r in rows),
        superseded_human=len({rid for rid,_ in excluded if rid.startswith('human:')}))
    out.mkdir(parents=True,exist_ok=True)
    (out/'snapshot_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    if result['accepted']:
        (out/'input_sha256.txt').write_text(new_hash+'\n')
    return result


def evaluate_pending(client, prompt, pending, previous, all_previous, excluded, concurrency):
    """Bound in-flight work; after a fatal error submit no further requests."""
    if concurrency < 1:
        raise ValueError('concurrency must be positive')
    if len({r['id'] for r in pending}) != len(pending):
        raise ValueError('duplicate pending judgment id')
    offsets = defaultdict(int)
    for row in all_previous:
        if (row['id'],row['attempt']) in excluded:
            offsets[row['id']] = max(offsets[row['id']],row['attempt'])
    items = iter(pending)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        active = set()
        def submit():
            item = next(items,None)
            if item is not None:
                active.add(pool.submit(client.evaluate,prompt,item,previous,attempt_offset=offsets[item['id']]))
                return True
            return False
        for _ in range(concurrency):
            if not submit():
                break
        try:
            while active:
                done, _ = wait(active,return_when=FIRST_COMPLETED)
                active.difference_update(done)
                for task in done:
                    task.result()
                for _ in done:
                    submit()
        except BaseException:
            for task in active:
                task.cancel()
            raise


def sample_manifest(rows, n=1500):
    pools = {g: [] for g in GROUPS}
    for row in rows:
        if row['QRole'] != '用户':
            continue
        for t, turn in enumerate(user_turns(row), 1):
            prefix = row['Dialogue'][:turn['start']]
            agent = next((m['utterance'] for m in reversed(prefix) if m['speaker'] != 'Q'), None)
            if agent is None:
                continue
            group = 'negative' if negative_agent(agent) else 'nonnegative'
            pools[group].append(dict(dialogue_id=row['DialogueID'], turn=t))
    rng = random.Random(SEED)
    return dict(seed=SEED, per_group=n, pool_sizes={g: len(pools[g]) for g in GROUPS},
                groups={g: rng.sample(sorted(pools[g], key=lambda r: (r['dialogue_id'], r['turn'])), n) for g in GROUPS})


def judge_item(row, t, text=None):
    turn = user_turns(row)[t-1]
    prefix = row['Dialogue'][:turn['start']]
    agent = next(m['utterance'] for m in reversed(prefix) if m['speaker'] != 'Q')
    return dict(goal=clean_summary(row['UserSumm'], row['DialogueID'])[0],
                history=[dict(role='user' if m['speaker']=='Q' else 'assistant', text=normalize(m['utterance'])) for m in prefix[-6:]],
                previous_agent=normalize(agent), text=normalize('\n'.join(turn['messages'])) if text is None else text)


def quantile(values, p):
    values = sorted(values)
    x = (len(values)-1)*p
    i = int(x)
    return values[i] + (values[min(i+1, len(values)-1)]-values[i])*(x-i)


def decision(negative_gap, nonnegative_gap, interval):
    return '限定在被拒之后' if (interval[0] > 0 or interval[1] < 0) and abs(negative_gap) > abs(nonnegative_gap) else '用普遍定义'


def summarize(pairs, replicates=2000):
    if replicates < 2000:
        raise ValueError('At least 2000 bootstrap replicates required')
    clusters = defaultdict(list)
    for p in pairs:
        clusters[p['dialogue_id']].append(p)
    ids = sorted(clusters)
    result = {}
    for metric in ('continue', 'emotion'):
        def hit(p):
            return p['topic']=='continue' if metric=='continue' else p['emotion']
        totals = {}
        for did in ids:
            totals[did] = [[sum(hit(p[s]) for p in clusters[did] if p['group']==g) for s in ('human','simulator')] + [sum(p['group']==g for p in clusters[did])] for g in GROUPS]
        def rates(draw):
            out = []
            for gi in range(2):
                h,s,n = [sum(totals[d][gi][j] for d in draw) for j in range(3)]
                out.append(dict(human=h/n, simulator=s/n, gap=(s-h)/n, n=n) if n else None)
            return out
        point = rates(ids)
        draws = [[], []]
        interactions = []
        rng = random.Random(SEED)
        attempts = 0
        while attempts < replicates or (all(point) and len(interactions) < replicates):
            attempts += 1
            values = rates(rng.choices(ids, k=len(ids))) if ids else [None,None]
            for i,v in enumerate(values):
                if v is not None:
                    draws[i].append(v['gap'])
            if all(v is not None for v in values):
                interactions.append(values[0]['gap']-values[1]['gap'])
        ci = lambda v: [quantile(v,.025), quantile(v,.975)] if v else None
        result[metric] = dict(groups={g: dict(point[i], interval=ci(draws[i])) if point[i] else None for i,g in enumerate(GROUPS)},
            interaction=point[0]['gap']-point[1]['gap'] if all(point) else None,
            interval=ci(interactions), bootstrap_valid=[len(v) for v in draws], interaction_valid=len(interactions))
    main = result['continue']
    result['decision'] = decision(*(main['groups'][g]['gap'] for g in GROUPS), main['interval']) if main['interval'] else '数据不足，无法判定'
    return result


def write_report(out, selected, pred, requests, skipped):
    pairs = []
    for g, items in selected.items():
        for r in items:
            rid = f"{r['dialogue_id']}:{r['turn']}"
            h,s = (pred.get(f'{source}:{rid}') for source in ('human','simulator'))
            if h is not None and s is not None:
                pairs.append(dict(r, group=g, human=h, simulator=s))
    summary = summarize(pairs)
    usage = Counter()
    for r in requests:
        if r.get('usage'):
            for k in ('prompt_tokens','completion_tokens','total_tokens'):
                usage[k] += r['usage'][k]
            u = r['usage']
            usage['reasoning_tokens'] += (u.get('completion_tokens_details') or {}).get('reasoning_tokens',u.get('reasoning_tokens',0))
    lines = ['# ADR-016 分组测量', '', '差距=模拟器−真人；比例分母包含 unclear。仅共同判断成功的轮次进入配对比较。',
             '按对话配对 bootstrap：2000次，种子20261026；两组共同重采样对话，保留其全部配对轮次；95%百分位线性插值区间。无该组轮次的重采样不计算该组区间；两组均有配对数据时继续抽取直到有效交互项重采样至少2000次。',
             f'选中轮次：{ {g:len(v) for g,v in selected.items()} }；跳过：{dict(skipped)}；共同有效：{len(pairs)}。',
             f'实际请求：{len(requests)}；实际 token：{dict(usage)}；缺失 usage：{sum(r.get("usage") is None for r in requests)}。reasoning_tokens 属于 completion_tokens，不重复加总。', '']
    for g in GROUPS:
        lines += [f'## {g}', '', '|来源|close/情绪否|close/情绪是|continue/情绪否|continue/情绪是|unclear/否|unclear/是|', '|---|---:|---:|---:|---:|---:|---:|']
        for source in ('human','simulator'):
            counts = Counter((p[source]['topic'],p[source]['emotion']) for p in pairs if p['group']==g)
            lines.append('|'+source+'|'+'|'.join(str(counts[t,e]) for t in ('close','continue','unclear') for e in (False,True))+'|')
        lines += ['', '|指标|真人比例|模拟器比例|差距|95%区间|配对轮次|', '|---|---:|---:|---:|---|---:|']
        for metric in ('continue','emotion'):
            v = summary[metric]['groups'][g]
            lines.append(f"|{metric}|{v['human']:.6f}|{v['simulator']:.6f}|{v['gap']:+.6f}|{v['interval']}|{v['n']}|" if v else f'|{metric}|NA|NA|NA|NA|0|')
    lines += ['', '## 交互项与预先登记判定', '']
    for metric in ('continue','emotion'):
        v = summary[metric]
        lines.append(f"- {metric}：负面组差距−非负面组差距={v['interaction']}；95%区间={v['interval']}；有效bootstrap次数（分组/交互项）={v['bootstrap_valid']}/{v['interaction_valid']}。")
    lines += [f"- 判定：{summary['decision']}。仅当 continue 交互项区间不含零且负面组差距绝对值大于非负面组时限定；情绪不参与。", '', '结果对应本次抽样中共同有效的配对轮次。']
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(statistics=summary, usage=dict(usage), skipped=dict(skipped)),ensure_ascii=False,indent=2)+'\n')
    print(f'Requests={len(requests)}; actual tokens={dict(usage)}; paired={len(pairs)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-dir', type=Path, required=True)
    ap.add_argument('--limit-per-group', type=int)
    ap.add_argument('--out', type=Path, default=ROOT/'outputs/reaction')
    ap.add_argument('--accept-new-snapshot', action='store_true')
    ap.add_argument('--trust-human-inputs', action='store_true')
    ap.add_argument('--concurrency', type=int, default=1)
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument('--report-only', action='store_true')
    modes.add_argument('--plan-only', action='store_true', help='Read inputs and cache; print pending/retry IDs without writes or requests')
    args = ap.parse_args()
    if args.limit_per_group is not None and args.limit_per_group < 1:
        ap.error('--limit-per-group must be positive')
    if args.concurrency < 1:
        ap.error('--concurrency must be positive')
    if args.trust_human_inputs and not args.accept_new_snapshot:
        ap.error('--trust-human-inputs requires --accept-new-snapshot')
    rows = load()
    manifest = sample_manifest(rows)
    manifest_path = ROOT/'outputs/reaction/sample_manifest.json'
    if not args.plan_only:
        manifest_path.parent.mkdir(parents=True,exist_ok=True)
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != manifest:
            raise RuntimeError('Manifest differs; stop')
    elif not args.plan_only:
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    selected = {g: v[:args.limit_per_group] for g,v in manifest['groups'].items()}
    if not args.plan_only:
        args.out.mkdir(parents=True,exist_ok=True)
    prompt = (ROOT/'config/judge_prompt.md').read_text()
    final = json.loads((ROOT/'outputs/judge_validation/final_prompt.json').read_text())
    if hashlib.sha256(prompt.encode()).hexdigest() != final['sha256']:
        raise RuntimeError('Final prompt mismatch')
    metadata = dict(manifest=manifest, limit=args.limit_per_group, sim_dir=str(args.sim_dir.resolve()), prompt_sha256=final['sha256'])
    run_path = args.out/'run.json'
    if run_path.exists() and {k:v for k,v in json.loads(run_path.read_text()).items() if k not in ('started_at','started_at_source')} != metadata:
        raise RuntimeError('Run inputs differ; use a new output directory')
    if not args.plan_only and not run_path.exists():
        run_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    generated = {r['request_id']:r for r in map(json.loads,(args.sim_dir/'generations.jsonl').read_text().splitlines())}
    moderated_path = args.sim_dir/'moderated.json'
    moderated = {r['request_id'] for r in json.loads(moderated_path.read_text())} if moderated_path.exists() else set()
    by_id = {r['DialogueID']:r for r in rows}
    cache_path = args.out/'judgments.jsonl'
    previous = cached(cache_path, allow_request_errors=True)
    todo = []
    skipped = Counter()
    for items in selected.values():
        for r in items:
            rid = f"{r['dialogue_id']}:{r['turn']}"
            gen = generated.get(rid)
            for source in ('human','simulator'):
                if source=='simulator' and (rid in moderated or gen and gen['status']=='moderated'):
                    skipped['moderated'] += 1
                    continue
                if source=='simulator' and (not gen or gen['status']!='ok'):
                    skipped['missing'] += 1
                    continue
                todo.append(dict(id=f'{source}:{rid}', **judge_item(by_id[r['dialogue_id']],r['turn'],gen['text'] if source=='simulator' else None)))
    input_hash = hashlib.sha256(json.dumps(todo, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    input_path = args.out/'input_sha256.txt'
    inputs_match = not input_path.exists() or input_path.read_text().strip() == input_hash
    snapshot = None
    if args.accept_new_snapshot:
        snapshot = accept_snapshot(args.out, todo, prompt, input_path.read_text().strip() if input_path.exists() else None, input_hash, trust_human_inputs=args.trust_human_inputs)
        print(json.dumps(dict(snapshot_accepted=snapshot['accepted'], accepted_human=snapshot['accepted_human'],
            superseded_simulator=snapshot['superseded_simulator'], superseded_human=snapshot['superseded_human'],
            mismatch_ids=snapshot['mismatch_ids'], trust_human_inputs=snapshot['trust_human_inputs']),ensure_ascii=False))
        if not snapshot['accepted'] and not args.plan_only:
            raise RuntimeError('Snapshot comparison failed; see snapshot_check.json')
        inputs_match = snapshot['accepted']
    if not args.plan_only and not inputs_match:
        raise RuntimeError('Judge inputs changed; stop rather than reuse cache')
    if not args.plan_only and not input_path.exists():
        input_path.write_text(input_hash+'\n')
    all_previous = previous
    previous = active_responses(args.out, previous)
    pred = predictions(previous)
    pending = [r for r in todo if r['id'] not in pred]
    if args.plan_only:
        attempted = {r['id'] for r in previous}
        previously_dispatched = {r['id'] for r in all_previous}
        print(json.dumps(dict(inputs_match=inputs_match, cached_responses=len(previous), pending=len(pending),
            retry_ids=[r['id'] for r in pending if r['id'] in attempted],
            superseded_pending=sum(r['id'] in previously_dispatched and r['id'] not in attempted for r in pending),
            unstarted=sum(r['id'] not in previously_dispatched for r in pending)), ensure_ascii=False))
        if snapshot is not None and not snapshot['accepted']:
            raise RuntimeError('Snapshot comparison failed; see snapshot_check.json')
        return
    if not args.report_only:
        if pending:
            client = Judge(cache_path)
            try:
                evaluate_pending(client,prompt,pending,previous,all_previous,
                                 superseded_keys(args.out),args.concurrency)
            finally:
                client.close()
    requests = cached(cache_path, allow_request_errors=True)
    (args.out/'request_usage.jsonl').write_text(''.join(json.dumps({k:r.get(k) for k in ('id','attempt','model','provider','status','usage')}, ensure_ascii=False)+'\n' for r in requests))
    pred = predictions(active_responses(args.out, requests))
    skipped['unjudged_or_parse_error'] = sum(pred.get(r['id']) is None for r in todo)
    write_report(args.out,selected,pred,requests,skipped)


if __name__=='__main__':
    main()
