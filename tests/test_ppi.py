"""Exact finite-population expectations make removing correction fail."""
from itertools import combinations
import random
import pytest
from usersim.ppi import estimate, interval, clustered, simulate


def test_ppi_design_unbiased_unequal_dialogue_sizes():
    # Cluster totals on 1, 2, 4, 8 turns; Spark has a large systematic bias.
    spark = [1,2,4,8]
    gemini = [0,-1,2,-3]
    total_turns = 15
    full = sum(spark)/total_turns
    target = sum(gemini)/total_turns
    estimates = [estimate(full,[gemini[i]-spark[i] for i in draw],4,total_turns)
                 for draw in combinations(range(4),2)]
    assert sum(estimates)/len(estimates)==pytest.approx(target,abs=1e-12)
    # Mutation witness: deleting the paired correction leaves Spark's biased mean.
    assert abs(full-target)>.5


def test_bootstrap_resamples_dialogues_reproducibly():
    a = interval(.4,[-3,0,1,4],8,30,random.Random(7),100)
    b = interval(.4,[-3,0,1,4],8,30,random.Random(7),100)
    assert a==b and a[0]<a[1]


def test_perfect_spark_correction_zero_width():
    yes = dict(topic='continue',emotion=True)
    no = dict(topic='close',emotion=False)
    pairs = [dict(dialogue_id=d,group='all',spark={'human':no,'baseline':yes if d%2 else no},
             gemini={'human':no,'baseline':yes if d%2 else no}) for d in range(8) for _ in range(d+1)]
    result = simulate(pairs,['baseline'],4,7,repetitions=10,bootstrap=30)
    for row in result['statistics']:
        assert row['coverage']==1
        assert row['ppi_median_width']==0
        assert all(r['point']==row['gemini_full'] for r in row['replicates'])
    with pytest.raises(ValueError):
        simulate(pairs,['baseline'],9,7,10,30)


def test_simulation_corrects_biased_spark_end_to_end():
    # Binary labels on unequal clusters: Spark always predicts simulator
    # continue/emotion, while Gemini's paired delta is identically zero.
    # Averaging the actual simulation points must recover the full Gemini gap.
    no = dict(topic='close',emotion=False)
    yes = dict(topic='continue',emotion=True)
    pairs = [dict(dialogue_id=d,group='all',spark={'human':no,'baseline':yes},
                  gemini={'human':no,'baseline':no})
             for d in range(12) for _ in range(1+d%4)]
    result = simulate(pairs,['baseline'],6,20261027,repetitions=500,bootstrap=8)
    for row in result['statistics']:
        mean_point = sum(r['point'] for r in row['replicates'])/500
        assert abs(mean_point-row['gemini_full'])<.035
        assert row['spark_full']==1
