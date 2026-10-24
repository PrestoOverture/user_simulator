"""Validate artifacts against raw CSDS, rather than trusting reported counts."""
import csv
import json
import hashlib
from collections import defaultdict
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from usersim.csds import load, user_turns, sample, clean_summary, summary_audit
from usersim.replay import prompt
from usersim.metrics import measure

FILES=['spark_check.md','data_format_notes.md','sampled_ids.json','prompt_template.md','side_by_side.csv',
       'metrics_by_turn.csv','metrics_summary.md','token_usage.json','report.md']


def read_csv(path):
    with Path(path).open(newline="") as stream:
        return list(csv.DictReader(stream))


def check(out,n,raw):
    out=Path(out)
    for name in FILES:
        assert (out/name).is_file() and (out/name).stat().st_size>0,f'{out/name}: missing or empty'
    manifest=json.loads((out/'sampled_ids.json').read_text())
    assert isinstance(manifest['seed'],int),'seed missing/invalid'
    ids=manifest['ids']
    assert len(ids)==len(set(ids))==n,f'{out}: ID count'
    assert [r['DialogueID'] for r in sample(list(raw.values()),n,manifest['seed'],manifest.get('excluded_ids',[]))]==ids,'sample reproducibility'
    expected={(i,t) for i in ids for t in range(1,len(user_turns(raw[i]))+1)}
    records=[json.loads(s) for s in (out/'request_usage.jsonl').read_text().splitlines()]
    records=[r for r in records if not r['request_id'].startswith('check-')]
    grouped=defaultdict(list)
    for record in records:
        grouped[record['request_id']].append(record)
        if record.get('http_status')==400 and str(record.get('error_code'))=='10013':
            assert record['status']=='moderated', '10013 must be terminal moderated'
    assert set(grouped)=={f'{i}:{t}' for i,t in expected}, 'request IDs'
    moderated_records=[r for r in records if r['status']=='moderated']
    assert all(r.get('http_status')==400 and str(r.get('error_code'))=='10013' for r in moderated_records), 'moderation code'
    moderated_keys={(int(r['request_id'].split(':')[0]),int(r['request_id'].split(':')[1])) for r in moderated_records}
    assert len(moderated_keys)==len(moderated_records), 'duplicate moderation'
    for rid, attempts in grouped.items():
        assert attempts[-1]['status'] in ('ok','moderated'), 'exhausted/unresolved request'
        assert all(r['status']=='error' for r in attempts[:-1]), 'duplicate terminal result'
        assert len(attempts)<=4 and [r['attempt'] for r in attempts]==list(range(1,len(attempts)+1)), 'retry sequence'
    moderated=json.loads((out/'moderated.json').read_text())
    assert len(moderated)==len(moderated_keys) and {(r['dialogue_id'],r['turn']) for r in moderated}==moderated_keys, 'moderated log mismatch'
    planned=expected.copy()
    expected-=moderated_keys
    pairs=read_csv(out/'side_by_side.csv')
    metrics=read_csv(out/'metrics_by_turn.csv')
    key=lambda r:(int(r['dialogue_id']),int(r['turn']))
    assert len(pairs)==len(expected) and {key(r) for r in pairs}==expected,'side_by_side turn count/keys'
    assert len(metrics)==2*len(expected),'metrics total count'
    for source in ['human','simulator']:
        group=[r for r in metrics if r['source']==source]
        assert len(group)==len(expected) and {key(r) for r in group}==expected,'metrics source count/keys'
    assert sum(not p['simulator'].strip() for p in pairs)/len(pairs)<.02,'empty Spark outputs >=2%'
    bykey={key(r):r for r in pairs}
    metric_map={(key(r),r['source']):r for r in metrics}
    for i in ids:
        assert raw[i]['QRole']=='用户','non-user role'
        previous={'human':[],'simulator':[]}
        for t,turn in enumerate(user_turns(raw[i]),1):
            if (i,t) in moderated_keys:
                continue
            p=bykey[i,t]
            assert p['human']=='\n'.join(turn['messages']),'human text mismatch'
            for source in ['human','simulator']:
                text=p[source]
                count=len(turn['messages']) if source=='human' else len([s for s in text.splitlines() if s.strip()])
                actual=measure(text,previous[source],t==1,count,source)
                recorded=metric_map[((i,t),source)]
                assert all(str(v)==recorded[k] for k,v in actual.items()),'metric mismatch'
                previous[source].append(text)
    usage=json.loads((out/'token_usage.json').read_text())
    for name in ['input_tokens','output_tokens','request_count','elapsed_seconds','full_projection']:
        assert usage[name],f'usage {name} missing/zero'
    assert usage['model']=='spark-x2.6' and usage['thinking']=={'type':'disabled'}
    assert len(records)==usage['request_count'], 'request count'
    assert usage['successful_requests']==len(expected), 'success count'
    assert usage['moderated_count']==len(moderated_keys), 'moderated count'
    assert usage['moderated_ratio']==len(moderated_keys)/len(planned), 'moderated ratio'
    assert usage['moderated_dialogue_ids']==sorted({i for i,t in moderated_keys}), 'moderated IDs'
    assert usage['planned_user_turns']==manifest['user_turns']==len(planned), 'planned count'
    assert usage['usage_unknown_requests']==sum(not r.get('usage_known',False) for r in records)
    for field in ['input_tokens','output_tokens']:
        assert sum(r[field] for r in records)==usage[field],f'{field} mismatch'
    assert usage['total_tokens']==usage['input_tokens']+usage['output_tokens']
    projection=usage['full_projection']
    all_count=sum(len(user_turns(r)) for r in raw.values())
    users_count=sum(len(user_turns(r)) for r in raw.values() if r['QRole']=='用户')
    assert projection['all_csds_user_turns']==all_count and projection['user_role_user_turns']==users_count
    assert projection['all_csds_tokens']==round(usage['total_tokens']/len(planned)*all_count)
    assert projection['user_role_tokens']==round(usage['total_tokens']/len(planned)*users_count)
    assert usage['user_turns']==len(expected)
    assert not (out/'STOPPED.md').exists(), 'stopped run'
    assert json.loads((out/'summary_audit.json').read_text())==[summary_audit(raw[i]) for i in ids], 'summary audit mismatch'
    generations=[json.loads(s) for s in (out/'generations.jsonl').read_text().splitlines()]
    assert len(generations)==len(planned), 'generation count'
    for g in generations:
        row=raw[g['dialogue_id']];turn=user_turns(row)[g['turn']-1]
        fingerprint=hashlib.sha256(json.dumps(prompt(row,turn),ensure_ascii=False).encode()).hexdigest()
        assert g['prompt_sha256']==fingerprint, 'prompt fingerprint mismatch'
        assert (g['status']=='moderated')==((g['dialogue_id'],g['turn']) in moderated_keys), 'generation status'
        if g['status']=='ok':
            assert g['text']==bykey[g['dialogue_id'],g['turn']]['simulator'], 'generation text mismatch'
    report=(out/'report.md').read_text()
    assert all(f'## {i}.' in report for i in range(1,7)),'six report answers'
    if n==500:
        assert '20段与500段对比' in report,'missing comparison'
    print(f'{out}: OK; dialogues={n}, user_turns={len(expected)}, empty={sum(not p["simulator"].strip() for p in pairs)}')
    return set(ids),manifest['seed']


def main(paths):
    assert len(paths)==2,'Provide outputs and outputs/extended'
    raw={r['DialogueID']:r for r in load()}
    a,seed_a=check(paths[0],20,raw); b,seed_b=check(paths[1],500,raw)
    assert not a & b,'overlapping samples'
    assert seed_a!=seed_b,'seeds must differ'
    print('PASS: nine artifacts, disjoint samples, turn alignment, text/metrics, tokens and report structure')


if __name__=='__main__':
    try:
        main(sys.argv[1:])
    except (AssertionError,KeyError,ValueError,OSError,ZeroDivisionError) as exc:
        print(f'FAIL: {exc}',file=sys.stderr)
        sys.exit(1)
