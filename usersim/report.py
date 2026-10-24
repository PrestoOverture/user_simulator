"""Descriptive paired summaries; no model judge."""
import csv
import json
import statistics
from pathlib import Path

LABELS = dict(char_count='字数', short='短句率', message_count='连发条数', politeness='礼貌词频率',
              escalation='升级要求', refusal='拒绝方案', urgency='催促', repeated='重复追问',
              first_char_count='首轮字数', first_identifier='首轮编号披露', correction='自我更正')


def means(metrics):
    return {k: {source: statistics.mean(float(r[k]) for r in metrics if r['source']==source and r[k]!='')
                for source in ('human', 'simulator')} for k in LABELS}


def ranked(stats):
    # Different units cannot be compared as raw differences. Use absolute relative gap.
    return sorted(stats, key=lambda k: abs(stats[k]['simulator']-stats[k]['human']) / max(abs(stats[k]['human']), 1e-12), reverse=True)


def md(text):
    return text.replace('|', '\\|').replace('\n', '<br>')


def write_reports(out, metrics, pairs, usage, baseline=None):
    out = Path(out)
    stats = means(metrics)
    ranking = ranked(stats)
    table = '|指标|真人均值|模拟器均值|差距（模拟器−真人）|相对绝对差距|\n|---|---:|---:|---:|---:|\n'
    for k in ranking:
        h, s = stats[k]['human'], stats[k]['simulator']
        relative = abs(s-h)/abs(h) if h else None
        table += f'|{LABELS[k]}|{h:.6f}|{s:.6f}|{s-h:+.6f}|{relative:.4f}|\n' if relative is not None else f'|{LABELS[k]}|{h:.6f}|{s:.6f}|{s-h:+.6f}|真人为0，未定义|\n'
    method = '排序使用绝对相对差距 |模拟器−真人|/真人，因字数、条数、比例单位不同；真人为0且模拟器非0排在前，二者都0并列末端。小分母会放大排序，不代表统计显著。首轮指标仅在每段首轮计算，其余按用户轮次加权。相似度对去空白文本与该来源此前任一用户轮次比较（模拟器只用于自身指标，绝不进入重放历史）。\n\n'
    (out/'metrics_summary.md').write_text('# 规则统计\n\n'+method+table)
    lines = ['# 中文售后客服用户模拟器试跑观察报告', '',
             f"样本 {usage['dialogues']} 段；用户轮次 {len(pairs)}；种子见 sampled_ids.json。只比较本次样本，不据此选定正式实验指标。", '',
             '## 1. 数据格式差异及影响', '',
             'CSDS 原文带分词空格，Q 连发分别存储，需合并；QRole 不全为顾客，本次只取用户。没有独立解决状态标签。订单数字多被脱敏；首轮编号披露口径为：真人含[订单编号]或[数字]，模拟器含上述占位符或至少6位连续数字；只识别编号外形，不判断有效性。历史和目标按ADR-014去掉中文字符之间的分词空格，英文/数字间空格保留，对照表保留原文。UserSumm 是多句列表，可能含结果，按 data_format_notes.md 的规则去除结果；抽中摘要已逐条人工核对，并使用覆盖表修正规则不适用项；审计记录见summary_audit.json。目标仍可能包含后续才提出的诉求，这是合同指定的目标输入。历史严格截断在目标轮次之前。完整格式和3段原文见 data_format_notes.md。', '',
             f"审核剔除：{usage['moderated_count']} / {usage['planned_user_turns']}轮（{usage['moderated_ratio']:.4%}）；对话ID：{usage['moderated_dialogue_ids']}。HTTP400且10013不重试，真人和模拟器配对两侧均剔除；原始历史不改写。该剔除可能集中于特定商品类别，存在选择偏差。逐轮见moderated.json，与请求日志对应。", '',
             '## 2. 真人指标出现次数与稀疏程度', '',
             '这里只报告次数和比例，不人为设置“足够常见”的研究阈值；是否纳入正式实验交由 D1 决定。零命中或少量命中不足以据此给出稳定行为差距结论。', '',
             '|规则|真人命中/分母|比例|', '|---|---:|---:|']
    for k in ['short','politeness','escalation','refusal','urgency','repeated','correction','first_identifier']:
        vals=[int(r[k]) for r in metrics if r['source']=='human' and r[k]!='']
        lines.append(f'|{LABELS[k]}|{sum(vals)}/{len(vals)}|{statistics.mean(vals):.4%}|')
    lines += ['', '## 3. 差距最大的三项及并排例子', '', method, table,
              '以下按上述相对差距排序选前三项，每项列出配对绝对差距最大的两轮（只是可核对例子，非随机抽查或显著性检验）。']
    lookup={(r['dialogue_id'], r['turn'], r['source']):r for r in metrics}
    for k in ranking[:3]:
        valid=[p for p in pairs if lookup[(p['dialogue_id'],p['turn'],'human')][k]!='']
        valid.sort(key=lambda p: abs(float(lookup[(p['dialogue_id'],p['turn'],'simulator')][k])-float(lookup[(p['dialogue_id'],p['turn'],'human')][k])),reverse=True)
        lines += ['', f'### {LABELS[k]}', '', '|ID/轮|真人原话|Spark输出|真人值/Spark值|', '|---|---|---|---|']
        for p in valid[:2]:
            h=lookup[(p['dialogue_id'],p['turn'],'human')][k]
            s=lookup[(p['dialogue_id'],p['turn'],'simulator')][k]
            lines.append(f"|{p['dialogue_id']}/{p['turn']}|{md(p['human'])}|{md(p['simulator'])}|{h}/{s}|")
    lines += ['', '## 4. 规则未覆盖的阅读观察', '',
              '关键词和长度没有衡量回复是否承接客服问题、是否编造订单细节、是否过早重述目标。下列为人工阅读入口，具体观察在本报告的人工阅读补充中记录；不能用规则差异替代语义判断。', '',
              '## 5. 关键词误判', '',
              '本次独立审查已去除“申请”对礼貌的触发，收窄升级、拒绝和自我更正规则，将普通询时与催促分开。规则修订及680条固定种子命中/未命中逐条判断见 outputs/review.md 和 outputs/review_evidence/keyword_samples.json。“不要”仍可能只是任务参数；“麻烦”仍可能是陈述不便，催促词也可能用于顾客自己的行动。重复“好的/谢谢”可能触发重复追问。字面命中不等于行为真值。', '',
              '## 6. token用量、全量推算与额度', '',
              f"本次重放输入 {usage['input_tokens']:,}，输出 {usage['output_tokens']:,}，合计 {usage['total_tokens']:,} token；HTTP尝试 {usage['request_count']}，成功生成 {usage['successful_requests']}；墙钟耗时 {usage['elapsed_seconds']:.2f} 秒。",
              f"按已知token总量除以计划轮次（含moderated）再乘全量用户轮次推算：全CSDS（含非用户角色，纯体量外推） {usage['full_projection']['all_csds_tokens']:,} token；仅 QRole=用户 {usage['full_projection']['user_role_tokens']:,} token。公式与分母见 token_usage.json。未观测的其他角色、长对话与样本分布可能不同，此为线性外推。",
              f"未返回供应商用量的请求{usage['usage_unknown_requests']}次，其token未知，汇总为已知用量下界，不能把未知消耗当作已确认零消耗。",
              '用户提供总额度10亿token；本次全量单次重放的线性估算低于该名义额度。账户剩余额度、有效期和正式实验规模未核验，不能确认实际足够。连通性请求单列在 usage 的 check_usage，不混入每轮外推。']
    if baseline:
        old=means(list(csv.DictReader((Path(baseline)/'metrics_by_turn.csv').open())))
        lines += ['', '## 7. 20段与500段对比', '',
                  '下表比较均值差距方向；同方向仅指这两份样本的描述一致，不代表总体结论或统计显著。', '',
                  '|指标|20段差距|500段差距|方向一致|','|---|---:|---:|---|']
        for k in ranking:
            a=old[k]['simulator']-old[k]['human']; b=stats[k]['simulator']-stats[k]['human']
            lines.append(f'|{LABELS[k]}|{a:+.6f}|{b:+.6f}|{"是" if (a>0)-(a<0)==(b>0)-(b<0) else "否"}|')
        lines.append('\n前三项相对差距排序：20段 '+ '、'.join(LABELS[k] for k in ranked(old)[:3])+'；500段 '+'、'.join(LABELS[k] for k in ranking[:3])+'。稀疏规则的分母和命中数见第2问，是否选定核心指标仍待 Claude/用户决定。')
    if (out/'review_notes.md').exists():
        lines += ['', (out/'review_notes.md').read_text()]
    (out/'report.md').write_text('\n'.join(lines)+'\n')
