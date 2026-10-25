"""Frozen lexical behavior proxies. No model or network dependencies."""
import json
import random
import re
from pathlib import Path
from .csds import clean_summary, user_turns

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / 'config/behaviors.json').read_text())
LABELS = dict(opening_transfer='开场转人工', escalation='广义升级（不含开场转人工）',
              acceptance='未解决接受', alternative='换方案/放弃', emotion='情绪说出来',
              exclamation='感叹号', narrative='叙事加料', behavioral='情绪体现在行为上',
              negative_agent='客服负面回应')


def normalize(text):
    return re.sub(r'\s+', '', text or '')


def negative_agent(text, rules=None):
    rules = RULES if rules is None else rules
    text = normalize(text)
    hit = bool(re.search(rules['negative_agent'], text))
    excluded = rules.get('negative_agent_exclude') and re.search(rules['negative_agent_exclude'], text)
    strong = rules.get('negative_agent_strong') and re.search(rules['negative_agent_strong'], text)
    return bool(hit and (not excluded or strong))


def classify(text, previous_agent='', goal='', history='', first=False, rules=None):
    rules = RULES if rules is None else rules
    text = normalize(text)
    compact = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
    negative = negative_agent(previous_agent, rules)
    opening_text = re.sub(rules.get('opening_exclude', r'(?!)'), '', text)
    opening = first and bool(re.search(rules['opening_transfer'], opening_text))
    # An opening transfer alone is separate; other escalation language still counts.
    escalation_text = re.sub(rules['opening_transfer'], '', text) if first else text
    escalation_text = re.sub(rules.get('escalation_exclude', r'(?!)'), '', escalation_text)
    acceptance = negative and bool(re.fullmatch(rules['acceptance'], compact))
    acceptance = acceptance or bool(negative and rules.get('acceptance_prefix') and re.search(rules['acceptance_prefix'], text))
    alternative = bool(re.search(rules['alternative'], text))
    if text in ('不用了', '不用了谢谢') and rules.get('alternative_closing_agent') and re.search(rules['alternative_closing_agent'], normalize(previous_agent)):
        alternative = False
    narrative_text = re.sub(rules.get('narrative_exclude', r'(?!)'), '', text)
    emotion_text = re.sub(rules.get('emotion_exclude', r'(?!)'), '', text)
    new_terms = [term for term in rules['narrative_terms']
                 if term in narrative_text and term not in normalize(goal + history)]
    return dict(opening_transfer=int(opening),
                escalation=int(bool(re.search(rules['escalation'], escalation_text))),
                acceptance=int(acceptance), alternative=int(alternative),
                emotion=int(bool(re.search(rules['emotion'], emotion_text))),
                exclamation=int(bool(re.search('[!！]', text))), narrative=int(bool(new_terms)),
                behavioral=int(negative and (0 < len(compact) <= 5 or acceptance or alternative)),
                negative_agent=int(negative))


def contexts(row):
    goal, _ = clean_summary(row['UserSumm'], row['DialogueID'])
    for t, turn in enumerate(user_turns(row), 1):
        prefix = row['Dialogue'][:turn['start']]
        agent = next((m['utterance'] for m in reversed(prefix) if m['speaker'] != 'Q'), '')
        yield dict(dialogue_id=row['DialogueID'], turn=t, request_id=f"{row['DialogueID']}:{t}",
                   text='\n'.join(turn['messages']), previous_agent=agent, goal=goal,
                   history='\n'.join(('用户:' if m['speaker'] == 'Q' else '客服:') + m['utterance'] for m in prefix))


def measure(context, text=None, rules=None):
    return classify(context['text'] if text is None else text, context['previous_agent'],
                    context['goal'], context['history'], context['turn'] == 1, rules)


def validation_samples(records):
    """Frozen stratified sample; simulator pool includes all three conditions."""
    samples = []
    for metric in LABELS:
        for side in ('human', 'simulator'):
            pool = [r for r in records if (r['source'] == 'human') == (side == 'human')]
            for hit, n in ((1, 30), (0, 50)):
                eligible = [r for r in pool if r['values'][metric] == hit]
                rng = random.Random(f"{RULES['seed']}:{metric}:{side}:{hit}")
                for r in rng.sample(eligible, min(n, len(eligible))):
                    samples.append(dict(sample_id=f"{metric}/{side}/{hit}/{r['source']}/{r['request_id']}",
                                        metric=metric, side=side, predicted=hit, pool_size=len(eligible),
                                        source=r['source'], **{k:r[k] for k in ('request_id', 'text', 'previous_agent', 'goal', 'history')},
                                        judgment=None, reason=None))
    return samples


