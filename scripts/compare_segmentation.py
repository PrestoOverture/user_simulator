"""Read-only v1 comparison on the same 20 dialogue IDs, using current metric definitions."""
import csv
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from usersim.metrics import measure
from usersim.report import LABELS, md, means


def simulator_metrics(pairs):
    rows=[]; previous={}
    for pair in pairs:
        did=int(pair['dialogue_id']); turn=int(pair['turn']); text=pair['simulator']
        before=previous.setdefault(did,[])
        values=measure(text,before,turn==1,len([s for s in text.splitlines() if s.strip()]),'simulator')
        rows.append(dict(dialogue_id=did,turn=turn,source='simulator',**values))
        before.append(text)
    return rows


def main():
    old=Path('outputs/archive/v1_segmented'); new=Path('outputs')
    a=json.loads((old/'sampled_ids.json').read_text());b=json.loads((new/'sampled_ids.json').read_text())
    assert a['ids']==b['ids'] and a['seed']==b['seed']==20261025
    pold=list(csv.DictReader((old/'side_by_side.csv').open()))
    pnew=list(csv.DictReader((new/'side_by_side.csv').open()))
    key=lambda p:(p['dialogue_id'],p['turn'])
    common={key(p) for p in pold}&{key(p) for p in pnew}
    mo=simulator_metrics([p for p in pold if key(p) in common])
    mn=simulator_metrics([p for p in pnew if key(p) in common])
    import statistics
    lines=['# 分词空格敏感性观察：同一批20段', '',
           f'种子20261025，20段ID完全相同；两版共同有效用户轮次{len(common)}，v1表行数{len(pold)}、新版{len(pnew)}。仅对共同有效轮次比较。归档只读。', '',
           '为保证口径一致，两版模拟器原输出均用当前指标重算，包含新版首轮编号披露（占位符或≥6位数字）；不能直接拿v1旧的8位数字列作比较。重复指标分别使用该版此前共同有效模拟器轮次。', '',
           '|指标|v1模拟器均值|新版模拟器均值|新版−v1|','|---|---:|---:|---:|']
    for k,label in LABELS.items():
        x=statistics.mean(float(r[k]) for r in mo if r[k]!='')
        y=statistics.mean(float(r[k]) for r in mn if r[k]!='')
        lines.append(f'|{label}|{x:.6f}|{y:.6f}|{y-x:+.6f}|')
    ao={r['dialogue_id']:r for r in json.loads((old/'summary_audit.json').read_text())}
    an={r['dialogue_id']:r for r in json.loads((new/'summary_audit.json').read_text())}
    lines += ['', '## 任务目标逐条对照', '', '|ID|目标是否相同|v1目标|新版目标|','|---|---|---|---|']
    changed=[]
    for did in a['ids']:
        x=ao[did]['goal'];y=an[did]['goal']
        if x!=y:changed.append(did)
        lines.append(f'|{did}|{"相同" if x==y else "不同"}|{md(x)}|{md(y)}|')
    lines += ['', f'任务目标变化ID：{changed}。新版10049保留“最后请客服帮助取消订单”的诉求。', '',
              '两版除中文字符间分词空格外，任务目标存在上述变化，而且生成是两次独立抽样。这些均值是两版输出的观察差异，不能将差异单独归因于去空格；未挑选或为匹配结论重生成。']
    (new/'sensitivity_segmentation.md').write_text('\n'.join(lines)+'\n')
    print(f'sensitivity written; paired turns={len(common)}, changed goals={changed}')


if __name__=='__main__':main()
