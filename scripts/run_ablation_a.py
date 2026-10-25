"""Spark prompt ablation on negative-agent contexts; resumable, no full run implied."""
import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim.csds import load, user_turns, clean_summary
from usersim.replay import prompt
from usersim.behaviors import contexts, measure, negative_agent, LABELS, normalize
from usersim.spark import Spark
from scripts.run_pilot import dump

ADDITION = '如果客服的回答没能解决你的问题，你可以像真实顾客一样接受、放弃、换个办法，或者简短回应后结束，不必坚持原来的要求。'
SETTINGS = ('s1', 's2', 's3')
SEED = 20261025
REPLICATES = 2000
METRICS = dict(behavioral=LABELS['behavioral'], acceptance=LABELS['acceptance'],
               alternative=LABELS['alternative'], short='负面后短句（≤5字）',
               emotion='把情绪说出来（情绪词）', exclamation=LABELS['exclamation'])


def ablation_prompt(row, turn, setting):
    messages = prompt(row, turn)
    if setting == 's2':
        messages[1]['content'] += '\n' + ADDITION
    elif setting == 's3':
        messages[1]['content'] = messages[1]['content'].split('\n', 1)[1]
    elif setting != 's1':
        raise ValueError('Unknown setting')
    return messages


def targets(rows, limit=None):
    chosen=[]
    for row in rows:
        if row['QRole'] != '用户':
            continue
        eligible=[(turn, c) for turn,c in zip(user_turns(row), contexts(row)) if negative_agent(c['previous_agent'])]
        if eligible:
            chosen.append((row, eligible))
    return chosen[:limit] if limit is not None else chosen


def values(context, text):
    result=measure(context,text)
    compact=re.sub(r'[^\w\u4e00-\u9fff]', '', normalize(text))
    result['short']=int(0 < len(compact) <= 5)
    return result


def write_report(out, chosen):
    caches={s:{r['request_id']:r for r in map(json.loads,(out/s/'generations.jsonl').read_text().splitlines())}
            for s in SETTINGS if (out/s/'generations.jsonl').exists()}
    if not all(s in caches for s in SETTINGS):
        return
    common=[(row,c) for row,ts in chosen for _,c in ts
            if all(c['request_id'] in caches[s] and caches[s][c['request_id']]['status']=='ok' for s in SETTINGS)]
    if not common:
        raise ValueError('No common valid targets')
    ids=list(dict.fromkeys(row['DialogueID'] for row,c in common))
    lines=['# 消融 A：客服负面回应之后', '',
           f'按全量 CSDS（QRole=用户）数据原顺序选择前 {len(chosen)} 段含目标轮次的对话。三设置共同有效 {len(common)} 轮、{len(ids)} 段；审核拒绝两侧共同剔除。',
           f'差距=模拟器−真人；按对话配对 bootstrap {REPLICATES} 次，固定种子 {SEED}，有放回抽取同样数量对话并保留其全部目标轮次；95%百分位区间线性插值。区间不包含规则误差。',
           's1=现有 prompt；s2=末尾追加合同原句；s3=删除任务目标行。行为项为既有规则的并集；短句、未解决接受、换方案/放弃可重叠。情绪词与感叹号分别计数。', '',
           '|指标|设置|真人比例|模拟器比例|差距 [95%区间]|','|---|---|---:|---:|---|']
    result={}
    for metric,label in METRICS.items():
        for s in SETTINGS:
            clusters=[]
            for did in ids:
                group=[c for row,c in common if row['DialogueID']==did]
                clusters.append((sum(values(c,c['text'])[metric] for c in group),
                                 sum(values(c,caches[s][c['request_id']]['text'])[metric] for c in group),len(group)))
            h,sim,n=[sum(c[j] for c in clusters) for j in range(3)]
            rng=random.Random(SEED); gaps=[]
            for _ in range(REPLICATES):
                draw=rng.choices(clusters,k=len(clusters))
                gaps.append(sum(c[1]-c[0] for c in draw)/sum(c[2] for c in draw))
            gaps.sort()
            def q(p):
                x=(len(gaps)-1)*p; i=int(x)
                return gaps[i]+(gaps[min(i+1,len(gaps)-1)]-gaps[i])*(x-i)
            result[f'{metric}/{s}']=dict(human_hits=h,simulator_hits=sim,denominator=n,gap=(sim-h)/n,low=q(.025),high=q(.975))
            lines.append(f'|{label}|{s}|{h}/{n}={h/n:.6f}|{sim}/{n}={sim/n:.6f}|{(sim-h)/n:+.6f} [{q(.025):+.6f}, {q(.975):+.6f}]|')
    dump(out/'report.json',dict(seed=SEED,replicates=REPLICATES,results=result))
    (out/'report.md').write_text('\n'.join(lines)+'\n')


