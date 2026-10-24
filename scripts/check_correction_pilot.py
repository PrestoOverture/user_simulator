"""Validate paired pilot artifacts and rebuild the three-condition factual report."""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim.anchors import pool, select, anchored_prompt, anchor_template
from usersim.csds import load, user_turns
from usersim.replay import prompt
from usersim.metrics import measure
from usersim.report import means, ranked, LABELS


def read_jsonl(path):
    return [json.loads(s) for s in path.read_text().splitlines()]


def evaluate(rows, generations):
    metrics = []
    for row in rows:
        previous = dict(human=[], simulator=[])
        for t, turn in enumerate(user_turns(row), 1):
            item = generations[f"{row['DialogueID']}:{t}"]
            if item['status'] == 'moderated':
                continue
            for source, text, count in [('human', '\n'.join(turn['messages']), len(turn['messages'])),
                                        ('simulator', item['text'], len([s for s in item['text'].splitlines() if s.strip()]))]:
                metrics.append(dict(dialogue_id=row['DialogueID'], turn=t, source=source,
                                    **measure(text, previous[source], t == 1, count, source)))
                previous[source].append(text)
    return metrics


def check(root, n=500):
    all_rows = load()
    ids = json.loads(Path('outputs/extended/sampled_ids.json').read_text())['ids'][:n]
    lookup = {r['DialogueID']: r for r in all_rows}
    rows = [lookup[i] for i in ids]
    excluded = set(json.loads(Path('outputs/sampled_ids.json').read_text())['ids']) | set(json.loads(Path('outputs/extended/sampled_ids.json').read_text())['ids'])
    items = pool(all_rows, excluded)
    expected = {f"{r['DialogueID']}:{t}" for r in rows for t in range(1,len(user_turns(r))+1)}
    conditions = {}
    facts = []
    for name, folder in [('baseline', Path('outputs/extended')), ('C1',root/'C1'), ('C2',root/'C2')]:
        raw = read_jsonl(folder/'generations.jsonl')
        if name == 'baseline':
            raw = [g for g in raw if g['dialogue_id'] in ids]
        gens = {g['request_id']: g for g in raw}
        assert len(raw) == len(gens) and set(gens) == expected, (name, 'turn IDs differ')
        anchors = None
        if name != 'baseline':
            manifest = json.loads((folder/'anchors.json').read_text())
            wanted = [select(items, r, name, manifest['seed']) for r in rows]
            assert manifest['condition'] == name and manifest['excluded_ids'] == sorted(excluded)
            assert manifest['dialogues'] == wanted, (name, 'anchor provenance/selection differs')
            anchors = {a['dialogue_id']: a for a in wanted}
        for row in rows:
            for t, turn in enumerate(user_turns(row),1):
                g = gens[f"{row['DialogueID']}:{t}"]
                assert g['dialogue_id'] == row['DialogueID'] and g['turn'] == t
                messages = prompt(row,turn) if anchors is None else anchored_prompt(row,turn,anchors[row['DialogueID']])
                assert g['prompt_sha256'] == hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
                assert g['status'] in ('ok','moderated')
                assert (g['text'] is None) == (g['status'] == 'moderated')
        valid = {k for k,g in gens.items() if g['status'] == 'ok'}
        empty = sum(not gens[k]['text'].strip() for k in valid)
        assert valid and empty/len(valid) < .02, (name, 'empty output >=2%')
        metrics = evaluate(rows,gens)
        if anchors is not None:
            for file, expected_rows in [('metrics_by_turn.csv',metrics)]:
                with (folder/file).open(newline='') as f:
                    actual = list(csv.DictReader(f))
                assert actual == [{k:str(v) for k,v in m.items()} for m in expected_rows], (name,file)
            with (folder/'side_by_side.csv').open(newline='') as f:
                pairs=list(csv.DictReader(f))
            assert {f"{p['dialogue_id']}:{p['turn']}" for p in pairs} == valid and len(pairs)==len(valid)
            for p in pairs:
                row=lookup[int(p['dialogue_id'])]; t=int(p['turn'])
                assert p['human']=='\n'.join(user_turns(row)[t-1]['messages'])
                assert p['simulator']==gens[f"{p['dialogue_id']}:{t}"]['text']
            moderated = [dict(dialogue_id=g['dialogue_id'], turn=g['turn'], request_id=k)
                         for k,g in gens.items() if g['status']=='moderated']
            assert {m['request_id'] for m in json.loads((folder/'moderated.json').read_text())} == {m['request_id'] for m in moderated}
            sampled = json.loads((folder/'sampled_ids.json').read_text())
            assert sampled['ids'] == ids and sampled['n'] == n and sampled['user_turns'] == len(expected)
            records = read_jsonl(folder/'request_usage.jsonl')
            assert all(r['request_id'] in expected for r in records)
            for rid,g in gens.items():
                attempts = [r for r in records if r['request_id']==rid]
                terminal = [r for r in attempts if r['status'] in ('ok','moderated')]
                assert len(terminal)==1 and terminal[0]['status']==g['status'], (name,rid,'request status')
                if g['status']=='moderated':
                    assert len(attempts)==1 and terminal[0]['http_status']==400 and str(terminal[0]['error_code'])=='10013'
            usage=json.loads((folder/'token_usage.json').read_text())
            assert usage['planned_user_turns']==len(expected) and usage['dialogues']==n
            assert usage['request_count']==len(records)
            assert usage['input_tokens']==sum(r['input_tokens'] for r in records)
            assert usage['output_tokens']==sum(r['output_tokens'] for r in records)
            assert usage['total_tokens']==usage['input_tokens']+usage['output_tokens']
            assert usage['usage_unknown_requests']==sum(not r.get('usage_known',False) for r in records)
            assert usage['user_turns']==len(valid) and usage['moderated_count']==len(expected)-len(valid)
            assert usage['model']=='spark-x2.6' and usage['thinking']=={'type':'disabled'}
            assert usage['temperature']=='omitted; official default 1'
            assert (folder/'prompt_template.md').read_text()==anchor_template()
        conditions[name]=(metrics,gens)
        facts.append(f"{name}: planned={len(expected)}, paired={len(valid)}, moderated={len(expected)-len(valid)}, empty={empty} ({empty/len(valid):.4%})")
    keys = ranked(means(conditions['baseline'][0]))
    def mean(name,source,key):
        values=[float(m[key]) for m in conditions[name][0] if m['source']==source and m[key]!='']
        return sum(values)/len(values)
    report=['# 原话约束探索性预跑', '', f'{n}段基线的同批轮次；非正式实验。各条件按自身 moderated 轮次配对剔除。全部指标用当前 usersim.metrics.measure 重算；不修改基线文件。', '', *facts, '',
            '差距为 |模拟器均值−真人均值|；缩小比例为 (基线差距−该条件差距)/基线差距，负值表示差距扩大；基线差距为0时记 NA。沿用基线报告的相对绝对差距 |模拟器−真人|/|真人| 降序；真人为0时仅排序分母用1e-12，二者均0排在末尾。各条件真人均值因审核剔除可能不同。', '',
            '|指标|基线真人|基线模拟器|C1真人|C1模拟器|C1差距缩小|C2真人|C2模拟器|C2差距缩小|',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for key in keys:
        base=abs(mean('baseline','simulator',key)-mean('baseline','human',key))
        cells=[LABELS[key],f"{mean('baseline','human',key):.6f}",f"{mean('baseline','simulator',key):.6f}"]
        for c in ('C1','C2'):
            h,s=mean(c,'human',key),mean(c,'simulator',key)
            cells += [f'{h:.6f}',f'{s:.6f}',f'{(base-abs(s-h))/base:.2%}' if base else 'NA']
        report.append('| '+' | '.join(cells)+' |')
    report += ['', '短句等二值指标为比例；首轮指标仅计首轮。重复追问按各条件剔除审核轮次后的历史计算。未知 token 消耗不计入已知总量，详见各条件 token_usage.json。示例按固定种子、每段5条；下列并排例子为按基线顺序的前三个共同有效轮次，不按效果筛选。']
    common=[k for k in expected if all(gens[k]['status']=='ok' for _,gens in conditions.values())]
    ordered=[f"{r['DialogueID']}:{t}" for r in rows for t in range(1,len(user_turns(r))+1) if f"{r['DialogueID']}:{t}" in common][:3]
    def cell(s):
        return s.replace('|','\\|').replace('\r','<br>').replace('\n','<br>')
    for name,(_,gens) in conditions.items():
        report += ['',f'## {name} 三组并排例子','','|轮次|真人|模拟器|','|---|---|---|']
        for rid in ordered:
            did,t=map(int,rid.split(':'))
            human='\n'.join(user_turns(lookup[did])[t-1]['messages'])
            report.append(f'|{rid}|{cell(human)}|{cell(gens[rid]["text"])}|')
    for c in ('C1','C2'):
        a=json.loads((root/c/'anchors.json').read_text())
        report += ['',f"{c} 随机补齐示例数：{sum(x['fallback'] for d in a['dialogues'] for x in d['examples'])}。"]
    assert len(ordered)==3, 'Fewer than three common valid example turns'
    (root/'report.md').write_text('\n'.join(report)+'\n')
    print('\n'.join(facts))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('root',nargs='?',type=Path,default=Path('outputs/correction_pilot'))
    p.add_argument('--n',type=int,choices=(3,500),default=500)
    args=p.parse_args()
    check(args.root,args.n)
