"""Offline contract audit: frozen inputs, provenance, metrics and credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import run_correction_decision as run
from usersim.judge import input_sha256, parse
from scripts.measure_reaction import predictions
from scripts.validate_judge import cached


def verify(n=500):
    if n==500 and (run.OUT/'preregistration_ppi.json').exists():
        from scripts.check_correction_decision_ppi import verify as verify_ppi
        return verify_ppi()
    out=run.OUT
    chosen,plans,baseline=run.prepare(out)
    run.audit_generations(out,chosen,plans,baseline,n)
    text=(ROOT/'config/judge_prompt.md').read_text()
    assert hashlib.sha256(text.encode()).hexdigest()==run.read(ROOT/'outputs/judge_validation/final_prompt.json')['sha256']
    todo,eligible,excluded=run.judge_inputs(out,chosen,baseline,n)
    assert todo==run.read(out/f'judge_inputs_{n}.json')
    imports=run.read(out/'reused_judgments.json')
    original=run.reusable()
    responses={}
    for sha,entry in imports.items():
        assert original[sha]==entry
        path=ROOT/entry['cache']
        if path not in responses: responses[path]=run.lines(path)
        matches=[r for r in responses[path] if r['event']=='response' and r['id']==entry['id'] and r['attempt']==entry['attempt'] and r['status']=='ok']
        assert len(matches)==1 and matches[0]['prediction']==entry['prediction']
        assert parse(matches[0]['content'])==entry['prediction']
        dispatch=[r for r in responses[path] if r['event']=='dispatch' and r['id']==entry['id'] and r['attempt']==entry['attempt']]
        assert len(dispatch)==1 and dispatch[0].get('input_sha256')==sha
    rows=run.lines(out/'judgments.jsonl')
    dispatch={(r['id'],r['attempt']):r for r in rows if r['event']=='dispatch'}
    inputs={input_sha256(text,r):r for r in todo}
    for r in rows:
        assert r['model']=='gemini-3.8-flash' and r['provider']=='sandbox'
        if r['event']=='dispatch':
            assert r['input_sha256']==r['id'] and r['id'] in inputs
        else:
            assert (r['id'],r['attempt']) in dispatch
            assert r['status']!='parse_error'
            if r['status']=='ok': assert parse(r['content'])==r['prediction']
    assert len(dispatch)<=10000
    pred={k:v['prediction'] for k,v in imports.items()}
    pred.update(predictions(cached(out/'judgments.jsonl',allow_request_errors=True)))
    todo_by_id={v['id']:v for v in todo}
    v2_pairs={v['request_id']:v for v in run.read(ROOT/'outputs/correction_v2/paired_judgments_500.json')}
    expected=[]
    for r in eligible:
        rid=r['request_id']
        view={c:pred[input_sha256(text,todo_by_id[c+':'+rid])] for c in ('human',)+run.SOURCES}
        for source in ('human','baseline'):
            assert view[source]==v2_pairs[rid]['predictions'][source]
        assert plans[rid]['oracle']==view['human']['topic']
        expected.append(dict(r,predictions=view,targets={c:plans[rid][c] for c in run.CONDITIONS}))
    assert expected==run.read(out/f'paired_judgments_{n}.json')
    before=run.read(out/f'summary_{n}.json')
    rebuilt=run.report(out,chosen,baseline,n)
    assert before==rebuilt
    for metric,values in before['statistics'].items():
        hit=lambda p:p['topic']=='continue' if metric=='continue' else p['emotion']
        h=sum(hit(p['predictions']['human']) for p in expected)/len(expected)
        for c,v in values.items():
            s=sum(hit(p['predictions'][c]) for p in expected)/len(expected)
            assert v['human']==h and v['simulator']==s
            assert abs(v['gap']-(s-h))<1e-12
    for condition in run.CONDITIONS:
        for target in run.TARGETS:
            selected=[r for r in expected if r['targets'][condition]==target]
            hits=sum(r['predictions'][condition]['topic']==target for r in selected)
            assert before['adherence'][condition][target]==dict(n=len(selected),matches=hits,rate=hits/len(selected) if selected else None)
    for target in run.TARGETS:
        targets=sum(r['targets']['decision']==target for r in expected)
        actual=sum(r['predictions']['decision']['topic']==target for r in expected)
        assert before['decision_topic_distribution'][target]==dict(target_count=targets,actual_count=actual,
            target_fraction=targets/len(expected),actual_fraction=actual/len(expected))
    # Credential values are read for scanning only, never printed.
    import os,shlex,re
    secrets=[os.environ.get(k,'') for k in ('SPARK_API_KEY','SANDBOX_API_KEY')]
    env=ROOT/'.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if '=' in line and line.split('=',1)[0].removeprefix('export ').strip() in ('SPARK_API_KEY','SANDBOX_API_KEY'):
                secrets.extend(shlex.split(line.split('=',1)[1]))
    paths=[out]+[ROOT/p for p in ('usersim/correction_decision.py','scripts/run_correction_decision.py','scripts/check_correction_decision.py','tests/test_correction_decision.py')]
    for base in paths:
        for path in (base.rglob('*') if base.is_dir() else [base]):
            if path.is_file():
                content=path.read_text()
                assert not any(secret in content for secret in secrets if len(secret)>8), 'Credential scan failed'
                assert not re.search(r'Bearer\s+(?:sk-|[A-Za-z0-9]{24})',content), 'Credential pattern found'
    value=dict(verified=True,n=n,paired_turns=len(expected),judgment_fingerprints=len(inputs),
               reused_sources_checked=len(imports),credential_scan_passed=True,api_calls=0)
    run.dump(out/f'verification_{n}.json',value)
    return value


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--n',type=int,choices=(20,500),default=500)
    print(json.dumps(verify(ap.parse_args().n),ensure_ascii=False))
