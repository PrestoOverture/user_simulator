"""Leave-option replay, frozen sampling and offline measurement; Spark only."""
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

from . import replay
from .behaviors import negative_agent
from .csds import summary_audit

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261028
REPLICATES = 2000
LEAVE_INSTRUCTION = '如果你此时不想再回复（问题已解决、不想继续、或者放弃），只输出：[离开]'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prompt(row, point):
    messages = replay.prompt(row, {'start': point['start']})
    messages[-1]['content'] += '\n' + LEAVE_INSTRUCTION
    return messages


def classify(text):
    text = text.strip()
    return 'leave' if text == '[离开]' else 'mixed' if '[离开]' in text else 'stay'


def closing_only(text, config=None):
    config = config or json.loads((ROOT/'config/closing_phrases.json').read_text())
    compact = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
    hit = any(p in compact for p in config['phrases'])
    for phrase in sorted(config['phrases'] + config['fillers'], key=len, reverse=True):
        compact = compact.replace(phrase, '')
    return hit and not compact


def select(rows, n=2000, seed=SEED):
    """One leave point and at most one continued agent-run endpoint per dialogue."""
    def endings(pool):
        return dict(Counter(r['Dialogue'][-1]['speaker'] if r['Dialogue'] else 'empty' for r in pool))
    users = [r for r in rows if r['QRole'] == '用户']
    eligible = [r for r in users if r['Dialogue'] and r['Dialogue'][-1]['speaker'] == 'A']
    if len(eligible) < 300:
        raise RuntimeError(f'STOP: only {len(eligible)} user dialogues end with agent (<300)')
    leave, continued = [], []
    continuation_positions = 0
    for row in sorted(eligible, key=lambda r: (r['split'], str(r['DialogueID']))):
        d = row['Dialogue']
        common = dict(dialogue_id=row['DialogueID'], split=row['split'])
        leave.append(dict(common, kind='leave', start=len(d)))
        positions = [i for i in range(1, len(d)) if d[i-1]['speaker']=='A' and d[i]['speaker']=='Q']
        continuation_positions += len(positions)
        if positions:
            start = random.Random(f'{seed}:{row["split"]}:{row["DialogueID"]}').choice(positions)
            continued.append(dict(common, kind='continue', start=start))
    rng = random.Random(seed)
    sampled_leave = rng.sample(leave, min(n, len(leave)))
    paired_ids = {(p['split'], p['dialogue_id']) for p in sampled_leave}
    paired_continue = [p for p in continued if (p['split'], p['dialogue_id']) in paired_ids]
    chosen = sampled_leave + paired_continue
    by_id = {(r['split'], r['DialogueID']): r for r in users}
    for p in chosen:
        row = by_id[p['split'], p['dialogue_id']]
        agent = row['Dialogue'][p['start']-1]['utterance']
        p.update(id=f'{p["split"]}:{p["dialogue_id"]}:{p["kind"]}:{p["start"]}',
                 negative=negative_agent(agent), closing=closing_only(agent), previous_agent=agent)
        p['messages'] = prompt(row, p)
        p['input_sha256'] = digest(p['messages'])
    population = dict(all_dialogues=len(rows), all_endings=endings(rows), user_dialogues=len(users),
                      user_endings=endings(users), leave_points=len(leave),
                      continue_dialogues=len(continued), continue_positions=continuation_positions,
                      no_continue_dialogues=len(leave)-len(continued))
    audits = [summary_audit(by_id[k], allow_unreviewed=True) for k in sorted({(p['split'],p['dialogue_id']) for p in chosen})]
    return dict(seed=seed, population=population, points=chosen, summary_audit=audits,
                closing_config=json.loads((ROOT/'config/closing_phrases.json').read_text()))


def interval(records, outcome):
    ids = sorted({r['dialogue_id'] for r in records})
    clusters = [(sum(r['outcome']==outcome for r in records if r['dialogue_id']==i),
                 sum(r['dialogue_id']==i for r in records)) for i in ids]
    n = len(records)
    if not n:
        return dict(hits=0, n=0, rate=None, low=None, high=None)
    rng = random.Random(SEED)
    draws = []
    for _ in range(REPLICATES):
        sample = rng.choices(clusters, k=len(clusters))
        draws.append(sum(x[0] for x in sample)/sum(x[1] for x in sample))
    draws.sort()
    def q(p):
        x = (len(draws)-1)*p
        i = int(x)
        return draws[i] + (draws[min(i+1,len(draws)-1)]-draws[i])*(x-i)
    hits = sum(r['outcome']==outcome for r in records)
    return dict(hits=hits,n=n,rate=hits/n,low=q(.025),high=q(.975))


