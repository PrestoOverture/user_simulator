"""Offline independent data-origin, generation, and budget audit; no API calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from usersim.csds import load,user_turns
from usersim.replay import unsegment
from usersim.correction_v2 import previous_agent
from scripts.run_correction_v2 import prepare,audit_generations,OUT,read,credentials


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--n',type=int,choices=(20,500),required=True)
    args=ap.parse_args()
    chosen,plans,baseline=prepare(OUT)
    rows={r['DialogueID']:r for r in load()}
    excluded=set(read(OUT/'inputs.json')['excluded_ids'])
    checked=set()
    for p in plans.values():
        for item in p['examples']+[p['structure']]:
            key=item['dialogue_id'],item['turn']
            assert key[0] not in excluded
            row=rows[key[0]]
            assert row['split']=='train' and row['QRole']=='用户'
            turn=user_turns(row)[key[1]-1]
            text=unsegment('\n'.join(turn['messages']))
            assert item['chars']==len(re.sub(r'\s','',text))
            assert item['messages']==len(turn['messages'])
            if 'text' in item:
                assert item['text']==text
                assert item['agent']==previous_agent(row,turn)
            checked.add(key)
    audit_generations(OUT,chosen,plans,baseline,args.n)
    summary=read(OUT/f'summary_{args.n}.json')
    pairs=read(OUT/f'paired_judgments_{args.n}.json')
    assert summary['paired_turns']==len(pairs)
    for metric,values in summary['statistics'].items():
        def hit(p):
            return p['topic']=='continue' if metric=='continue' else p['emotion']
        human=sum(hit(p['predictions']['human']) for p in pairs)/len(pairs)
        for c,v in values.items():
            rate=sum(hit(p['predictions'][c]) for p in pairs)/len(pairs)
            assert abs(v['human']-human)<1e-12
            assert abs(v['simulator']-rate)<1e-12
            assert abs(v['gap']-(rate-human))<1e-12
            assert sum(summary['joint_counts'][c].values())==len(pairs)
    b=read(OUT/'budget.json')
    assert 0<=b['spent_usd']<=b['limit_usd']
    # No secret values may appear in any deliverable, including logs.
    credentials()
    secrets=[os.environ[name] for name in ('SPARK_API_KEY','SANDBOX_API_KEY')]
    files=[p for p in OUT.rglob('*') if p.is_file()]
    for path in files+[ROOT/'usersim/correction_v2.py',ROOT/'scripts/run_correction_v2.py']:
        data=path.read_text()
        assert all(secret and secret not in data for secret in secrets),f'Secret scan failed: {path.name}'
    print(json.dumps(dict(verified=True,n=args.n,independent_source_turns_checked=len(checked),
                         paired_turns=len(pairs),credential_scanned_files=len(files)+2,
                         spent_usd=b['spent_usd'],api_calls=0),ensure_ascii=False))


if __name__=='__main__':
    main()
