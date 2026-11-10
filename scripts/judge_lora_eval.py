"""ADR-018 evaluation using frozen P2 inputs, caches and PPI implementation."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_correction_decision_ppi as prior
from scripts import run_correction_decision as a
from usersim.csds import load
from usersim.judge import Judge, input_sha256
from usersim.judge_spark import frozen_prompt, judge_batch, read_cache


def read(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def paid_stage(out, pending, prompt, allow_paid, client_factory=None, max_paid_usd=7.0):
    """The only paid entry point; authorization checked before client construction."""
    if not allow_paid or not pending:
        return {}
    with prior.sample_writer(out):
        if client_factory is None:
            class Client(prior.SampleJudge):
                def __init__(self):
                    a.MeteredJudge.__init__(self, out)
                    self.cache = out/'gemini_sample.jsonl'
                    self.allowed = {input_sha256(prompt, r) for r in pending}
                    self.allowed_ids = {r['id'] for r in pending}
                def record(self, row):
                    if row['event'] == 'dispatch':
                        bill = a.meter(out)
                        if bill['spent_usd'] + a.REQUEST_RESERVE_USD >= max_paid_usd:
                            raise RuntimeError('Paid billing plus request reserve reaches configured cap; stop')
                    Judge.record(self, row)
            client_factory = Client
        previous = lines(out/'gemini_sample.jsonl')
        read_cache(out/'gemini_sample.jsonl')  # unresolved paid dispatches must stop
        client = client_factory()
        try:
            result = {}
            for r in pending:
                sha = input_sha256(prompt,r)
                try:
                    result[sha] = client.evaluate_sample(prompt,r,previous)
                except RuntimeError:
                    terminal = lines(out/'gemini_sample.jsonl')
                    if terminal and terminal[-1].get('id') == sha and terminal[-1].get('status') == 'parse_error':
                        result[sha] = None
                    else:
                        raise
            return result
        finally:
            client.close()


def local_predictions(path):
    rows = lines(path)
    dispatch = {(r['id'],r['attempt']):r for r in rows if r['event']=='dispatch'}
    result = {}
    for r in rows:
        if r['event']=='response' and r['status'] in ('ok','parse_error'):
            try:
                result[dispatch[r['id'],r['attempt']]['input_sha256']] = a.parse_judgment(r['content'])
            except (ValueError, TypeError):
                result[dispatch[r['id'],r['attempt']]['input_sha256']] = None
    return result


def projection(reg, prompt, gemini, items=None):
    if items is None:
        source = read(a.OUT/'ppi_judge_inputs.json')
        human = [r for r in source if r['id'].startswith('human:') and int(r['id'].split(':')[1]) in reg['sample_ids']]
        # Unknown future outputs: conservative unique-input count before generation.
        reused = sum(input_sha256(prompt,r) in gemini for r in human)
        pending = len(human)*3-reused
        logical = len(human)*3
    else:
        unique = {input_sha256(prompt,r):r for r in items}
        reused = sum(k in gemini for k in unique)
        pending = len(unique)-reused
        logical = len(items)
    unit = read(a.OUT/'smoke_check.json')['usd_per_new_judgment']
    return dict(logical_inputs=logical, reusable_unique=reused, new_unique=pending,
                estimated_usd=pending*unit, retry_inclusive_estimated_upper_usd=pending*unit*3,
                cost_basis='P2 measured USD/judgment; 3 transient attempts; estimate, not a tariff guarantee')


def run(args):
    if args.max_paid_usd <= 0:
        raise ValueError('--max-paid-usd must be positive')
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    reg = read(a.OUT/'preregistration_ppi.json')
    if reg['population_ids'] != read(a.OUT/'inputs.json')['ids'] or len(reg['sample_ids']) != 100:
        raise ValueError('Frozen registration mismatch')
    a.frozen(out/'preregistration_ppi.json',reg)
    prompt = frozen_prompt(ROOT)
    gemini = prior.gemini_predictions()
    gemini.update(local_predictions(out/'gemini_sample.jsonl'))
    if args.plan:
        value = projection(reg,prompt,gemini)
        (out/'projection.json').write_text(json.dumps(value,indent=2)+'\n')
        print(json.dumps(value),flush=True)
        return value
    if not args.base or not args.lora:
        raise ValueError('--base and --lora required')
    rows = {r['DialogueID']:r for r in load()}
    eligible = read(a.OUT/'ppi_eligible.json')
    generations = {}
    for variant in ('base','lora'):
        records = lines(getattr(args,variant))
        if len({r['request_id'] for r in records}) != len(records):
            raise ValueError('Duplicate generations')
        if any(r.get('variant') != variant for r in records):
            raise ValueError('Generation variant mismatch')
        generations[variant] = {r['request_id']:r for r in records}
    items = []; support = []; excluded = Counter()
    from usersim.csds import user_turns
    total_turns = sum(len(user_turns(rows[d])) for d in reg['population_ids'])
    excluded['inherited_P2_ineligible'] = total_turns-len(eligible)
    for r in eligible:
        rid = r['request_id']
        if any(not generations[c].get(rid,{}).get('text','').strip() for c in ('base','lora')):
            excluded['missing_or_empty_generation'] += 1
            continue
        support.append(r)
        for c in ('human','base','lora'):
            text = None if c == 'human' else generations[c][rid]['text']
            items.append(dict(id=c+':'+rid,**a.judge_item(rows[r['dialogue_id']],r['turn'],text)))
    allowed = [r for r in items if int(r['id'].split(':')[1]) in reg['sample_ids']]
    plan = projection(reg,prompt,gemini,allowed)
    plan['billing_stop_usd'] = args.max_paid_usd
    print('Gemini preflight: '+json.dumps(plan),flush=True)
    (out/'projection.json').write_text(json.dumps(plan,indent=2)+'\n')
    if args.mock:
        label = dict(topic='close',emotion=False)
        spark = {r['id']:dict(status='ok',prediction=label) for r in items}
        gemini.update({input_sha256(prompt,r):label for r in allowed})
    else:
        if not args.run_spark:
            raise ValueError('Use --run-spark for network Spark judging, or --mock')
        counts = prior.spark_failure_counts(read_cache(out/'spark_judgments.jsonl',allow_pending=True))
        prior.enforce_spark_parse_rate(counts)
        def completed(row):
            counts[row['status']]+=1
            prior.enforce_spark_parse_rate(counts)
        spark = judge_batch(out/'spark_judgments.jsonl',items,prompt=prompt,thinking='enabled',
            reuse_caches=[a.OUT/'spark_judgments.jsonl'],on_result=completed)
        valid_ids = {r['request_id'] for r in support if all(spark[c+':'+r['request_id']]['status']=='ok' for c in ('human','base','lora'))}
        allowed = [r for r in allowed if r['id'].split(':',1)[1] in valid_ids]
        pending = list({input_sha256(prompt,r):r for r in allowed if input_sha256(prompt,r) not in gemini}.values())
        gemini.update(paid_stage(out,pending,prompt,args.allow_paid,max_paid_usd=args.max_paid_usd))
    by_id = {r['id']:r for r in items}
    pairs = []
    for r in support:
        rid = r['request_id']; cs=('human','base','lora')
        if any(spark[c+':'+rid]['status'] != 'ok' for c in cs):
            reason = 'judge_transport_failed' if any(spark[c+':'+rid]['status']=='transport_failed' for c in cs) else 'judge_parse_or_moderation'
            excluded[reason] += 1
            continue
        g = {}
        if r['dialogue_id'] in reg['sample_ids']:
            keys = {c:input_sha256(prompt,by_id[c+':'+rid]) for c in cs}
            if any(k not in gemini for k in keys.values()):
                raise RuntimeError('Registered Gemini sample incomplete; use --allow-paid to authorize missing judgments')
            if any(gemini[k] is None for k in keys.values()):
                excluded['gemini_parse_missing'] += 1
                continue
            g = {c:gemini[k] for c,k in keys.items()}
        pairs.append(dict(r,spark={c:spark[c+':'+rid]['prediction'] for c in cs},gemini=g))
    if not pairs:
        raise ValueError('No paired valid turns')
    results = {}
    # Reuse P2 compute unchanged: its source aliases all point to this variant.
    # Only baseline's gap is exported; adherence/reduction diagnostics are discarded.
    for variant in ('base','lora'):
        adapted = [dict(r,targets={c:'close' for c in a.CONDITIONS},
                    spark={c:r['spark']['human' if c=='human' else variant] for c in ('human',)+a.SOURCES},
                    gemini={c:r['gemini']['human' if c=='human' else variant] for c in ('human',)+a.SOURCES} if r['gemini'] else {}) for r in pairs]
        value = prior.compute(reg,adapted)
        results[variant] = {method:{m:v['baseline'] for m,v in metrics.items()} for method,metrics in value['statistics'].items()}
    p2 = read(a.OUT/'summary_500.json')
    report = ['# LoRA evaluation', '', f'Mock: {args.mock}; paired turns: {len(pairs)}; exclusions: {dict(excluded)}',
        'PPI: frozen 500/100 dialogue design, 2000 paired dialogue bootstrap; gaps = simulator − human. P2 numbers retain their original support.',
        'ADR-018 adoption was post-hoc (one preregistered efficiency cell failed).', '',
        '|method|condition|metric|gap|95% CI|','|---|---|---|---:|---|']
    for method in ('ppi','spark_only','gemini_only'):
        for c in ('base','lora'):
            for m,v in results[c][method].items():
                report.append(f'|{method}|{c}|{m}|{v["gap"]:.6f}|{v["gap_ci95"]}|')
        for m,values in p2['statistics'][method].items():
            for c,v in values.items():
                report.append(f'|{method}|P2 {c}|{m}|{v["gap"]:.6f}|{v["gap_ci95"]}|')
    v2 = read(ROOT/'outputs/correction_v2/summary_500.json')
    for m,values in v2['statistics'].items():
        for c,v in values.items():
            report.append(f'|Gemini full (P2 v2; N={v2["paired_turns"]})|{c}|{m}|{v["gap"]:.6f}|{v["gap_ci95"]}|')
    report += ['',json.dumps(plan), '', 'Cost estimates use historical average; they are not guaranteed provider price caps.']
    (out/'report.md').write_text('\n'.join(report)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(mock=args.mock,results=results,paired_turns=len(pairs),excluded=dict(excluded),projection=plan),indent=2)+'\n')
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path)
    p.add_argument('--lora',type=Path)
    p.add_argument('--output',type=Path,default=ROOT/'outputs/lora_eval')
    p.add_argument('--plan',action='store_true')
    p.add_argument('--mock',action='store_true')
    p.add_argument('--run-spark',action='store_true')
    p.add_argument('--allow-paid',action='store_true')
    p.add_argument('--max-paid-usd',type=float,default=7.0)
    run(p.parse_args())


if __name__ == '__main__':
    main()
