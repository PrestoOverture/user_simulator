"""Build deterministic D1 evidence from saved artifacts; no network/API calls."""
import csv
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usersim.report import LABELS
from usersim.csds import load
from check_correction_pilot import common_valid, evaluate, read_jsonl

SEED = 20261025
REPLICATES = 2000


def read_metrics(folder):
    with (ROOT / folder / 'metrics_by_turn.csv').open(newline='') as f:
        return list(csv.DictReader(f))


def summarize(metrics):
    """Resample dialogues with replacement, retaining all paired turns and weights."""
    paired = {(str(r['dialogue_id']), str(r['turn']), r['source']): r for r in metrics}
    ids = sorted({k[0] for k in paired})
    result = {}
    for key in LABELS:
        clusters = []
        positives = set()
        hit_turns = 0
        for did in ids:
            pairs = [(r, paired[(d, t, 'simulator')]) for (d, t, source), r in paired.items()
                     if d == did and source == 'human' and r[key] != '']
            h = sum(float(a[key]) for a, _ in pairs)
            delta = sum(float(b[key])-float(a[key]) for a, b in pairs)
            hits = sum(float(a[key]) > 0 for a, _ in pairs)
            if hits:
                positives.add(did)
            hit_turns += hits
            clusters.append((h, delta, len(pairs)))
        denominator = sum(c[2] for c in clusters)
        gap = sum(c[1] for c in clusters)/denominator
        rng = random.Random(SEED)
        samples = []
        for _ in range(REPLICATES):
            draw = rng.choices(clusters, k=len(clusters))
            count = sum(c[2] for c in draw)
            if not count:
                raise ValueError('Bootstrap replicate has no eligible turns')
            samples.append(sum(c[1] for c in draw)/count)
        samples.sort()
        def quantile(p):
            index = (len(samples)-1)*p
            lower = int(index)
            return samples[lower] + (samples[min(lower+1, len(samples)-1)]-samples[lower])*(index-lower)
        result[key] = dict(human=sum(c[0] for c in clusters)/denominator, gap=gap,
                           low=quantile(.025), high=quantile(.975),
                           dialogues=len(positives), turns=hit_turns, denominator=denominator)
    return result


def interval(s):
    return f"{s['gap']:+.6f} [{s['low']:+.6f}, {s['high']:+.6f}]"


def review_tables():
    text = (ROOT/'outputs/review.md').read_text()
    old, revised = {}, {}
    for line in text.splitlines():
        cells = line.strip('|').split('|')
        if len(cells) == 6 and cells[1] in ('human', 'simulator'):
            old[cells[0], cells[1]] = f'误判 {cells[2]}={cells[3]}；漏判 {cells[4]}={cells[5]}'
        if len(cells) == 4 and cells[1] in ('human', 'simulator'):
            values = []
            for cell in cells[2:]:
                match = re.fullmatch(r'\d+→(\d+)/(\d+)', cell)
                if not match:
                    break
                a, b = map(int, match.groups())
                values.append(f'{a}/{b}={a/b:.1%}')
            if len(values) == 2:
                revised[cells[0], cells[1]] = f'旧命中层错误 {values[0]}；旧未命中层错误 {values[1]}'
    assert len(old) == len(revised) == 10
    return old, revised


