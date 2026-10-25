"""ADR-016 source-blind, cached reaction measurement."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.behaviors import negative_agent, normalize
from usersim.csds import load, user_turns, clean_summary
from usersim.judge import Judge
from scripts.validate_judge import cached, predictions

SEED = 20261026
GROUPS = ('negative', 'nonnegative')


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
    ap.add_argument('--report-only', action='store_true')
    args = ap.parse_args()
    if args.limit_per_group is not None and args.limit_per_group < 1:
        ap.error('--limit-per-group must be positive')
    rows = load()
    manifest = sample_manifest(rows)
    manifest_path = ROOT/'outputs/reaction/sample_manifest.json'
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != manifest:
            raise RuntimeError('Manifest differs; stop')
    else:
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    selected = {g: v[:args.limit_per_group] for g,v in manifest['groups'].items()}
    args.out.mkdir(parents=True,exist_ok=True)
    prompt = (ROOT/'config/judge_prompt.md').read_text()
    final = json.loads((ROOT/'outputs/judge_validation/final_prompt.json').read_text())
    if hashlib.sha256(prompt.encode()).hexdigest() != final['sha256']:
        raise RuntimeError('Final prompt mismatch')
    metadata = dict(manifest=manifest, limit=args.limit_per_group, sim_dir=str(args.sim_dir.resolve()), prompt_sha256=final['sha256'])
    run_path = args.out/'run.json'
    if run_path.exists() and json.loads(run_path.read_text()) != metadata:
        raise RuntimeError('Run inputs differ; use a new output directory')
    if not run_path.exists():
        run_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    generated = {r['request_id']:r for r in map(json.loads,(args.sim_dir/'generations.jsonl').read_text().splitlines())}
    moderated_path = args.sim_dir/'moderated.json'
    moderated = {r['request_id'] for r in json.loads(moderated_path.read_text())} if moderated_path.exists() else set()
    by_id = {r['DialogueID']:r for r in rows}
    cache_path = args.out/'judgments.jsonl'
    previous = cached(cache_path)
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
    if input_path.exists() and input_path.read_text().strip() != input_hash:
        raise RuntimeError('Judge inputs changed; stop rather than reuse cache')
    if not input_path.exists():
        input_path.write_text(input_hash+'\n')
    if not args.report_only:
        pred = predictions(previous)
        pending = [r for r in todo if r['id'] not in pred]
        if pending:
            client = Judge(cache_path)
            try:
                for item in pending:
                    client.evaluate(prompt,item,previous)
            finally:
                client.close()
    requests = cached(cache_path)
    (args.out/'request_usage.jsonl').write_text(''.join(json.dumps({k:r.get(k) for k in ('id','attempt','model','provider','status','usage')}, ensure_ascii=False)+'\n' for r in requests))
    pred = predictions(requests)
    skipped['unjudged_or_parse_error'] = sum(pred.get(r['id']) is None for r in todo)
    write_report(args.out,selected,pred,requests,skipped)


if __name__=='__main__':
    main()
