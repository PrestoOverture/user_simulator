"""Contract C: cached Spark evaluation and deterministic offline PPI reports."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from usersim.judge_spark import (SparkJudge, frozen_prompt, read_cache, PARSER_VERSION,
    judge_batch, JudgeStopped, write_stop, cache_writer, _write_json)
from scripts.validate_judge import frozen, metric

OUT = ROOT/'outputs/spark_judge_validation'
PREREGISTRATION = dict(seed=20261027, sample_dialogues=[50,100,200], repetitions=500,
    bootstrap_replicates=2000, coverage_min=.90, median_width_reduction_min=.20,
    adoption_n=100, selection='highest development topic kappa; tie: disabled',
    bootstrap='paired dialogue percentile bootstrap; Spark population fixed',
    parse_failure_stop=.02)


def dump(path, value):
    _write_json(path,value)


def initialize():
    OUT.mkdir(exist_ok=True)
    path = OUT/'summary.json'
    if path.exists():
        if json.loads(path.read_text())['preregistration'] != PREREGISTRATION:
            raise RuntimeError('Preregistration changed')
    else:
        dump(path,dict(preregistration=PREREGISTRATION,status='registered before requests'))


def responses(*, allow_pending=False):
    return [r for p in sorted(OUT.glob('*.jsonl')) for r in read_cache(p,allow_pending=allow_pending) if r['event']=='response']


def safety(*, allow_pending=False):
    rows = responses(allow_pending=True) if allow_pending else responses()
    terminal = [r for r in rows if r['status'] in ('ok','parse_error','moderated')]
    failures = sum(r['status']=='parse_error' for r in terminal)
    if terminal and failures/len(terminal) > .02:
        raise JudgeStopped('parse_failure_rate',f'Parse failures {failures}/{len(terminal)} exceed 2%; stop')


def run_batch(name, items, thinking, prompt, concurrency=1):
    path = OUT/(name+'.jsonl')
    if name == 'full':
        counts = Counter(r['status'] for r in responses())
        def completed(row):
            counts[row['status']] += 1
            print(f'{name} {row["id"]} {row["status"]}',flush=True)
            total = sum(counts[s] for s in ('ok','parse_error','moderated'))
            if counts['parse_error']/total > .02:
                raise JudgeStopped('parse_failure_rate',f'Parse failures {counts["parse_error"]}/{total} exceed 2%; stop')
        judge_batch(path,items,concurrency,prompt=prompt,thinking=thinking,on_result=completed)
        return
    previous = read_cache(path)
    done = {r['id'] for r in previous if r.get('status') in ('ok','moderated','parse_error')}
    if not done <= {r['id'] for r in items}:
        raise RuntimeError('Wrong split in cache')
    previous_by_id = {}
    for row in previous:
        previous_by_id.setdefault(row['id'],[]).append(row)
    status_counts = Counter(r['status'] for r in responses())
    client = None
    try:
        for item in items:
            if item['id'] in done:
                # Verify cached fingerprints without sending requests.
                from usersim.judge import input_sha256
                if any(r['input_sha256']!=input_sha256(prompt,item) or r['thinking']!=thinking for r in previous_by_id[item['id']]):
                    raise RuntimeError('Cached input mismatch')
                continue
            if client is None:
                client = SparkJudge(path,thinking)
            row = client.evaluate(prompt,item,previous_by_id.get(item['id'],()))
            print(f'{name} {item["id"]} {row["status"]}',flush=True)
            status_counts[row['status']] += 1
            total = sum(status_counts[s] for s in ('ok','parse_error','moderated'))
            if status_counts['parse_error']/total > .02:
                raise RuntimeError(f'Parse failures {status_counts["parse_error"]}/{total} exceed 2%; stop')
    finally:
        if client:
            client.close()


def scores(name,items):
    rows = [r for r in read_cache(OUT/(name+'.jsonl'),allow_pending=True) if r['event']=='response' and r['status'] in ('ok','parse_error','moderated')]
    by_id = {r['id']:r for r in rows}
    if set(by_id) != {r['id'] for r in items}:
        return None
    valid = [r for r in items if by_id[r['id']]['status']!='moderated']
    pred = {r['id']:by_id[r['id']].get('prediction') for r in valid}
    return {d:metric(valid,pred,d) for d in ('topic','emotion')}


def comparison_inputs(prompt):
    """Reconstruct only inputs with successful existing Gemini labels; no client."""
    from usersim.csds import load
    from usersim.judge import input_sha256
    from scripts.validate_judge import cached
    from scripts.measure_reaction import judge_item, active_responses, predictions
    result = []
    reaction = ROOT/'outputs/reaction/full'
    run = json.loads((reaction/'run.json').read_text())
    generated = {r['request_id']:r for r in map(json.loads,(Path(run['sim_dir'])/'generations.jsonl').read_text().splitlines())}
    by_dialogue = {r['DialogueID']:r for r in load()}
    previous = active_responses(reaction,cached(reaction/'judgments.jsonl',allow_request_errors=True))
    gemini = predictions(previous)
    fingerprints = {r['id']:r['input_sha256'] for r in previous if r['status']=='ok' and 'input_sha256' in r}
    for group, turns in run['manifest']['groups'].items():
        for turn in turns:
            rid = f"{turn['dialogue_id']}:{turn['turn']}"
            for source in ('human','simulator'):
                old_id = source+':'+rid
                if gemini.get(old_id) is None:
                    continue
                item = dict(id=old_id,**judge_item(by_dialogue[turn['dialogue_id']],turn['turn'],
                    generated[rid]['text'] if source=='simulator' else None))
                fingerprint = input_sha256(prompt,item)
                if old_id in fingerprints and fingerprints[old_id]!=fingerprint:
                    raise RuntimeError('Reaction Gemini input hash mismatch')
                result.append(dict(dataset='reaction/full',condition=source,group=group,
                    dialogue_id=turn['dialogue_id'],turn=turn['turn'],gemini=gemini[old_id],
                    item=dict(item,id=fingerprint)))
    correction = ROOT/'outputs/correction_v2'
    inputs = json.loads((correction/'judge_inputs_500.json').read_text())
    labels = json.loads((correction/'reused_judgments.json').read_text())
    pred = {k:v['prediction'] for k,v in labels.items()}
    pred.update(predictions(cached(correction/'judgments.jsonl',allow_request_errors=True)))
    paired = json.loads((correction/'paired_judgments_500.json').read_text())
    expected = {c+':'+r['request_id']:v for r in paired for c,v in r['predictions'].items()}
    for item in inputs:
        fingerprint = input_sha256(prompt,item)
        label = pred.get(fingerprint)
        if label is None:
            continue
        if expected.get(item['id']) != label:
            raise RuntimeError('Correction paired Gemini labels mismatch')
        source,did,turn = item['id'].split(':')
        result.append(dict(dataset='correction_v2',condition=source,group='all',
            dialogue_id=int(did),turn=int(turn),gemini=label,item=dict(item,id=fingerprint)))
    path = OUT/'comparison_inputs.json'
    if path.exists():
        if json.loads(path.read_text())!=result:
            raise RuntimeError('Comparison input snapshot changed')
    else:
        dump(path,result)
    return result


def grouped_test(items):
    metadata = {r['id']:r for r in json.loads((ROOT/'outputs/judge_validation/source_map.json').read_text())['items']}
    rows = {r['id']:r for r in read_cache(OUT/'test.jsonl',allow_pending=True) if r.get('status') in ('ok','parse_error','moderated')}
    subset = [r for r in items if r['split']=='test']
    if set(rows)!={r['id'] for r in subset}:
        return None
    subset = [r for r in subset if rows[r['id']]['status']!='moderated']
    pred = {r['id']:rows[r['id']].get('prediction') for r in subset}
    groups = [('all',subset)]
    groups += [(s,[r for r in subset if metadata[r['id']]['source']==s]) for s in ('human','spark','deepseek')]
    groups += [(f'negative={b}',[r for r in subset if metadata[r['id']]['negative']==b]) for b in (False,True)]
    groups += [(f'{s}/negative={b}',[r for r in subset if metadata[r['id']]['source']==s and metadata[r['id']]['negative']==b]) for s in ('human','spark','deepseek') for b in (False,True)]
    result = {}
    for name,group in groups:
        valid = [r for r in group if pred[r['id']] is not None]
        bias = {}
        for d in ('topic','emotion'):
            hit = lambda p:p['topic']=='continue' if d=='topic' else p['emotion']
            bias[d] = sum(hit(pred[r['id']])-hit(r['annotation']) for r in valid)/len(valid) if valid else None
        result[name] = dict(scores={d:metric(group,pred,d) for d in ('topic','emotion')},self_bias=bias,
                            bias_valid_n=len(valid),parse_error_n=len(group)-len(valid))
    return result


def agreement_and_pairs(entries):
    rows = {r['id']:r for r in read_cache(OUT/'full.jsonl',allow_pending=True) if r.get('status') in ('ok','parse_error','moderated')}
    groups = {}
    pairs = {}
    for entry in entries:
        key = (entry['dataset'],entry['condition'],entry['group'])
        groups.setdefault(key,[]).append(entry)
        pairkey = (entry['dataset'],entry['dialogue_id'],entry['turn'],entry['group'])
        pair = pairs.setdefault(pairkey,dict(dialogue_id=entry['dialogue_id'],turn=entry['turn'],
                    group=entry['group'],spark={},gemini={}))
        pair['gemini'][entry['condition']] = entry['gemini']
        row = rows.get(entry['item']['id'])
        if row and row['status']=='ok':
            pair['spark'][entry['condition']] = row['prediction']
    agreement = []
    for key,group in sorted(groups.items()):
        evaluated = [e for e in group if e['item']['id'] in rows and rows[e['item']['id']]['status']!='moderated']
        items = [dict(id=e['item']['id'],annotation=e['gemini']) for e in evaluated]
        pred = {e['item']['id']:rows[e['item']['id']].get('prediction') for e in evaluated}
        scores_ = {d:metric(items,pred,d) for d in ('topic','emotion')}
        if not items:
            for score in scores_.values():
                score.update(accuracy=None,kappa=None)
        agreement.append(dict(dataset=key[0],source=key[1],group=key[2],n_inputs=len(group),
            n_pending=sum(e['item']['id'] not in rows for e in group),
            n_moderated=sum(rows.get(e['item']['id'],{}).get('status')=='moderated' for e in group),
            scores=scores_))
    output = {}
    for dataset,conditions in [('reaction/full',('human','simulator')),('correction_v2',('human','baseline','situation','structure','combined'))]:
        allpairs = [p for k,p in pairs.items() if k[0]==dataset]
        valid = [p for p in allpairs if all(c in p['spark'] and c in p['gemini'] for c in conditions)]
        output[dataset] = dict(pairs=valid,original_pairs=len(allpairs),excluded_pairs=len(allpairs)-len(valid))
    return agreement,output


def run_ppi(entries):
    from usersim.ppi import simulate
    agreement,datasets = agreement_and_pairs(entries)
    if any(r['n_pending'] for r in agreement):
        raise RuntimeError('Full Spark cache incomplete')
    for dataset,conditions in [('reaction/full',['simulator']),('correction_v2',['baseline','situation','structure','combined'])]:
        data = datasets[dataset]
        for n in PREREGISTRATION['sample_dialogues']:
            path = OUT/f"ppi_{dataset.replace('/','_')}_{n}.json"
            if path.exists():
                continue
            print(f'PPI {dataset} n={n} started',flush=True)
            reference = None
            if dataset=='reaction/full':
                from scripts.measure_reaction import summarize
                reference = summarize([dict(p,human=p['gemini']['human'],simulator=p['gemini']['simulator']) for p in data['pairs']])['decision']
            result = simulate(data['pairs'],conditions,n,PREREGISTRATION['seed'],
                PREREGISTRATION['repetitions'],PREREGISTRATION['bootstrap_replicates'],dataset=='reaction/full',reference)
            result['excluded_pairs'] = data['excluded_pairs']
            result['original_pairs'] = data['original_pairs']
            if dataset=='reaction/full':
                original = json.loads((ROOT/'outputs/reaction/full/summary.json').read_text())['statistics']['decision']
                result['original_full_decision'] = original
                result['original_full_decision_agreement'] = sum(r['decision']==original for r in result['interactions'])/len(result['interactions'])
            dump(path,result)
            print(f'PPI {dataset} n={n} finished',flush=True)


def report(items):
    data = dict(preregistration=PREREGISTRATION,status='incomplete')
    data['prompt_sha256'] = hashlib.sha256(frozen_prompt(ROOT).encode()).hexdigest()
    data['development'] = {v:scores('dev_'+v,[r for r in items if r['split']=='dev']) for v in ('disabled','enabled')}
    data['development_progress'] = {}
    development_items = {r['id']:r for r in items if r['split']=='dev'}
    for variant in ('disabled','enabled'):
        cached_rows = [r for r in read_cache(OUT/f'dev_{variant}.jsonl',allow_pending=True)
                       if r.get('status') in ('ok','parse_error','moderated')]
        evaluated = [development_items[r['id']] for r in cached_rows if r['status']!='moderated']
        pred = {r['id']:r.get('prediction') for r in cached_rows}
        data['development_progress'][variant] = dict(expected=100,evaluated=len(cached_rows),
            statuses=dict(Counter(r['status'] for r in cached_rows)),
            partial_scores={d:metric(evaluated,pred,d) for d in ('topic','emotion')} if evaluated else None)
    selection = OUT/'selection.json'
    if selection.exists():
        data['selection'] = json.loads(selection.read_text())
        data['test'] = grouped_test(items)
    rows = responses(allow_pending=True)
    reparsed = [r for r in rows if 'cached_original_status' in r]
    data['parser'] = dict(version=PARSER_VERSION, cached_responses_reparsed=len(reparsed),
        recovered_parse_errors=sum(r['cached_original_status']=='parse_error' and r['status']=='ok' for r in reparsed),
        original_parse_errors=sum(r['cached_original_status']=='parse_error' for r in reparsed))
    usage = Counter()
    for r in rows:
        u = r.get('usage') or {}
        for k in ('prompt_tokens','completion_tokens','total_tokens'):
            usage[k] += u.get(k,0)
        usage['reasoning_tokens'] += (u.get('completion_tokens_details') or {}).get('reasoning_tokens',u.get('reasoning_tokens',0))
    data['reasoning_tokens_missing_responses'] = sum(
        r['thinking']=='enabled' and r.get('usage') is not None
        and 'reasoning_tokens' not in r['usage']
        and 'reasoning_tokens' not in (r['usage'].get('completion_tokens_details') or {}) for r in rows)
    data['usage'] = dict(usage)
    if data['reasoning_tokens_missing_responses']:
        data['usage']['reasoning_tokens'] = None
    data['statuses'] = dict(Counter(r['status'] for r in rows))
    terminal = sum(r['status'] in ('ok','moderated','parse_error') for r in rows)
    data['parse_failure_rate'] = data['statuses'].get('parse_error',0)/terminal if terminal else 0
    data['usage_missing'] = sum(r.get('usage') is None for r in rows)
    entries_path = OUT/'comparison_inputs.json'
    data['agreement'] = []
    if entries_path.exists():
        entries = json.loads(entries_path.read_text())
        data['agreement'],datasets = agreement_and_pairs(entries)
        data['paired_support'] = {k:{f:v for f,v in d.items() if f!='pairs'} for k,d in datasets.items()}
        data['input_counts'] = dict(Counter(r['dataset'] for r in entries))
        data['unique_comparison_inputs'] = len({r['item']['id'] for r in entries})
        moderated_ids = {r['id'] for r in rows if r['status']=='moderated'}
        data['moderated_inputs'] = [dict(dataset=e['dataset'],condition=e['condition'],dialogue_id=e['dialogue_id'],turn=e['turn'])
            for e in entries if e['item']['id'] in moderated_ids] if data['statuses'].get('moderated') else []
    data['ppi'] = {}
    for path in sorted(OUT.glob('ppi_*.json')):
        v = json.loads(path.read_text())
        data['ppi'][path.stem] = {k:v for k,v in v.items() if k!='interactions' and k!='statistics'}
        data['ppi'][path.stem]['statistics'] = [{k:v for k,v in row.items() if k!='replicates'} for row in v['statistics']]
    complete = (data.get('test') is not None and len(data['ppi'])==6 and bool(data['agreement'])
                and all(r['n_pending']==0 for r in data['agreement']))
    n100 = [row for p in data['ppi'].values() for row in p['statistics'] if row['n']==100]
    data['adoption_passed'] = all(r['coverage']>=.90 and r['median_width_reduction'] is not None
        and r['median_width_reduction']>=.20 for r in n100) if complete else None
    if complete:
        data['status'] = 'complete'
    if data['parse_failure_rate']>.02:
        data['status'] = 'stopped: parse failure rate exceeds 2%'
    lines = ['# Spark 判断器验证（合同 C）','',f"状态：{data['status']}。仅调用 Spark；离线报告不创建客户端。",
        '预先登记：n=100，在所有数据集/条件/分组的 continue 与情绪差距上覆盖率≥90%，区间宽度中位数较仅 Gemini 缩小≥20%。n=50/100/200，各500次；按对话 bootstrap 2000次；种子20261027。',
        'thinking enabled 是按合同要求对 ADR-012 默认关闭规则的例外；temperature 省略，使用官方默认。Spark 测试集 κ 只报告，不设门槛。',
        'API 参数已核对[讯飞官方 Chat 文档](https://maas.xfyun.cn/doc/guide/3%E3%80%81API%20%E6%8E%A5%E5%8F%A3/3.1%20%E6%98%9F%E7%81%AB%E6%A8%A1%E5%9E%8B%20API/3.1.1%20Chat.html)。',
        f"冻结 prompt SHA-256：{data['prompt_sha256']}。请求只包含 goal/history/previous_agent/text，来源、分组和标注均不进入请求。",
        '', '## 变体选择（仅开发集100轮）','', '|thinking|指标|准确率|κ|', '|---|---|---:|---:|']
    for v,scores_ in data['development'].items():
        if scores_:
            for d,r in scores_.items():
                lines.append(f"|{v}|{d}|{r['accuracy']:.6f}|{r['kappa']}|")
        else:
            partial = data['development_progress'][v]
            if partial['partial_scores']:
                for d,r in partial['partial_scores'].items():
                    lines.append(f"|{v}（仅{partial['evaluated']}/100，不可选型）|{d}|{r['accuracy']:.6f}|{r['kappa']}|")
            else:
                lines.append(f'|{v}|尚未完成|NA|NA|')
    lines += ['', '### 开发进度与停止证据', '', '|thinking|已返回/计划|成功|解析失败|moderated|', '|---|---:|---:|---:|---:|']
    for variant,progress in data['development_progress'].items():
        counts = progress['statuses']
        lines.append(f"|{variant}|{progress['evaluated']}/100|{counts.get('ok',0)}|{counts.get('parse_error',0)}|{counts.get('moderated',0)}|")
    lines += ['', '### Delta Contract：共用解析器与缓存重解析', '',
        '两个变体均允许剥掉首尾空白、成对的 ** 或 *、以及 ```json ... ``` 外层代码块；剩余内容仍须恰好是一个合法 JSON 对象，字段及取值范围不变，其他多余文本仍为解析失败。',
        f"共离线重新解析 {data['parser']['cached_responses_reparsed']} 条已有缓存响应，其中原解析失败 {data['parser']['original_parse_errors']} 条，恢复成功 {data['parser']['recovered_parse_errors']} 条；没有重新请求，原始缓存字节保留。2%停止线按新解析器计算。",
        '原严格解析的停点见 stop_evidence.json；该文件为历史记录，现行失败比例以本报告及新解析器为准。']
    if data['parse_failure_rate']>.02:
        lines += ['', '新解析器的解析失败比例已超过2%，按合同停止后续请求。阶段完成情况见进度表；未完成的测试或全量/PPI结果不能用于判定采纳标准。没有重试内容或改写 prompt。']
    lines += ['',f"选定变体：{data.get('selection',{}).get('thinking','尚未选定')}。话题 κ 相同则选 disabled；测试只评估选定变体一次，缓存复用不发请求。",
        '', '## 测试集200轮：准确率与 κ', '', '|分组|指标|n|准确率|κ|', '|---|---|---:|---:|---:|']
    for group,value in (data.get('test') or {}).items():
        for d,r in value['scores'].items():
            lines.append(f"|{group}|{d}|{r['n']}|{r['accuracy']:.6f}|{r['kappa']}|")
    lines += ['', '## 自偏差（Spark 比例 − 人工比例）','',
        '在各来源共同有效轮次计算；解析失败计入准确率及 κ 的错误类别，但不虚构其 continue/情绪标签。moderated 剔除。',
        '', '|来源/分组|有效n|continue 偏差|情绪偏差|解析失败n|','|---|---:|---:|---:|---:|']
    for group,value in (data.get('test') or {}).items():
        bias = value['self_bias']
        lines.append(f"|{group}|{value['bias_valid_n']}|{bias['topic']}|{bias['emotion']}|{value['parse_error_n']}|")
    lines += ['', '## 与 Gemini 的一致率和 κ', '',
        f"输入数量：{data.get('input_counts',{})}；去重后的独立输入：{data.get('unique_comparison_inputs',0)}。相同内容复用 Spark 判断。",
        '', '|数据集|来源/条件|分组|指标|有效n|一致率|κ|未完成|moderated|', '|---|---|---|---|---:|---:|---:|---:|---:|']
    for r in data['agreement']:
        for d,m in r['scores'].items():
            accuracy = f"{m['accuracy']:.6f}" if m['n'] else 'NA'
            kappa = m['kappa'] if m['n'] else 'NA'
            lines.append(f"|{r['dataset']}|{r['source']}|{r['group']}|{d}|{m['n']}|{accuracy}|{kappa}|{r['n_pending']}|{r['n_moderated']}|")
    lines += ['', '## PPI 公式与抽样', '',
        '每个数据集独立抽样；M 为全量有效对话数，N_g 为组 g 全量配对轮次。d^S、d^G 分别是同一轮次模拟器指标减真人指标，按对话汇总为 D^S、D^G。',
        'PPI 点估计：Δ̂ = Σ全量 D^S / N_g + M/(n N_g) Σ抽样对话 (D^G − D^S)。只用 Gemini：Δ̂_G = M/(n N_g) Σ抽样对话 D^G。',
        '这是以已知全量轮次分母归一化的 Horvitz–Thompson 估计；均匀无放回抽对话时，配对矫正项使点估计精确设计无偏，即使对话长度不同。样本估计可能超出比例差的范围，不截断。',
        '每次抽取 n 段对话（同一数据集各条件/指标共用样本），再有放回重采样这些完整对话 2000 次，Spark 全量项及 N_g 固定，取2.5%/97.5%百分位。未加有限总体修正，区间可能保守。n=50/100/200 各重复500次。',
        '所有指标在同一批配对轮次上计算；任一侧 moderated 或解析失败的轮次从配对分析剔除。correction_v2 沿用已有五侧共同有效配对池，再剔除 Spark 不完整配对。Gemini 全量参照在该支持集重算；原始支持集和剔除数如下。',
        json.dumps(data.get('paired_support',{}),ensure_ascii=False),
        '', '## PPI 覆盖率与区间宽度', '',
        '采纳标准的宽度缩小=1−PPI区间宽度中位数/Gemini-only区间宽度中位数。另在 summary 中报告逐次配对相对缩小的中位数；该辅助指标遇 Gemini-only宽度为0时记缺失并单列。',
        '', '|数据集|条件|组|指标|n|覆盖率|PPI中位宽度|Gemini中位宽度|中位缩小|零宽样本数|抽样轮次中位数|', '|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,p in data['ppi'].items():
        for r in p['statistics']:
            lines.append(f"|{name}|{r['condition']}|{r['group']}|{r['metric']}|{r['n']}|{r['coverage']:.3f}|{r['ppi_median_width']:.6f}|{r['gemini_only_median_width']:.6f}|{r['median_width_reduction']}|{r['zero_gemini_width']}|{r['median_sampled_turns']}|")
    lines += ['', '## reaction/full 交互项判定一致性', '',
        '按 ADR-016：continue 的负面组差距 − 非负面组差距区间不含0，且负面组差距绝对值更大，才限定在被拒之后。两组按同一对话重采样。情绪不参与判定。']
    for name,p in data['ppi'].items():
        if name.startswith('ppi_reaction'):
            lines.append(f"- {name}：共同支持集全量判定={p['full_decision']}；原报告={p.get('original_full_decision')}；与共同支持集一致比例={p['decision_agreement']}；与原报告一致比例={p.get('original_full_decision_agreement')}。逐次交互项点估计、区间和判定见 {name}.json。")
    lines += ['', '## 预先登记采纳标准', '',f"是否达标：{data['adoption_passed'] if complete else '尚不能判定（未完成）'}。不根据结果调整标准、n 或统计方法。",
        '', '## 解析失败、moderated 与 Spark token', '',
        f"响应状态：{data['statuses']}；解析失败比例={data['parse_failure_rate']:.6%}（分母为返回内容或审核拒绝的终结响应）；超过2%立即停止，不重试内容、不改 prompt。",
        f"实际返回 token：{data['usage']}；缺失 usage 请求数={data['usage_missing']}；enabled 有 {data['reasoning_tokens_missing_responses']} 个响应未返回推理 token 明细，reasoning_tokens 记 null 而非0。reasoning_tokens 为 completion_tokens 子项，不重复加总；缺失用量不视为0，完整计费量在有缺失时未知。",
        f"moderated 输入：{data.get('moderated_inputs',[])}。10013 不重试；配对两侧剔除，存在内容选择偏差。",
        '', '逐条判断及实际 usage 在 dev_disabled.jsonl、dev_enabled.jsonl、test.jsonl、full.jsonl；输入映射在 comparison_inputs.json；所有 PPI 模拟逐次结果在 ppi_*.json。']
    dump(OUT/'summary.json',data)
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')


def verify_full_delivery():
    """Offline acceptance, once the full Spark and PPI pipeline has succeeded."""
    import os
    import subprocess
    env = dict(os.environ)
    for key in ('SPARK_API_KEY','SANDBOX_API_KEY'):
        env.pop(key,None)
    checks = {}
    hashes = []
    for number in (1,2):
        with (OUT/f'automatic_report_{number}.log').open('w') as log:
            result = subprocess.run([str(ROOT/'.venv/bin/python'),str(Path(__file__)),
                '--report-only'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        checks[f'report_{number}'] = result.returncode
        hashes.append({name:hashlib.sha256((OUT/name).read_bytes()).hexdigest()
                       for name in ('report.md','summary.json')})
        (OUT/f'final_sha256_{number}.txt').write_text(''.join(
            f'{digest}  {name}\n' for name,digest in hashes[-1].items()))
    checks['report_hashes_identical'] = hashes[0]==hashes[1]
    commands = {
        'pytest':[str(ROOT/'.venv/bin/python'),'-m','pytest','-q'],
        'compile':[str(ROOT/'.venv/bin/python'),'-m','py_compile','usersim/judge_spark.py',
                   'usersim/ppi.py','scripts/validate_spark_judge.py',
                   'tests/test_spark_judge.py','tests/test_ppi.py'],
        'protected_diff':['git','diff','--exit-code','--','docs','usersim/judge.py','usersim/spark.py'],
    }
    for name,command in commands.items():
        with (OUT/f'automatic_{name}.log').open('w') as log:
            checks[name] = subprocess.run(command,cwd=ROOT,env=env,
                stdout=log,stderr=subprocess.STDOUT).returncode
    paths = [ROOT/name for name in ('usersim/judge_spark.py','usersim/ppi.py',
        'scripts/validate_spark_judge.py','scripts/run_spark_judge_full.sh',
        'tests/test_spark_judge.py','tests/test_ppi.py')]
    paths += [p for p in OUT.rglob('*') if p.is_file()]
    import re
    secrets = [os.environ[k].encode() for k in ('SPARK_API_KEY','SANDBOX_API_KEY')
               if os.environ.get(k)]
    hits = []
    for path in paths:
        value = path.read_bytes()
        if any(secret in value for secret in secrets) or re.search(rb'(?:sk-|ak-)[A-Za-z0-9_-]{24,}',value):
            hits.append(str(path.relative_to(ROOT)))
    checks['credential_hits'] = hits
    summary = json.loads((OUT/'summary.json').read_text())
    checks['complete_summary'] = (summary['status']=='complete'
        and len(summary.get('ppi',{}))==6
        and all(r['n_pending']==0 for r in summary.get('agreement',[])))
    passed = (all(checks[k]==0 for k in ('report_1','report_2','pytest','compile','protected_diff'))
              and checks['report_hashes_identical'] and not hits and checks['complete_summary'])
    result = dict(passed=passed,checks=checks,sha256=hashes)
    _write_json(OUT/'automatic_verification.json',result)
    return result


def supervise_full(concurrency):
    """Restart unexpected exits, preserve terminal stop conditions, then verify."""
    import os
    import subprocess
    import time
    cache = OUT/'full.jsonl'
    stop_path = Path(str(cache)+'.stop.json')
    runtime_path = Path(str(cache)+'.runtime.json')
    state_path = OUT/'supervisor_state.json'
    with cache_writer(OUT/'full_supervisor'):
        prior = json.loads(state_path.read_text()) if state_path.exists() else {}
        started = prior.get('started_at',time.time()) if prior.get('stage') in ('running','restarting','waiting_worker') else time.time()
        restarts = prior.get('restarts',0)
        old_worker = prior.get('worker_pid')
        def state(stage,**extra):
            value = dict(stage=stage,started_at=started,observed_at=time.time(),
                supervisor_pid=os.getpid(),restarts=restarts,**extra)
            _write_json(state_path,value)
        while True:
            if stop_path.exists():
                reason = json.loads(stop_path.read_text())
                state('stopped',stop=reason)
                print(f'Supervisor stopping: {reason}',flush=True)
                return 3
            runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
            if (not runtime.get('batch_complete')
                    and time.time()-runtime.get('last_success_wall_time',started)>=3600):
                write_stop(cache,'no_success_60_minutes','No successful Spark response for 60 minutes')
                continue
            # A supervisor crash must not duplicate a still-running old worker/PPI job.
            if old_worker:
                old = subprocess.run(['ps','-p',str(old_worker),'-o','args='],capture_output=True,text=True)
                if old.returncode==0 and 'validate_spark_judge.py --phase full' in old.stdout:
                    state('waiting_worker',worker_pid=old_worker)
                    time.sleep(5)
                    continue
                old_worker = None
            try:
                safety(allow_pending=True)
            except JudgeStopped as exc:
                write_stop(cache,exc.reason,str(exc))
                continue
            command = [str(ROOT/'.venv/bin/python'),'-u',str(Path(__file__)),
                       '--phase','full','--concurrency',str(concurrency)]
            worker = subprocess.Popen(command,cwd=ROOT)
            state('running',worker_pid=worker.pid,command=command)
            result = worker.wait()
            if stop_path.exists():
                continue
            if result==0:
                if json.loads((OUT/'summary.json').read_text())['status']!='complete':
                    write_stop(cache,'incomplete_delivery','Worker returned success without a complete report')
                    continue
                state('verifying')
                verification = verify_full_delivery()
                state('complete' if verification['passed'] else 'checks_failed',verification=verification)
                return 0 if verification['passed'] else 1
            restarts += 1
            state('restarting',worker_exit_code=result)
            print(f'Unexpected full-worker exit {result}; resume from cache in 5 seconds',flush=True)
            time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report-only',action='store_true')
    ap.add_argument('--supervise-full',action='store_true')
    ap.add_argument('--phase',choices=['dev','test','full'],default='dev')
    ap.add_argument('--concurrency',type=int,choices=range(1,9),default=8,
                    help='Initial full-phase concurrency; adaptive range 1..8')
    args = ap.parse_args()
    prompt = frozen_prompt(ROOT)
    items = frozen()
    initialize()
    if args.report_only:
        report(items)
        return
    if args.supervise_full:
        raise SystemExit(supervise_full(args.concurrency))
    safety(allow_pending=args.phase=='full')
    try:
        if args.phase=='dev':
            dev = [r for r in items if r['split']=='dev']
            for variant in ('disabled','enabled'):
                run_batch('dev_'+variant,dev,variant,prompt)
            result = {v:scores('dev_'+v,dev) for v in ('disabled','enabled')}
            chosen = max(('disabled','enabled'),key=lambda v:result[v]['topic']['kappa'])
            value = dict(thinking=chosen,development=result)
            path = OUT/'selection.json'
            if path.exists() and json.loads(path.read_text())!=value:
                raise RuntimeError('Selection immutable')
            dump(path,value)
        elif args.phase=='test':
            chosen = json.loads((OUT/'selection.json').read_text())['thinking']
            run_batch('test',[r for r in items if r['split']=='test'],chosen,prompt)
        else:
            chosen = json.loads((OUT/'selection.json').read_text())['thinking']
            entries = comparison_inputs(prompt)
            unique = {r['item']['id']:r['item'] for r in entries}
            run_batch('full',list(unique.values()),chosen,prompt,args.concurrency)
            run_ppi(entries)
    finally:
        report(items)


if __name__=='__main__':
    try:
        main()
    except JudgeStopped as exc:
        write_stop(OUT/'full.jsonl',exc.reason,str(exc))
        print(f'Spark stop condition: {exc}',flush=True)
        raise SystemExit(3)