def summarize(records, full):
    """Paired dialogue bootstrap, turn-weighted rates and signed gaps."""
    ids = sorted({r['dialogue_id'] for r in records})
    groups = {(r['source'], r['dialogue_id'], r['turn']): r for r in records}
    result = dict(seed=RULES['seed'], replicates=RULES['bootstrap_replicates'],
                  dialogues=len(ids), common_turns=sum(r['source']=='human' for r in records),
                  full_csds={}, comparisons={}, conditional={})
    for metric in LABELS:
        hits = [r for r in full if r['values'][metric]]
        result['full_csds'][metric] = dict(turn_hits=len(hits), turns=len(full),
            turn_rate=len(hits)/len(full), dialogue_hits=len({r['dialogue_id'] for r in hits}),
            dialogues=len({r['dialogue_id'] for r in full}))
    for source in ('baseline', 'C1', 'C2'):
        result['comparisons'][source] = {}
        for metric in LABELS:
            clusters = []
            for did in ids:
                human = [r for r in records if r['source']=='human' and r['dialogue_id']==did]
                h = sum(r['values'][metric] for r in human)
                s = sum(groups[source,did,r['turn']]['values'][metric] for r in human)
                clusters.append((h,s,len(human)))
            n = sum(c[2] for c in clusters)
            rng = random.Random(RULES['seed'])
            gaps = []
            for _ in range(RULES['bootstrap_replicates']):
                draw = rng.choices(clusters, k=len(ids))
                gaps.append(sum(c[1]-c[0] for c in draw)/sum(c[2] for c in draw))
            gaps.sort()
            def quantile(p):
                x=(len(gaps)-1)*p
                i=int(x)
                return gaps[i]+(gaps[min(i+1,len(gaps)-1)]-gaps[i])*(x-i)
            h,s = (sum(c[j] for c in clusters) for j in (0,1))
            result['comparisons'][source][metric] = dict(human=h/n, simulator=s/n,
                human_hits=h, simulator_hits=s, denominator=n, gap=(s-h)/n,
                low=quantile(.025), high=quantile(.975))
    for source in ('human','baseline','C1','C2'):
        eligible = [r for r in records if r['source']==source and r['values']['negative_agent']]
        result['conditional'][source] = dict(denominator=len(eligible), **{
            metric:dict(hits=sum(r['values'][metric] for r in eligible),
                        rate=sum(r['values'][metric] for r in eligible)/len(eligible) if eligible else None)
            for metric in ('emotion','narrative','behavioral')})
    return result


def validation_summary(samples):
    result = {}
    for metric in LABELS:
        result[metric] = {}
        for side in ('human','simulator'):
            groups = {}
            for hit, label in ((1,'false_discovery'),(0,'missed_in_negatives')):
                rows = [s for s in samples if s['metric']==metric and s['side']==side and s['predicted']==hit]
                errors = [s for s in rows if s['judgment'] != bool(hit)]
                groups[label] = dict(errors=len(errors), denominator=len(rows),
                    rate=len(errors)/len(rows) if rows else None,
                    revised_errors=sum(bool(s.get('revised_prediction', s['predicted'])) != s['judgment'] for s in rows),
                    examples=[s['sample_id'] for s in (errors or rows)[:3]])
            result[metric][side] = groups
    return result