async def main(args):
    if args.limit_dialogues is not None and args.limit_dialogues < 1:
        raise ValueError('limit must be positive')
    chosen=targets(load(),args.limit_dialogues)
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    for setting in args.settings:
        dest=out/setting; dest.mkdir(parents=True,exist_ok=True)
        manifest=dict(ids=[r['DialogueID'] for r,_ in chosen], request_ids=[c['request_id'] for _,ts in chosen for _,c in ts],setting=setting,selection='CSDS load order; QRole=用户; negative_agent existing rule',provider='spark',model='spark-x2.6')
        if (dest/'sampled_ids.json').exists() and json.loads((dest/'sampled_ids.json').read_text()) != manifest:
            raise ValueError('Existing manifest differs')
        dump(dest/'sampled_ids.json',manifest)
        dump(dest/'summary_audit.json',[dict(dialogue_id=r['DialogueID'],original=r['UserSumm'],goal=clean_summary(r['UserSumm'],r['DialogueID'])[0]) for r,_ in chosen])
        cache=dest/'generations.jsonl'
        completed={g['request_id']:g for g in map(json.loads,cache.read_text().splitlines())} if cache.exists() else {}
        jobs=[]
        for row,ts in chosen:
            for turn,c in ts:
                messages=ablation_prompt(row,turn,setting)
                fingerprint=hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
                if c['request_id'] in completed:
                    if completed[c['request_id']]['prompt_sha256'] != fingerprint:
                        raise ValueError('Cached prompt differs')
                else:
                    jobs.append((messages,fingerprint,c))
        if args.report_only and jobs:
            raise ValueError('Missing generations')
        client=Spark(dest,args.concurrency) if jobs and not args.report_only else None
        async def generate(messages,fingerprint,c):
            text=await client.generate(messages,c['request_id'])
            item=dict(request_id=c['request_id'],dialogue_id=c['dialogue_id'],turn=c['turn'],text=text,status='moderated' if text is None else 'ok',prompt_sha256=fingerprint)
            with cache.open('a') as f:
                f.write(json.dumps(item,ensure_ascii=False)+'\n')
        tasks=[asyncio.create_task(generate(*j)) for j in jobs] if client else []
        try:
            await asyncio.gather(*tasks)
        except Exception:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            raise
        finally:
            if client: await client.close()
        records=list(map(json.loads,(dest/'request_usage.jsonl').read_text().splitlines()))
        usage=dict(request_count=len(records),**{k:sum(r.get(k,0) for r in records) for k in ('input_tokens','output_tokens','reasoning_tokens')})
        dump(dest/'token_usage.json',usage)
        print(setting,json.dumps(usage),flush=True)
    write_report(out,chosen)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--settings',nargs='+',choices=SETTINGS,default=list(SETTINGS))
    p.add_argument('--limit-dialogues',type=int)
    p.add_argument('--out',default='outputs/ablation_a')
    p.add_argument('--concurrency',type=int,default=4)
    p.add_argument('--report-only',action='store_true')
    asyncio.run(main(p.parse_args()))