def build_report(out):
    out = Path(out)
    manifest = json.loads((out/'manifest.json').read_text())
    cache = read_generations(out/'generations.jsonl')
    results = []
    for p in manifest['points']:
        r = cache.get(p['id'])
        if r is None or r['input_sha256'] != p['input_sha256']:
            raise RuntimeError('Missing or stale generation: '+p['id'])
        results.append(dict(p, **{k:r[k] for k in ('status','text')}, outcome=classify(r['text']) if r['status']=='ok' else None))
    valid = [r for r in results if r['status']=='ok']
    groups = {}
    for kind in ('leave','continue'):
        for label, key, value in [('all',None,None),('negative', 'negative',True),('nonnegative','negative',False),('closing','closing',True),('nonclosing','closing',False)]:
            subset = [r for r in valid if r['kind']==kind and (key is None or r[key]==value)]
            groups[f'{kind}/{label}'] = {o:interval(subset,o) for o in ('leave','mixed')}
    attempts_path = out/'requests.jsonl'
    events = [json.loads(s) for s in attempts_path.read_text().splitlines()] if attempts_path.exists() else []
    attempts = [r for r in events if r['event']=='response']
    usage = {k:sum((r.get('usage') or {}).get(k,0) for r in attempts) for k in ('prompt_tokens','completion_tokens','total_tokens')}
    moderated = [r['id'] for r in results if r['status']=='moderated']
    summary = dict(phase=manifest['phase'],seed=SEED,bootstrap_replicates=REPLICATES,
                   population=manifest['population'],groups=groups,planned=len(results),
                   moderated_ids=moderated,moderated_count=len(moderated),moderated_rate=len(moderated)/len(results),
                   usage=usage,attempts=len(attempts),unknown_usage=sum(r.get('usage') is None for r in attempts),
                   unreviewed_summaries=sum(not a['reviewed'] for a in manifest['summary_audit']))
    write_json(out/'summary.json',summary)
    lines = ['# 直接离开测量', '', f"阶段：{manifest['phase']}；计划 {len(results)} 条。", '',
             '## 全量 CSDS 分布', '', json.dumps(manifest['population'],ensure_ascii=False,sort_keys=True), '',
             'A=客服，Q=对方；同时报告全量及 QRole=用户子集。抽样池为以客服结束的用户对话；离开点为最后一条客服之后，继续点为同批对话内客服连续消息结束且下一条是用户的位置，每段固定种子随机取一个，再各无放回抽至2000条。不足全取。继续点与抽中的离开点按对话ID配对；没有继续位置的对话只保留离开点。', '',
             '## 离开率与混合率', '',
             '真人在离开点=100%，继续点=0%。纯离开为去首尾空白后恰好 [离开]；混合为包含标记但还有其他文字。审核拒绝只剔除该点的真人及模拟器，不剔除同段其他点。', '',
             f'按对话有放回 bootstrap {REPLICATES} 次，种子 {SEED}，95%百分位区间线性插值；每次保留被抽中对话的全部有效点。空组为 NA。', '',
             '|点/分组|有效数|离开率 [95% CI]|混合率 [95% CI]|','|---|---:|---|---|']
    def fmt(x):
        return 'NA' if x['rate'] is None else f"{x['hits']}/{x['n']} = {x['rate']:.6f} [{x['low']:.6f}, {x['high']:.6f}]"
    for key, g in groups.items():
        lines.append(f"|{key}|{g['leave']['n']}|{fmt(g['leave'])}|{fmt(g['mixed'])}|")
    lines += ['', 'negative 使用 usersim/behaviors.py 现有规则；closing 要求 config/closing_phrases.json 中结束语命中，移除结束语、语气词和标点后无剩余内容，不把夹杂业务内容的消息算作只是结束语。两者均只看前缀最后一条客服消息。', '',
              '## 审核、用量与局限', '',
              f"审核剔除 {len(moderated)}/{len(results)}；ID：{json.dumps(moderated,ensure_ascii=False)}。Spark token：{json.dumps(usage,sort_keys=True)}；请求尝试 {len(attempts)}；用量未知尝试 {summary['unknown_usage']}。", '',
              f"模型 spark-x2.6；thinking disabled；temperature 官方默认（不传）；不传 max_tokens。未人工核对摘要 {summary['unreviewed_summaries']} 段，沿用 ADR-017 清洗及已有覆盖。", '',
              '这是一个有利于模拟器离开的设计：有一个明确的选项可以选。真人离开往往只是沉默。日志结束仅是离开代理，可能是截断；这里只测给定真实前缀下是否愿意离开，不能测自由对话中途放弃或因果效应。CSDS 是较早的人类客服数据；摘要可能残留结果；关键词分组有误差；审核剔除有选择偏差。区间不包含这些误差，小批仅验证管线，不能当作全量结论。', '']
    (out/'report.md').write_text('\n'.join(lines))
    return {name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('report.md','summary.json')}


def write_json(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n')


def read_generations(path):
    rows = [json.loads(s) for s in Path(path).read_text().splitlines()] if Path(path).exists() else []
    if len({r['id'] for r in rows}) != len(rows):
        raise RuntimeError('Duplicate generations')
    return {r['id']:r for r in rows}