def report(summary, validation, samples=()):
    lines = ['# 四类行为补测', '',
        '规则为词汇代理；所有比例分母均为用户轮次，另报全量真人出现对话数。开场转人工与其余升级分别计数；首轮若另含投诉等仍计其余升级。开场指标的轮次分母也包含非首轮。', '',
        f"三条件共同有效 {summary['common_turns']} 轮、{summary['dialogues']} 段。差距为模拟器−真人；按对话配对 bootstrap {summary['replicates']} 次、种子 {summary['seed']}，有放回抽取相同数量对话，保留各对话所有共同有效轮次，95%百分位区间线性插值。区间不包含规则误差。", '',
        '情绪词与感叹号分别计；叙事加料仅比较当前文本与清洗任务目标及严格前缀真实历史。客服负面回应取紧邻用户轮次之前最后一条客服消息。短句按去空白、去标点后的非空字符数≤5判定。', '',
        '## 全量 CSDS 真人（QRole=用户）', '',
        '|指标|命中轮次/全部轮次|轮次率|命中对话/全部对话|', '|---|---:|---:|---:|']
    for metric,label in LABELS.items():
        s=summary['full_csds'][metric]
        lines.append(f"|{label}|{s['turn_hits']}/{s['turns']}|{s['turn_rate']:.6f}|{s['dialogue_hits']}/{s['dialogues']}|")
    lines += ['', '## 共同有效轮次比较', '', '|指标|条件|真人比例|模拟器比例|差距 [95%区间]|', '|---|---|---:|---:|---|']
    for metric,label in LABELS.items():
        for source, values in summary['comparisons'].items():
            s=values[metric]
            lines.append(f"|{label}|{source}|{s['human']:.6f}|{s['simulator']:.6f}|{s['gap']:+.6f} [{s['low']:+.6f}, {s['high']:+.6f}]|")
    lines += ['', '## 客服负面回应之后', '', '|来源|轮次分母|a 情绪说出来|b 叙事加料|c 情绪体现在行为上|', '|---|---:|---:|---:|---:|']
    for source,s in summary['conditional'].items():
        cells = [f"{s[k]['hits']}/{s['denominator']}={s[k]['rate']:.6f}" if s[k]['rate'] is not None else 'NA' for k in ('emotion','narrative','behavioral')]
        lines.append('|'+source+'|'+str(s['denominator'])+'|'+'|'.join(cells)+'|')
    lines += ['', '## 规则验证', '',
        '初版规则和1332条分层样本先冻结、逐条人工式判断，再修规则和首次计算差距；未调用任何模型API。原真人池为500段共同有效轮次，模拟器池合并baseline/C1/C2。最终数量核对时，对真人命中不足30的升级/换方案/叙事三组，从全量CSDS原规则命中且未入旧样本的轮次补抽54条并逐条判断；补样之后未再改规则。具体来源和sampling_stage见validation_samples.json。每指标每侧目标为30条命中、50条未命中；模拟器全体命中不足30的组已全取，不能达到30。补样种子为种子:supplement:指标:human:1，各组均无放回。', '',
        '以下误判率/漏判比例使用初版预测分层（含真人补样），两阶段真人混合样本并非全量CSDS的均匀抽样；修订后列为同一旧命中层/旧未命中层上的错误数，不是修订后命中样本的精确率或独立准确率。误判率=命中样本中错误数/命中样本数；漏判比例=未命中样本中实际有行为数/未命中样本数；不是总体FPR/FNR。客服负面回应两侧共享客服原文；模拟器侧可抽中不同条件下的同一上下文。典型例子的逐条文本、判断和理由见validation_samples.json（按下列ID定位）。', '',
        '|指标|侧|初版误判率|初版漏判比例|修订后旧命中/旧未命中层错误|例子ID|', '|---|---|---|---|---|---|']
    for metric,label in LABELS.items():
        for side,v in validation[metric].items():
            cells=[]
            examples=[]
            for k in ('false_discovery','missed_in_negatives'):
                s=v[k]
                cells.append(f"{s['errors']}/{s['denominator']}={s['rate']:.1%}" if s['rate'] is not None else 'NA')
                examples.extend(s['examples'][:1])
            revised = '；'.join(f"{v[k]['revised_errors']}/{v[k]['denominator']}" for k in ('false_discovery','missed_in_negatives'))
            lines.append('|'+label+'|'+side+'|'+'|'.join(cells)+'|'+revised+'|'+'；'.join(examples)+'|')
    if samples:
        by_id = {s['sample_id']: s for s in samples}
        lines += ['', '## 冻结样本典型例子', '']
        def cell(text):
            return normalize(text).replace('|', '\\|')
        for metric, label in LABELS.items():
            for side in ('human', 'simulator'):
                for stratum in ('false_discovery', 'missed_in_negatives'):
                    ids = validation[metric][side][stratum]['examples'][:1]
                    for sid in ids:
                        sample = by_id[sid]
                        target = sample['previous_agent'] if metric == 'negative_agent' else sample['text']
                        lines.append(f"- {label} / {side} / {sid}：文本「{cell(target)}」；初版预测={sample['predicted']}，人工式判断={int(sample['judgment'])}。{sample['reason']}")
    return '\n'.join(lines)+'\n' 
