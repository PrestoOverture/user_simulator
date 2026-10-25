"""Bounded, cached judge validation. --report-only never constructs an API client."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.judge import Judge, MODEL, TOPICS

OUT = ROOT / 'outputs/judge_validation'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    if path.exists():
        raise RuntimeError(f'Refusing overwrite: {path.name}')
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def frozen():
    manifest = json.loads((OUT/'annotation_freeze.json').read_text())
    for name, expected in manifest['files'].items():
        if digest(OUT/name) != expected:
            raise RuntimeError(f'Frozen file changed: {name}')
    return json.loads((OUT/'standard_set.json').read_text())['items']


def cached(path, *, allow_request_errors=False):
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    dispatch = Counter((r['id'], r['attempt']) for r in rows if r['event'] == 'dispatch')
    responses = [r for r in rows if r['event'] == 'response']
    if dispatch != Counter((r['id'], r['attempt']) for r in responses):
        raise RuntimeError('Unresolved dispatch; do not resend automatically')
    if not allow_request_errors and any(r['status'] == 'request_error' and (r.get('error_type') != 'ReadTimeout' or r['attempt'] == 2) for r in responses):
        raise RuntimeError('Request error in cache; stop for review')
    if any(n != 1 for n in dispatch.values()):
        raise RuntimeError('Duplicate request')
    return responses


def predictions(rows):
    result = {}
    for r in rows:
        if r['status'] == 'ok':
            result[r['id']] = r['prediction']
        elif r['attempt'] == 2:
            result[r['id']] = None
    return result


def metric(items, pred, dimension):
    labels = list(TOPICS) if dimension == 'topic' else [False, True]
    matrix = [[0]*(len(labels)+1) for _ in labels]
    for r in items:
        a = r['annotation'][dimension]
        b = pred[r['id']][dimension] if pred[r['id']] is not None else None
        matrix[labels.index(a)][labels.index(b) if b in labels else len(labels)] += 1
    n = len(items)
    correct = sum(matrix[i][i] for i in range(len(labels)))
    observed = correct/n if n else 0
    expected = sum(sum(matrix[i])*sum(row[i] for row in matrix) for i in range(len(labels)))/(n*n) if n else 0
    return dict(n=n, accuracy=observed, kappa=(observed-expected)/(1-expected) if expected != 1 else None,
                labels=labels, matrix=matrix)


def report(items):
    final = json.loads((OUT/'final_prompt.json').read_text())
    if digest(OUT/f"prompt_{final['version']}.md") != final['sha256']:
        raise RuntimeError('Final prompt hash mismatch')
    versions = sorted(OUT.glob('prompt_v*.md'))
    if not 1 <= len(versions) <= 3:
        raise RuntimeError('Invalid version count')
    lines = ['# ADR-016 判断器验证', '', f'provider=sandbox；model={MODEL}；temperature 使用官方默认（省略参数）；接受 Gemini 默认推理。',
             '', '种子 20261026；共同有效的配对轮次池；仅抽取客服已经回应的轮次。六个来源×负面分层各50条；开发100、测试200，按对话隔离。最近6条真实消息为严格轮前历史。',
             '标注仅阅读 annotation_view.json，来源与负面分组另存 source_map.json；300条人工式标注及理由在首次请求前冻结，SHA-256 见 annotation_freeze.json。单一 Codex 标注者。',
             '读取超时允许同一输入必要重试一次，原请求保留，缺失usage单列；不重新评估已成功样本。解析失败在同一输入上重试一次；仍失败记 parse_error，计入准确率分母和混淆矩阵错误列。κ以 parse_error 为仅预测侧类别计算。',
             f'开发集共评估{len(versions)}版 prompt，定稿为{final["version"]}；5条试跑计入v1开发集100条内。测试只对定稿版本评估一遍，缓存重建不发请求。', '']
    metadata = {r['id']: r for r in json.loads((OUT/'source_map.json').read_text())['items']}
    all_usage = Counter()
    result = {}
    runs = [(p.stem, 'dev', p) for p in versions] + [(f"test_{final['version']}", 'test', OUT/f"prompt_{final['version']}.md")]
    for name, split, prompt_path in runs:
        rows = cached(OUT/f'{name}.jsonl')
        subset = [r for r in items if r['split'] == split]
        pred = predictions(rows)
        if len(rows) > 2*len(subset) or any(r['model'] != MODEL for r in rows):
            raise RuntimeError('Request budget/model mismatch')
        if set(pred) != {r['id'] for r in subset}:
            raise RuntimeError(f'Incomplete cache: {name}')
        usage = Counter()
        unknown_usage = sum(r['usage'] is None for r in rows)
        for r in rows:
            if r['usage'] is None:
                continue
            for key in ('prompt_tokens','completion_tokens','total_tokens'):
                usage[key] += r['usage'][key]
            u = r['usage']
            usage['reasoning_tokens'] += (u.get('completion_tokens_details') or {}).get('reasoning_tokens', u.get('reasoning_tokens', 0))
        all_usage.update(usage)
        scores = {d:metric(subset,pred,d) for d in ('topic','emotion')}
        result[name] = dict(scores=scores, usage=dict(usage), usage_missing_requests=unknown_usage)
        lines += [f'## {name}', '', f'样本 {len(subset)}；实际请求 {len(rows)}；解析失败响应 {sum(r["status"] == "parse_error" for r in rows)}；最终 parse_error {sum(p is None for p in pred.values())}。',
                  f'prompt SHA-256：{digest(prompt_path)}', '', '|指标|准确率|Cohen κ|', '|---|---:|---:|']
        for d,s in scores.items():
            k = f"{s['kappa']:.6f}" if s['kappa'] is not None else 'NA'
            lines.append(f"|{d}|{s['accuracy']:.2%}|{k}|")
        for d,s in scores.items():
            labels = [str(x) for x in s['labels']]
            lines += ['', f'### {d} 混淆矩阵（行=冻结标注，列=Gemini）', '', '|标注\\预测|'+'|'.join(labels+['parse_error'])+'|', '|---|'+'---:|'*(len(labels)+1)]
            lines += ['|'+label+'|'+'|'.join(map(str,row))+'|' for label,row in zip(labels,s['matrix'])]
        lines += ['', '### 分组一致率', '', '|分组|n|topic|emotion|两个标签均一致|', '|---|---:|---:|---:|---:|']
        groups = [(s, [r for r in subset if metadata[r['id']]['source']==s]) for s in ('human','spark','deepseek')]
        groups += [(f'negative={b}',[r for r in subset if metadata[r['id']]['negative']==b]) for b in (False,True)]
        groups += [(f'{s}/negative={b}',[r for r in subset if metadata[r['id']]['source']==s and metadata[r['id']]['negative']==b]) for s in ('human','spark','deepseek') for b in (False,True)]
        for label, group in groups:
            rates = [sum(pred[r['id']] is not None and pred[r['id']][d]==r['annotation'][d] for r in group)/len(group) for d in ('topic','emotion')]
            both = sum(pred[r['id']] is not None and all(pred[r['id']][d]==r['annotation'][d] for d in ('topic','emotion')) for r in group)/len(group)
            lines.append(f'|{label}|{len(group)}|{rates[0]:.2%}|{rates[1]:.2%}|{both:.2%}|')
        lines += ['', f'已返回的实际 token 用量：{dict(usage)}。未返回 usage 的请求：{unknown_usage}（读取超时）；不把缺失用量记为0。reasoning_tokens 是 completion_tokens 的子项，不重复加总。', '', '### 分歧例子', '', '每个指标按盲ID升序取前3个分歧，非额外标注或改标。', '']
        for d in ('topic','emotion'):
            wrong = [r for r in subset if pred[r['id']] is None or pred[r['id']][d] != r['annotation'][d]][:3]
            for r in wrong:
                lines.append(f"- {d} / {r['id']}：客服「{r['previous_agent']}」；用户「{r['text'].replace(chr(10),' / ')}」；标注={r['annotation'][d]}；Gemini={pred[r['id']]}。冻结理由：{r['annotation']['reason']}")
    scores = result[f"test_{final['version']}"]['scores']
    topic = scores['topic']['accuracy'] >= .85 and scores['topic']['kappa'] is not None and scores['topic']['kappa'] >= .70
    emotion = scores['emotion']['kappa'] is not None and scores['emotion']['kappa'] >= .70
    lines += ['', '## 测试集门槛', '', f'话题走向 κ≥0.70 且准确率≥85%：{"达标" if topic else "未达标"}。', f'情绪表达 κ≥0.70：{"达标" if emotion else "未达标"}。', f'总体：{"达标" if topic and emotion else "未达标；不上量，回报用户"}。', '', f'全部版本和测试已返回的实际 token 总量：{dict(all_usage)}；另有 {sum(r["usage_missing_requests"] for r in result.values())} 次请求未返回 usage，完整计费总量未知。', '', '本报告仅验证判断器与冻结标准集的一致性，未运行全量行为测量。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')
    (OUT/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('Report rebuilt from complete caches; no requests.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report-only', action='store_true')
    ap.add_argument('--phase', choices=['pilot','dev','finalize','test'])
    ap.add_argument('--version', type=int, choices=[1,2,3], default=1)
    args = ap.parse_args()
    items = frozen()
    if args.report_only:
        report(items)
        return
    version = f'v{args.version}'
    final_path = OUT/'final_prompt.json'
    prompt_path = OUT/f'prompt_{version}.md'
    if args.phase in ('pilot','dev'):
        if final_path.exists():
            raise RuntimeError('Prompt finalized; development closed')
        if not prompt_path.exists():
            if args.version > 1 and len(predictions(cached(OUT/f'prompt_v{args.version-1}.jsonl'))) != 100:
                raise RuntimeError('Previous development version incomplete')
            write(OUT/f'iteration_{version}.json', dict(version=version, created_at=datetime.now(timezone.utc).isoformat(), source='development only'))
            prompt_path.write_text((ROOT/'config/judge_prompt.md').read_text())
        if prompt_path.read_text() != (ROOT/'config/judge_prompt.md').read_text():
            raise RuntimeError('Version immutable; use next version')
    if args.phase == 'finalize':
        if len(predictions(cached(OUT/f'prompt_{version}.jsonl'))) != 100:
            raise RuntimeError('Development incomplete')
        write(final_path, dict(version=version, sha256=digest(prompt_path), finalized_at=datetime.now(timezone.utc).isoformat()))
        print('Prompt finalized; test still untouched.')
        return
    if args.phase == 'test':
        final = json.loads(final_path.read_text())
        if version != final['version'] or digest(prompt_path) != final['sha256']:
            raise RuntimeError('Final prompt mismatch')
    if args.phase not in ('pilot','dev','test'):
        raise RuntimeError('Choose phase or --report-only')
    subset = [r for r in items if r['split'] == ('test' if args.phase=='test' else 'dev')]
    cache_path = OUT/(f'test_{version}.jsonl' if args.phase=='test' else f'prompt_{version}.jsonl')
    previous = cached(cache_path)
    if args.phase == 'dev' and not (OUT/'pilot_check.json').exists():
        raise RuntimeError('Run and verify 5-item pilot first')
    if args.phase == 'pilot':
        if args.version != 1:
            raise RuntimeError('Pilot only v1')
        subset = subset[:5]
    if not set(r['id'] for r in previous) <= {r['id'] for r in items if r['split']==('test' if args.phase=='test' else 'dev')}:
        raise RuntimeError('Wrong split cache')
    todo = [r for r in subset if r['id'] not in predictions(previous)]
    if todo:
        judge = Judge(cache_path)
        try:
            with ThreadPoolExecutor(max_workers=1 if args.phase=='pilot' else 4) as pool:
                tasks = {pool.submit(judge.evaluate, prompt_path.read_text(), item, previous): item['id'] for item in todo}
                for task in as_completed(tasks):
                    task.result()
                    print(f'{args.phase} {tasks[task]} cached', flush=True)
        finally:
            judge.close()
    rows = cached(cache_path)
    pred = predictions(rows)
    if args.phase == 'pilot':
        if any(pred[r['id']] is None for r in subset):
            raise RuntimeError('Pilot parsing failed')
        if not (OUT/'pilot_check.json').exists():
            write(OUT/'pilot_check.json', dict(ids=[r['id'] for r in subset], parsed=5, usage_verified=True, model=MODEL, checked_at=datetime.now(timezone.utc).isoformat()))
    if args.phase in ('dev','test'):
        summary = {d:metric(subset,pred,d) for d in ('topic','emotion')}
        print(json.dumps(summary,ensure_ascii=False))
        write(OUT/f'{"test" if args.phase=="test" else "dev"}_{version}_summary.json', summary) if not (OUT/f'{"test" if args.phase=="test" else "dev"}_{version}_summary.json').exists() else None


if __name__ == '__main__':
    main()
