"""Offline paired comparison; reads frozen generations and existing rule functions only."""
import csv
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim import behaviors, metrics
from usersim.csds import load, user_turns

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261025
REPLICATES = 2000
SOURCES = ('human', 'Spark', 'DeepSeek')
REACTION = dict(b_behavioral='负面后情绪体现在行为上', b_short='负面后短句（行为口径）',
                b_acceptance='未解决接受', b_alternative='换方案/放弃',
                b_emotion='把情绪说出来', b_exclamation='感叹号')
EXPRESSION = dict(e_short='短句率', e_char_count='字数', e_first_char_count='首轮字数',
                  e_message_count='连发条数', e_politeness='礼貌词')


def pairs(path):
    with (path/'side_by_side.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    result = {(int(r['dialogue_id']), int(r['turn'])): r for r in rows}
    assert len(result) == len(rows), 'duplicate pairs'
    generations = [json.loads(line) for line in (path/'generations.jsonl').read_text().splitlines()]
    valid = {(g['dialogue_id'], g['turn']): g for g in generations if g['status'] == 'ok'}
    assert set(result) == set(valid), 'pair/generation keys differ'
    assert all(r['simulator'].strip() and r['simulator'] == valid[k]['text'] for k,r in result.items())
    return result


def common_records(spark, deepseek, raw):
    """Select the intersection before measuring either model or the human."""
    common = sorted(spark.keys() & deepseek.keys())
    records = []
    previous = defaultdict(list)
    contexts = {did: {c['turn']: c for c in behaviors.contexts(raw[did])} for did in sorted({did for did,_ in common})}
    for did,t in common:
        a,b = spark[did,t],deepseek[did,t]
        c = contexts[did][t]
        assert a['human'] == b['human'] == c['text'], 'human alignment'
        texts = dict(human=c['text'], Spark=a['simulator'], DeepSeek=b['simulator'])
        values = {}
        for source,text in texts.items():
            count = len(user_turns(raw[did])[t-1]['messages']) if source == 'human' else len([s for s in text.splitlines() if s.strip()])
            expression = metrics.measure(text, previous[did,source], t==1, count,
                                         'human' if source=='human' else 'simulator')
            previous[did,source].append(text)
            behavior = behaviors.measure(c,text)
            compact = re.sub(r'[^\w\u4e00-\u9fff]', '', behaviors.normalize(text))
            behavior['short'] = int(0 < len(compact) <= 5)
            values[source] = {**{'e_'+k: None if v=='' else v for k,v in expression.items()},
                              **{'b_'+k:v for k,v in behavior.items()}}
        records.append(dict(dialogue_id=did, turn=t, texts=texts, values=values,
                            previous_agent=c['previous_agent'], negative=bool(values['human']['b_negative_agent'])))
    return records


def quantile(values, p):
    ordered = sorted(values)
    x = (len(ordered)-1)*p
    i = int(x)
    return ordered[i] + (ordered[min(i+1,len(ordered)-1)]-ordered[i])*(x-i)


def summarize(records, replicates=REPLICATES):
    """Turn weighted means; identical resampled dialogue clusters for all sources."""
    names = list(records[0]['values']['human'])
    groups = defaultdict(list)
    for r in records:
        groups[r['dialogue_id']].append(r)
    # Per metric: denominator, human sum, Spark sum, DeepSeek sum.
    clusters = []
    for did in sorted(groups):
        vector = []
        for name in names:
            eligible = [r for r in groups[did] if r['values']['human'][name] is not None]
            vector.extend([len(eligible)] + [sum(r['values'][s][name] for r in eligible) for s in SOURCES])
        clusters.append(vector)
    totals = [sum(col) for col in zip(*clusters)]
    samples = {(name,s): [] for name in names for s in SOURCES[1:]}
    rng = random.Random(SEED)
    for _ in range(replicates):
        draw = rng.choices(clusters, k=len(clusters))
        sums = [sum(col) for col in zip(*draw)]
        for j,name in enumerate(names):
            n,h,a,b = sums[j*4:j*4+4]
            if n:
                samples[name,'Spark'].append((a-h)/n)
                samples[name,'DeepSeek'].append((b-h)/n)
    result = {}
    for j,name in enumerate(names):
        n,h,a,b = totals[j*4:j*4+4]
        result[name] = dict(denominator=n, human=h/n if n else None)
        for source,value in zip(SOURCES[1:], (a,b)):
            gaps = samples[name,source]
            result[name][source] = dict(mean=value/n if n else None, gap=(value-h)/n if n else None,
                                       low=quantile(gaps,.025) if gaps else None,
                                       high=quantile(gaps,.975) if gaps else None)
    return result


def number(value):
    return 'NA' if value is None else f'{value:.6f}'


def gap(v):
    return 'NA' if v['gap'] is None else f"{v['gap']:+.6f} [{v['low']:+.6f}, {v['high']:+.6f}]"


def shrink(base, value):
    return 'NA（s1差距为零）' if base == 0 else f'{(1-abs(value)/abs(base)):.2%}'


def table(summary, labels):
    lines = ['|指标|分母|真人比例/均值|Spark比例/均值|Spark−真人 [95%区间]|DeepSeek比例/均值|DeepSeek−真人 [95%区间]|',
             '|---|---:|---:|---:|---|---:|---|']
    for name,label in labels.items():
        r = summary[name]
        lines.append(f"|{label}|{r['denominator']}|{number(r['human'])}|{number(r['Spark']['mean'])}|{gap(r['Spark'])}|{number(r['DeepSeek']['mean'])}|{gap(r['DeepSeek'])}|")
    return lines


def report(records, all_stats, conditional, ablation):
    lines = ['# 决策 A/B 证据', '', '## 反应方式', '',
             'A 的 s1/s2/s3 基于全量客服负面轮次的三设置共同有效 3,831 轮，与 500 段子集的数字不同。s1=现有提示；s2=允许接受、放弃、换办法或简短回应；s3=删除任务目标。下表各行真人值仅对应本行分母。', '',
             '差距=模型−真人；差距缩小比例=1−|本设置差距|/|同批 s1 差距|。A 的设置以 A s1 为参照；500 段的 DeepSeek 以同批 Spark s1 为参照。负值表示绝对差距扩大；跨批数字不作配对比较。', '',
             '|指标|数据范围|设置|分母|真人比例|模型比例|差距 [95%区间]|相对同批s1差距缩小|', '|---|---|---|---:|---:|---:|---|---:|']
    for name,label in REACTION.items():
        metric = name[2:]
        base = ablation['results'][metric+'/s1']['gap']
        for setting in ('s1','s2','s3'):
            r = ablation['results'][metric+'/'+setting]
            n = r['denominator']
            lines.append(f"|{label}|A 全量负面|Spark {setting}|{n}|{r['human_hits']/n:.6f}|{r['simulator_hits']/n:.6f}|{gap(r)}|{shrink(base,r['gap'])}|")
        r = conditional[name]
        for source in SOURCES[1:]:
            v = r[source]
            lines.append(f"|{label}|500段共同负面|{source} {'s1' if source=='Spark' else ''}|{r['denominator']}|{number(r['human'])}|{number(v['mean'])}|{gap(v)}|{shrink(r['Spark']['gap'],v['gap'])}|")
    lines += ['', '## 表达层', ''] + table(all_stats, EXPRESSION)
    lines += ['', '## 两模型方向与区间对照', '',
              '|范围|指标|Spark差距方向|DeepSeek差距方向|方向一致|Spark区间含零|DeepSeek区间含零|', '|---|---|---|---|---|---|---|']
    def direction(x):
        return 'NA' if x is None else '正' if x>0 else '负' if x<0 else '零'
    def zero(v):
        return 'NA' if v['low'] is None else '是' if v['low']<=0<=v['high'] else '否'
    for scope,stats in (('全部共同轮次',all_stats),('负面之后',conditional)):
        for name,r in stats.items():
            a,b = r['Spark'],r['DeepSeek']
            same = 'NA' if a['gap'] is None else '是' if direction(a['gap'])==direction(b['gap']) else '否'
            lines.append(f"|{scope}|{name}|{direction(a['gap'])}|{direction(b['gap'])}|{same}|{zero(a)}|{zero(b)}|")
    lines += ['', '## 并排例子', '', '每类固定按（数字对话ID、轮次）升序取前3组，不按模型与真人差距筛选。反应方式取共同负面且三来源任一命中该指标的轮次；表达层短句/礼貌词同样取任一命中，连发条数取任一来源条数>1，字数取全部，首轮字数仅取首轮。不同类别可重复相同例子；此为规则命中示例，不是语义正确性判断。', '']
    def cell(text):
        return text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','\\|').replace('\r','').replace('\n','<br>')
    for name,label in {**REACTION,**EXPRESSION}.items():
        eligible = [r for r in records if (not name.startswith('b_') or r['negative']) and (name!='e_first_char_count' or r['turn']==1)]
        if name.startswith('b_') or name in ('e_short','e_politeness'):
            eligible = [r for r in eligible if any(r['values'][source][name] for source in SOURCES)]
        elif name == 'e_message_count':
            eligible = [r for r in eligible if any(r['values'][source][name]>1 for source in SOURCES)]
        assert len(eligible) >= 3, f'Insufficient examples: {name}'
        lines += [f'### {label}', '', '|对话:轮次|前一条客服|真人|Spark|DeepSeek|', '|---|---|---|---|---|']
        for r in eligible[:3]:
            lines.append('|'+f"{r['dialogue_id']}:{r['turn']}"+'|'+cell(r['previous_agent'])+'|'+ '|'.join(cell(r['texts'][s]) for s in SOURCES)+'|')
        lines.append('')
    lines += ['## 统计口径与校验事实', '',
              f'两模型来自同一批500段对话、计划4,710轮。DeepSeek 未遭审核剔除，Spark 剔除5轮（对话3757）；比较只用共同有效 {len(records):,} 轮。共同负面轮次 {sum(r["negative"] for r in records)}。空输出检查要求 DeepSeek 每轮非空。',
              f'两模型统计和 A 均按对话配对 bootstrap {REPLICATES} 次，固定种子 {SEED}；有放回抽取同数量对话，保留抽中对话的全部符合条件轮次。比例/均值按轮次加权；首轮指标仅用首轮。95%百分位区间线性插值。区间只针对模型−真人差距，不包含规则误差。条件比较在负面轮次中抽对话；首轮指标无符合轮次时为 NA。',
              '表达层完全调用 usersim/metrics.py；行为完全调用 usersim/behaviors.py。行为短句补用 A 原有去空白、去标点的非空≤5字符口径；表达层短句为 metrics.py 的字母/数字/汉字计数≤5。两者口径保持原样。客服负面取紧邻用户之前最后一条客服消息。重复追问的既往文本仅取各来源较早的共同有效轮次。叙事加料上下文仍为真实严格前缀与清洗目标。',
              'A 的比例、差距与区间直接合并 outputs/ablation_a_full/report.json；其三设置共同有效分母为3,831（全量原始负面3,832轮）。缩小比例为点估计，不附区间。本脚本不调用模型API，不作推荐。', '', '## 全指标：全部共同有效轮次', '']
    labels = {k:k for k in all_stats}
    lines += table(all_stats, labels)
    lines += ['', '## 全指标：客服负面回应之后', ''] + table(conditional, labels)
    return '\n'.join(lines)+'\n'


def main():
    spark_path = ROOT/'outputs/extended'
    deepseek_path = ROOT/'outputs/model_b/deepseek-v4.1-flash/full500'
    ids = [json.loads((p/'sampled_ids.json').read_text())['ids'] for p in (spark_path,deepseek_path)]
    assert ids[0] == ids[1] and len(set(ids[0])) == 500, 'dialogue batch mismatch'
    records = common_records(pairs(spark_path),pairs(deepseek_path),{r['DialogueID']:r for r in load()})
    assert len(records) == 4705 and len({r['dialogue_id'] for r in records}) == 500
    all_stats = summarize(records)
    conditional = summarize([r for r in records if r['negative']])
    ablation = json.loads((ROOT/'outputs/ablation_a_full/report.json').read_text())
    assert ablation['seed'] == SEED and ablation['replicates'] >= 2000
    assert all(r['denominator']==3831 for r in ablation['results'].values())
    out = ROOT/'outputs/decision_ab'
    out.mkdir(exist_ok=True)
    (out/'summary.json').write_text(json.dumps(dict(seed=SEED,replicates=REPLICATES,common_turns=len(records),
        all=all_stats,conditional=conditional),ensure_ascii=False,indent=2)+'\n')
    (out/'report.md').write_text(report(records,all_stats,conditional,ablation))
    print(f'PASS: {len(records)} common turns; {len(all_stats)} metrics; report.md and summary.json')


if __name__ == '__main__':
    main()