def build():
    full = summarize(read_metrics('outputs/extended'))
    small_rows = read_metrics('outputs')
    small = {}
    for key in LABELS:
        values = {source: [float(r[key]) for r in small_rows if r['source'] == source and r[key] != '']
                  for source in ('human', 'simulator')}
        small[key] = sum(values['simulator'])/len(values['simulator'])-sum(values['human'])/len(values['human'])
    folders = dict(baseline='outputs/extended', C1='outputs/correction_pilot/C1', C2='outputs/correction_pilot/C2')
    generations = {name: {g['request_id']: g for g in read_jsonl(ROOT/folder/'generations.jsonl')}
                   for name, folder in folders.items()}
    common = common_valid(generations)
    ids = json.loads((ROOT/'outputs/extended/sampled_ids.json').read_text())['ids']
    lookup = {r['DialogueID']: r for r in load()}
    rows = [lookup[i] for i in ids]
    pilot = {name: summarize(evaluate(rows, gens, common)) for name, gens in generations.items()}
    old, revised = review_tables()
    lines = ['# D1 核心指标证据', '',
             '依据：docs/prd.md 4.2、docs/roadmap.md 第3节。两个条件未定义数值阈值；本材料列出判定依据，“是否同时满足”待用户判定，不据区间是否含零替代研究阈值。', '',
             f'500段，基线有效配对4705轮（计划4710轮，审核剔除5轮）；20段有效201轮。按对话配对 bootstrap：{REPLICATES}次，种子{SEED}，每次从500个对话有放回抽500个，保留抽中对话全部配对轮次；差距为轮次加权模拟器−真人，95%百分位区间用线性插值。首轮指标仅用首轮。', '',
             f'修正预跑使用 baseline/C1/C2 共同有效轮次交集：{len(common)}轮，{len(ids)}段；重复追问在共同有效历史上重算。该表分母由三条件交集决定，本次恰与全基线同为4705轮。每项、每条件重置同一种子，使抽样对话一致。', '',
             '出现次数对二值指标为值=1，对字数/首轮字数为字数>0，对连发条数为条数>0（并非“连发≥2”）；表中均值沿用原指标口径。比例用0–1表示。', '',
             '## 500段出现与差距', '',
             '|指标|真人出现率/均值|出现对话/500|出现轮次/有效分母|模拟器−真人 [95%区间]|',
             '|---|---:|---:|---:|---|']
    for key, label in LABELS.items():
        s = full[key]
        lines.append(f"|{label}|{s['human']:.6f}|{s['dialogues']}/500|{s['turns']}/{s['denominator']}|{interval(s)}|")
    lines += ['', '## 测量可靠性', '',
              '来源 outputs/review.md 第3节；抽样合并20+500段，冻结于旧规则与旧输出。旧规则误判分母为抽中命中数，漏判分母为抽中未命中数，均非总体FPR/FNR。修正规则复核仍在旧分层样本上，不能视作当前命中样本精确率或独立准确率。无关键词审查数据写“无”。', '',
              '|指标|来源|旧规则误判率/漏判比例|修正规则同冻结样本复核|', '|---|---|---|---|']
    for key, label in LABELS.items():
        for source in ('human', 'simulator'):
            name = '礼貌词' if key == 'politeness' else label
            lines.append(f'|{label}|{source}|{old.get((name, source), "无")}|{revised.get((name, source), "无")}|')
    lines += ['', '## 修正预跑共同轮次', '', '|指标|基线差距 [95%区间]|C1差距 [95%区间]|C2差距 [95%区间]|', '|---|---|---|---|']
    for key, label in LABELS.items():
        lines.append('|'+label+'|'+'|'.join(interval(pilot[c][key]) for c in folders)+'|')
    lines += ['', '## 两个条件是否同时满足：事实依据总表', '',
              '方向只比较点估计符号；零差距记“零”，不同于正/负。20与500段为不同样本，方向一致不表示独立重复验证。', '',
              '|指标|条件一：真人均值；出现对话/轮次|条件二：500段差距 [95%区间]；含零|20段差距|20 vs 500方向|两个条件是否同时满足|', '|---|---|---|---:|---|---|']
    sign = lambda x: '正' if x > 0 else '负' if x < 0 else '零'
    for key, label in LABELS.items():
        s = full[key]
        direction = f'{sign(small[key])}/{sign(s["gap"])}；'+('一致' if sign(small[key]) == sign(s['gap']) else '不一致')
        lines.append(f"|{label}|{s['human']:.6f}；{s['dialogues']}/500段、{s['turns']}/{s['denominator']}轮|{interval(s)}；{'是' if s['low'] <= 0 <= s['high'] else '否'}|{small[key]:+.6f}|{direction}|待用户判定（阈值未定义）|")
    lines += ['', '测量边界：编号披露仅为占位符/数字外形代理；重复追问为字符相似代理；连发为消息/非空行代理。区间仅反映对话重抽样变异，不包含关键词测量误差或审核剔除偏差。未调用任何模型API，未选定核心指标。']
    behavior_report = (ROOT/'outputs/behaviors/report.md').read_text()
    lines += ['', '## 新增四类行为指标', '',
              '以下来自 outputs/behaviors/report.md；原有指标口径保持不变。']
    lines += [line if not line.startswith('#') else '##' + line
              for line in behavior_report.splitlines()[1:]]
    (ROOT/'outputs/d1_brief.md').write_text('\n'.join(lines)+'\n')
    print(f'Wrote outputs/d1_brief.md: {len(LABELS)} metrics; common turns={len(common)}; bootstrap={REPLICATES}, seed={SEED}')


if __name__ == '__main__':
    build()
