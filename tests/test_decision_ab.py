import pytest

from scripts.build_decision_ab import common_records, summarize
from scripts import check_outputs


def test_comparison_only_uses_common_valid_turns():
    raw = {1: dict(DialogueID=1, UserSumm=['用户询问售后。'], Dialogue=[
        dict(speaker=s, utterance=text) for s,text in
        [('Q','你好'), ('A','不能办理'), ('Q','好吧'), ('A','不能办理'), ('Q','谢谢')]])}
    spark = {(1,1): dict(human='你好',simulator='你好'),
             (1,3): dict(human='谢谢',simulator='好吧')}
    deepseek = {(1,1): dict(human='你好',simulator='你好'),
                (1,2): dict(human='好吧',simulator='好吧'),
                (1,3): dict(human='谢谢',simulator='好吧'),
                (99,1): dict(human='不在共同样本',simulator='不在共同样本')}
    records = common_records(spark,deepseek,raw)
    assert [(r['dialogue_id'],r['turn']) for r in records] == [(1,1),(1,3)]
    # The excluded DeepSeek-only second turn must not enter repetition history.
    assert records[-1]['values']['DeepSeek']['e_repeated'] == 0
    result = summarize(records, replicates=20)
    assert result['e_char_count']['denominator'] == 2
    assert result['e_first_char_count']['denominator'] == 1
    assert result['b_acceptance']['DeepSeek']['mean'] == .5
    conditional = summarize([r for r in records if r['negative']], replicates=20)
    assert conditional['b_acceptance']['denominator'] == 1
    assert conditional['e_first_char_count']['human'] is None
    assert conditional['e_first_char_count']['DeepSeek']['low'] is None
    with pytest.raises(AssertionError, match='human alignment'):
        common_records(spark,{**deepseek,(1,1):dict(human='错位',simulator='你好')},raw)


def test_bootstrap_pairs_dialogues_and_preserves_turn_weights():
    records = []
    for did, count, human, model in [(1,1,0,1),(2,3,1,0)]:
        for _ in range(count):
            records.append(dict(dialogue_id=did, values={
                'human':dict(x=human), 'Spark':dict(x=model), 'DeepSeek':dict(x=model)}))
    result = summarize(records,replicates=2000)['x']
    assert result['human'] == .75
    assert result['Spark'] == result['DeepSeek']
    assert result['Spark'] == dict(mean=.25,gap=-.5,low=-1,high=1)
    assert summarize(records,replicates=2000)['x'] == result


def test_legacy_checker_still_requires_sizes_disjointness_and_different_seeds(monkeypatch):
    monkeypatch.setattr(check_outputs,'load',lambda:[])
    calls = []
    def check(path,n,raw):
        calls.append((path,n))
        return ({n}, n)
    monkeypatch.setattr(check_outputs,'check',check)
    check_outputs.main(['outputs','outputs/extended'])
    assert calls == [('outputs',20),('outputs/extended',500)]
    monkeypatch.setattr(check_outputs,'check',lambda *args:({1},42))
    with pytest.raises(AssertionError,match='overlapping'):
        check_outputs.main(['outputs','outputs/extended'])
    monkeypatch.setattr(check_outputs,'check',lambda path,n,raw:({n},42))
    with pytest.raises(AssertionError,match='seeds must differ'):
        check_outputs.main(['outputs','outputs/extended'])
