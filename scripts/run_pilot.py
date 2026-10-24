"""Run reusable CSDS replay: --n --seed --out; resume successful turns."""
import argparse
import asyncio
import csv
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim.csds import load, sample, user_turns, clean_summary, summary_audit
from usersim.replay import prompt, template
from usersim.metrics import measure
from usersim.spark import Spark
from usersim.report import write_reports


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def write_csv(path, rows):
    with path.open('w', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def main(args):
    start=time.monotonic()
    out=Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rows=load()
    excluded=json.loads(Path(args.exclude).read_text())['ids'] if args.exclude else []
    chosen=sample(rows,args.n,args.seed,excluded)
    manifest=dict(seed=args.seed, ids=[r['DialogueID'] for r in chosen], n=args.n,
                  excluded_ids=excluded, preferred_range=[6,30], role='用户',
                  user_turns=sum(len(user_turns(r)) for r in chosen))
    if (out/'sampled_ids.json').exists() and json.loads((out/'sampled_ids.json').read_text()) != manifest:
        raise ValueError('Existing manifest differs; use a different output directory')
    dump(out/'sampled_ids.json',manifest)
    (out/'prompt_template.md').write_text(template())
    if out.resolve()!=Path('outputs').resolve():
        for name in ['spark_check.md','data_format_notes.md']:
            shutil.copyfile(Path('outputs')/name,out/name)
    dump(out/'summary_audit.json',[summary_audit(r) for r in chosen])
    completed={}
    cache=out/'generations.jsonl'
    if cache.exists():
        for line in cache.read_text().splitlines():
            item=json.loads(line); completed[item['request_id']]=item
    jobs=[]
    for row in chosen:
        for t,turn in enumerate(user_turns(row),1):
            jobs.append((row,t,turn,f"{row['DialogueID']}:{t}"))
    client = None if args.refresh_only else Spark(out,args.concurrency)
    async def generate(row,t,turn,rid):
        messages = prompt(row,turn)
        fingerprint = hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
        if rid in completed:
            if completed[rid].get('prompt_sha256') != fingerprint:
                raise ValueError('Cached generation prompt differs; rerun in a fresh directory')
            return
        content=await client.generate(messages,rid)
        item=dict(request_id=rid,dialogue_id=row['DialogueID'],turn=t,text=content,
                  status='moderated' if content is None else 'ok', prompt_sha256=fingerprint)
        completed[rid]=item
        with cache.open('a') as f:
            f.write(json.dumps(item,ensure_ascii=False)+'\n')
        if len(completed)%50==0:
            print(f'completed {len(completed)}/{len(jobs)}',flush=True)
    if args.refresh_only:
        for row, t, turn, rid in jobs:
            fingerprint = hashlib.sha256(json.dumps(prompt(row, turn), ensure_ascii=False).encode()).hexdigest()
            if rid not in completed or completed[rid].get('prompt_sha256') != fingerprint:
                raise ValueError(f'{rid}: missing or stale generation; rerun affected turns first')
    tasks=[] if args.refresh_only else [asyncio.create_task(generate(*job)) for job in jobs]
    try:
        await asyncio.gather(*tasks)
    except Exception:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        (out/'STOPPED.md').write_text('Spark exhausted retries; pipeline stopped. See request_usage.jsonl. No model fallback.\n')
        raise
    finally:
        if client is not None:
            await client.close()
    pairs=[]; metrics=[]; moderated=[]
    for row in chosen:
        previous={'human':[],'simulator':[]}
        for t,turn in enumerate(user_turns(row),1):
            human='\n'.join(turn['messages']); item=completed[f"{row['DialogueID']}:{t}"]; sim=item['text']
            if item['status'] == 'moderated':
                moderated.append(dict(dialogue_id=row['DialogueID'],turn=t,request_id=item['request_id']))
                # Human history remains real; excluded turns do not enter either metric series.
                continue
            # Context displays the last two contiguous speaker runs before the target.
            context=[]
            for u in row['Dialogue'][:turn['start']]:
                if not context or context[-1][0]!=u['speaker']:
                    context.append([u['speaker'],u['utterance']])
                else:
                    context[-1][1]+='\n'+u['utterance']
            pairs.append(dict(dialogue_id=row['DialogueID'],turn=t,context_last_two='\n'.join(s+': '+v for s,v in context[-2:]),human=human,simulator=sim))
            for source,text,count in [('human',human,len(turn['messages'])),('simulator',sim,len([s for s in sim.splitlines() if s.strip()]))]:
                metrics.append(dict(dialogue_id=row['DialogueID'],turn=t,source=source,
                                    **measure(text,previous[source],t==1,count,source)))
                previous[source].append(text)
    dump(out/'moderated.json',moderated)
    write_csv(out/'side_by_side.csv',pairs); write_csv(out/'metrics_by_turn.csv',metrics)
    records=[json.loads(line) for line in (out/'request_usage.jsonl').read_text().splitlines()]
    replay_records=[r for r in records if not r['request_id'].startswith('check-')]
    checks=[r for r in records if r['request_id'].startswith('check-')]
    inp=sum(r['input_tokens'] for r in replay_records); output=sum(r['output_tokens'] for r in replay_records)
    total=inp+output
    all_turns=sum(len(user_turns(r)) for r in rows)
    user_count=sum(len(user_turns(r)) for r in rows if r['QRole']=='用户')
    usage=dict(model='spark-x2.6',thinking={'type':'disabled'},temperature='omitted; official default 1',
               input_tokens=inp,output_tokens=output,total_tokens=total,request_count=len(replay_records),
               successful_requests=sum(r['status']=='ok' for r in replay_records),
               moderated_count=len(moderated),moderated_dialogue_ids=sorted({r['dialogue_id'] for r in moderated}),
               moderated_ratio=len(moderated)/len(jobs),planned_user_turns=len(jobs),
               usage_unknown_requests=sum(not r.get('usage_known',False) for r in replay_records),dialogues=len(chosen),
               user_turns=len(pairs),elapsed_seconds=(json.loads((out/'token_usage.json').read_text())['elapsed_seconds'] if args.refresh_only else time.monotonic()-start),
               request_elapsed_seconds=sum(r['elapsed_seconds'] for r in replay_records),
               check_usage=dict(request_count=len(checks),input_tokens=sum(r['input_tokens'] for r in checks),output_tokens=sum(r['output_tokens'] for r in checks)),
               full_projection=dict(formula='known sample replay tokens / planned sample user turns (including moderated) * population user turns; unknown rejected usage excluded',
                                    sample_tokens=total,sample_user_turns=len(jobs),all_csds_user_turns=all_turns,
                                    user_role_user_turns=user_count,all_csds_tokens=round(total/len(jobs)*all_turns),
                                    user_role_tokens=round(total/len(jobs)*user_count)))
    dump(out/'token_usage.json',usage)
    write_reports(out,metrics,pairs,usage,args.baseline)
    print(json.dumps(usage,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--n',type=int,required=True); parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--out',required=True); parser.add_argument('--exclude'); parser.add_argument('--baseline')
    parser.add_argument('--concurrency',type=int,default=4)
    parser.add_argument('--refresh-only',action='store_true',help='Validate cached prompts and rebuild metrics/reports without API calls')
    asyncio.run(main(parser.parse_args()))
