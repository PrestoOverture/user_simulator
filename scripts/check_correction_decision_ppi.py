"""Offline verification of A's registered mixed-judge/PPI evaluation."""
from collections import Counter
import hashlib
import math
from unittest.mock import patch
from scripts import run_correction_decision_ppi as p
from usersim.correction_decision import sample_dialogues, parse_judgment
from usersim.judge import input_sha256
from usersim.judge_spark import read_cache,parse as spark_parse
from usersim import ppi


def verify():
    with patch('httpx.Client.request',side_effect=RuntimeError('Offline verifier forbids API calls')),patch('httpx.AsyncClient.request',side_effect=RuntimeError('Offline verifier forbids API calls')):
        return _verify()


def _verify():
    a=p.a;out=p.OUT
    reg,todo,eligible,prompt=p.prepare()
    assert reg['sample_ids']==sample_dialogues(reg['population_ids']) and len(reg['sample_ids'])==100
    assert len(reg['population_ids'])==500
    assert reg['ppi_source_sha256']==hashlib.sha256((p.ROOT/'usersim/ppi.py').read_bytes()).hexdigest()
    support,spark=p.support_and_spark(todo,prompt)
    assert p.build_support(spark,eligible)==a.read(out/'ppi_support.json')
    allowed=p.registered_inputs(reg,todo,support)
    assert allowed==a.read(out/'gemini_sample_inputs.json')
    allowed_sha={input_sha256(prompt,r) for r in allowed}
    if (out/'gemini_sample.jsonl').exists():
        assert (out/'preregistration_ppi.json').stat().st_birthtime <= (out/'gemini_sample.jsonl').stat().st_birthtime
    all_sha={input_sha256(prompt,r) for r in todo}
    spark_rows=read_cache(out/'spark_judgments.jsonl')
    p.enforce_spark_parse_rate(p.spark_failure_counts(spark_rows))
    donors={}
    terminals=set()
    for row in spark_rows:
        assert row['model']=='spark-x2.6' and row['provider']=='spark' and row['thinking']=='enabled'
        sha=row['input_sha256'];assert sha in all_sha
        if row['event']=='reuse':
            path=p.Path(row['reused_from']).resolve()
            assert path.parent==p.C.resolve()
            if path not in donors:
                donors[path]={r['id']:r for r in read_cache(path,allow_pending=True) if r.get('status')=='ok' and r['thinking']=='enabled'}
            source=donors[path][row['reused_id']]
            assert source['input_sha256']==sha and source['prediction']==row['prediction']
        elif row['event']=='response' and row.get('status')=='ok':
            assert spark_parse(row['content'])==row['prediction']
        elif row['event']=='response' and row.get('status')=='parse_error':
            try:spark_parse(row['content'])
            except (ValueError,TypeError):pass
            else:raise AssertionError('Spark parse_error is unexpectedly parseable')
        if row.get('status') in ('ok','moderated','parse_error'):
            assert sha not in terminals,'Duplicate terminal Spark fingerprint'
            terminals.add(sha)
    recovered=0;paid=0
    for path in (out/'judgments.jsonl',out/'gemini_sample.jsonl'):
        rows=a.lines(path)
        dispatch={(r['id'],r['attempt']):r for r in rows if r['event']=='dispatch'}
        responses=[r for r in rows if r['event']=='response']
        assert Counter(dispatch.keys())==Counter((r['id'],r['attempt']) for r in responses)
        assert len(dispatch)==sum(r['event']=='dispatch' for r in rows)
        paid+=len(dispatch)
        for row in rows:
            assert row['model']=='gemini-3.8-flash' and row['provider']=='sandbox'
            if row['event']=='dispatch':
                assert row['input_sha256']==row['id'] and row['input_sha256'] in all_sha
                if path.name=='gemini_sample.jsonl':assert row['input_sha256'] in allowed_sha
            elif row.get('status')=='abandoned_in_flight':
                assert path.name=='gemini_sample.jsonl' and row['usage'] is None
            elif row.get('status') in ('ok','parse_error'):
                label=parse_judgment(row['content'])
                if row['status']=='ok':assert label==row['prediction']
                else:recovered+=1
    assert paid<=10000
    assert a.read(out/'gemini_reparse.json')['recovered']==recovered
    # Exact v2 import provenance, indexed once rather than quadratic cache scans.
    old=a.reusable(); needed={input_sha256(prompt,r) for r in todo if r['id'].split(':')[0] in ('human','baseline')}
    source_indices={}
    for sha in needed:
        entry=old[sha];path=p.ROOT/entry['cache']
        if path not in source_indices:
            raw=a.lines(path)
            source_indices[path]=({(r['id'],r['attempt']):r for r in raw if r['event']=='dispatch'},
                                  {(r['id'],r['attempt']):r for r in raw if r['event']=='response'})
        ds,rs=source_indices[path];key=entry['id'],entry['attempt']
        assert ds[key]['input_sha256']==sha
        assert rs[key]['status']=='ok' and parse_judgment(rs[key]['content'])==entry['prediction']==rs[key]['prediction']
    reg,pairs=p.paired_data()
    assert pairs==a.read(out/'paired_judgments_500.json')
    summary=a.read(out/'summary_500.json')
    calculated=p.compute(reg,pairs)
    assert all(summary[k]==v for k,v in calculated.items())
    N=len(pairs);M=len(reg['population_ids']);sample=reg['sample_ids']
    for metric in ('continue','emotion'):
        hit=lambda x:int(x['topic']=='continue') if metric=='continue' else int(x['emotion'])
        for condition in a.SOURCES:
            full=sum(hit(r['spark'][condition])-hit(r['spark']['human']) for r in pairs)/N
            residual={d:0 for d in sample}
            for r in pairs:
                if r['dialogue_id'] in residual:
                    residual[r['dialogue_id']]+=(hit(r['gemini'][condition])-hit(r['gemini']['human'])-hit(r['spark'][condition])+hit(r['spark']['human']))
            direct=ppi.estimate(full,list(residual.values()),M,N)
            assert math.isclose(direct,summary['statistics']['ppi'][metric][condition]['gap'],abs_tol=1e-12)
    for condition in a.CONDITIONS:
        for target in p.TARGETS:
            denom=sum(r['targets'][condition]==target for r in pairs)
            if not denom:continue
            hit=lambda r,labels:int(r['targets'][condition]==target and labels[condition]['topic']==target)
            full=sum(hit(r,r['spark']) for r in pairs)/denom
            residual=[sum(hit(r,r['gemini'])-hit(r,r['spark']) for r in pairs if r['dialogue_id']==d) for d in sample]
            assert math.isclose(ppi.estimate(full,residual,M,denom),summary['rates']['ppi'][f'{condition}/adherence/{target}']['estimate'],abs_tol=1e-12)
    for topic in p.TARGETS:
        full=sum(r['spark']['decision']['topic']==topic for r in pairs)/N
        residual=[sum(int(r['gemini']['decision']['topic']==topic)-int(r['spark']['decision']['topic']==topic) for r in pairs if r['dialogue_id']==d) for d in sample]
        assert math.isclose(ppi.estimate(full,residual,M,N),summary['rates']['ppi'][f'decision/topic/{topic}']['estimate'],abs_tol=1e-12)
    assert summary['c_adoption']==a.read(out/'ppi_adoption.json') and summary['c_adoption']['status']=='complete'
    assert summary['c_adoption']['authorization']==a.read(out/'ppi_adoption_authorization.json')
    assert summary['c_adoption']['authorization']['decision']=='adopt_ppi_post_hoc'
    assert summary['c_adoption']['authorization_sha256']==hashlib.sha256((out/'ppi_adoption_authorization.json').read_bytes()).hexdigest()
    assert summary['c_adoption']['summary_sha256']==hashlib.sha256((p.C/'summary.json').read_bytes()).hexdigest()
    assert a.read(p.C/'summary.json')['status']=='complete'
    assert summary['c_adoption']['adoption_passed']==a.read(p.C/'summary.json')['adoption_passed']
    assert a.read(out/'preregistration_before_paid.json')['sha256']==hashlib.sha256((out/'preregistration_ppi.json').read_bytes()).hexdigest()
    # Rebuild all outputs, checking actual deterministic bytes.
    paths=[out/'report_500.md',out/'summary_500.json']
    before=[hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    p.report()
    assert before==[hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    import os,shlex,re
    secrets=[os.environ.get(k,'') for k in ('SPARK_API_KEY','SANDBOX_API_KEY')]
    env=p.ROOT/'.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if '=' in line and line.split('=',1)[0].removeprefix('export ').strip() in ('SPARK_API_KEY','SANDBOX_API_KEY'):
                secrets.extend(shlex.split(line.split('=',1)[1]))
    paths=list(out.rglob('*'))+[p.ROOT/'usersim/correction_decision.py']+list((p.ROOT/'scripts').glob('*correction_decision*.py'))+[p.ROOT/'tests/test_correction_decision.py']
    for path in paths:
        if not path.is_file() or path.suffix=='.pyc':continue
        content=path.read_bytes()
        assert not any(secret.encode() in content for secret in secrets if len(secret)>8),'Credential scan failed'
        assert not re.search(rb'Bearer\s+(?:sk-|[A-Za-z0-9]{24})',content),'Credential pattern found'
    value=dict(verified=True,n=500,api_calls=0,gemini_sample_dialogues=100,paired_turns=N,
               spark_fingerprints=len(terminals),gemini_reparsed=recovered,credential_scan_passed=True,ppi_recomputed=True)
    a.dump(out/'verification_500.json',value)
    return value
