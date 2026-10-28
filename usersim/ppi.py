"""Finite-population paired PPI with dialogue sampling and bootstrap.

For M dialogues, known full paired turn count N and sample of n dialogues:
  gap_ppi = sum_all Spark_delta / N
            + M/(n*N) * sum_sample (Gemini_delta - Spark_delta).
Deltas are simulator indicator minus human indicator on exactly the same turns.
The Horvitz-Thompson correction is exactly design-unbiased even with unequal
cluster sizes. Gemini-only uses M/(n*N)*sum_sample Gemini_delta.
Spark's full population and N are fixed during bootstrap; sample whole dialogues.
"""
from collections import defaultdict
import random


def quantile(values, p):
    values = sorted(values)
    x = (len(values)-1)*p
    i = int(x)
    return values[i]+(values[min(i+1,len(values)-1)]-values[i])*(x-i)


def estimate(spark_full, residuals, population_dialogues, population_turns):
    if not residuals or population_turns <= 0:
        raise ValueError('Empty sample or population')
    return spark_full + population_dialogues*sum(residuals)/(len(residuals)*population_turns)


def interval(spark_full, residuals, population_dialogues, population_turns, rng, replicates=2000):
    draws = [estimate(spark_full,rng.choices(residuals,k=len(residuals)),population_dialogues,population_turns)
             for _ in range(replicates)]
    return [quantile(draws,.025),quantile(draws,.975)]


def clustered(pairs, conditions):
    """Each pair has dialogue_id, group, spark/gemini maps including human."""
    ids = sorted({p['dialogue_id'] for p in pairs})
    groups = sorted({p['group'] for p in pairs})
    keys = [(c,g,m) for c in conditions for g in groups for m in ('continue','emotion')]
    counts = defaultdict(int)
    spark = defaultdict(lambda: [0]*len(ids))
    gemini = defaultdict(lambda: [0]*len(ids))
    index = {d:i for i,d in enumerate(ids)}
    for p in pairs:
        for key in keys:
            c,g,m = key
            if p['group'] != g:
                continue
            hit = lambda label: int(label['topic']=='continue') if m=='continue' else int(label['emotion'])
            counts[key] += 1
            i = index[p['dialogue_id']]
            spark[key][i] += hit(p['spark'][c])-hit(p['spark']['human'])
            gemini[key][i] += hit(p['gemini'][c])-hit(p['gemini']['human'])
    return ids,keys,counts,dict(spark),dict(gemini)


def simulate(pairs, conditions, n, seed, repetitions=500, bootstrap=2000, reaction=False, reference_decision=None):
    ids,keys,counts,spark,gemini = clustered(pairs,conditions)
    population = len(ids)
    if n > population:
        raise ValueError('Sample exceeds dialogue population; do not resize')
    s_full = {k:sum(spark[k])/counts[k] for k in keys}
    target = {k:sum(gemini[k])/counts[k] for k in keys}
    residual = {k:[g-s for g,s in zip(gemini[k],spark[k])] for k in keys}
    rng = random.Random(seed)
    results = {k:[] for k in keys}
    interactions = []
    turn_counts = {k:[0]*population for k in keys}
    index = {d:i for i,d in enumerate(ids)}
    for pair in pairs:
        for k in keys:
            if pair['group']==k[1]:
                turn_counts[k][index[pair['dialogue_id']]] += 1
    full_decision = None
    # Joint full-Gemini bootstrap for ADR-016, preserving both groups.
    if reaction:
        neg,pos = ('simulator','negative','continue'),('simulator','nonnegative','continue')
        full_draws = []
        full_rng = random.Random(seed+1)
        for _ in range(bootstrap):
            draw = full_rng.choices(range(population),k=population)
            full_draws.append(population*sum(gemini[neg][i] for i in draw)/(population*counts[neg])
                              - population*sum(gemini[pos][i] for i in draw)/(population*counts[pos]))
        ci = [quantile(full_draws,.025),quantile(full_draws,.975)]
        full_decision = reference_decision or decision(target[neg],target[pos],ci)
    for repetition in range(repetitions):
        sample = rng.sample(range(population),n)
        points = {k:estimate(s_full[k],[residual[k][i] for i in sample],population,counts[k]) for k in keys}
        only_points = {k:estimate(0,[gemini[k][i] for i in sample],population,counts[k]) for k in keys}
        ppi_draws = {k:[] for k in keys}
        only_draws = {k:[] for k in keys}
        for _ in range(bootstrap):
            draw = rng.choices(sample,k=n)
            for k in keys:
                scale = population/(n*counts[k])
                ppi_draws[k].append(s_full[k]+scale*sum(residual[k][i] for i in draw))
                only_draws[k].append(scale*sum(gemini[k][i] for i in draw))
        for k in keys:
            ci = [quantile(ppi_draws[k],.025),quantile(ppi_draws[k],.975)]
            only_ci = [quantile(only_draws[k],.025),quantile(only_draws[k],.975)]
            results[k].append(dict(repetition=repetition,sampled_turns=sum(turn_counts[k][i] for i in sample),point=points[k],interval=ci,
                gemini_only_point=only_points[k],gemini_only_interval=only_ci))
        if reaction:
            draws = [a-b for a,b in zip(ppi_draws[neg],ppi_draws[pos])]
            ci = [quantile(draws,.025),quantile(draws,.975)]
            interactions.append(dict(repetition=repetition,point=points[neg]-points[pos],interval=ci,
                decision=decision(points[neg],points[pos],ci)))
    output = []
    for k in keys:
        rows = results[k]
        widths = [r['interval'][1]-r['interval'][0] for r in rows]
        only_widths = [r['gemini_only_interval'][1]-r['gemini_only_interval'][0] for r in rows]
        reduction = [1-w/o if o else None for w,o in zip(widths,only_widths)]
        finite = [v for v in reduction if v is not None]
        output.append(dict(condition=k[0],group=k[1],metric=k[2],n=n,population_dialogues=population,
            paired_turns=counts[k],gemini_full=target[k],spark_full=s_full[k],
            coverage=sum(r['interval'][0]<=target[k]<=r['interval'][1] for r in rows)/repetitions,
            ppi_median_width=quantile(widths,.5),gemini_only_median_width=quantile(only_widths,.5),
            median_width_reduction=1-quantile(widths,.5)/quantile(only_widths,.5) if quantile(only_widths,.5) else None,
            median_paired_width_reduction=quantile(finite,.5) if finite else None,
            median_sampled_turns=quantile([r['sampled_turns'] for r in rows],.5),
            sampled_turns_range=[min(r['sampled_turns'] for r in rows),max(r['sampled_turns'] for r in rows)],
            zero_gemini_width=len(rows)-len(finite),replicates=rows))
    return dict(statistics=output,full_decision=full_decision,interactions=interactions,
        decision_agreement=sum(r['decision']==full_decision for r in interactions)/repetitions if interactions else None)


def decision(negative_gap, nonnegative_gap, ci):
    return '限定在被拒之后' if (ci[0]>0 or ci[1]<0) and abs(negative_gap)>abs(nonnegative_gap) else '用普遍定义'
