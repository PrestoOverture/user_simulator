from usersim.behaviors import classify, contexts, measure, normalize


def test_escalation_and_opening():
    assert classify('转 人 工', first=True)['opening_transfer'] == 1
    assert classify('转人工', first=True)['escalation'] == 0
    assert classify('转人工')['escalation'] == 1
    assert classify('转人工，我要投诉', first=True)['escalation'] == 1
    for text in ('换个客服', '给我安排专员', '要求回电', '我要投诉12315'):
        assert classify(text)['escalation'] == 1
    assert classify('请查一下订单')['escalation'] == 0


def test_acceptance_needs_negative_agent():
    assert classify('那行吧', '只能等待')['acceptance'] == 1
    assert classify('嗯', '不支持退款')['acceptance'] == 1
    assert classify('好的吧', '已为您退款')['acceptance'] == 0
    assert classify('为什么不行？', '不支持退款')['acceptance'] == 0


def test_alternative():
    for text in ('那我买其他款式吧', '那我不要了', '算了不退了', '那我自己去'):
        assert classify(text)['alternative'] == 1
    assert classify('我要退货')['alternative'] == 0


def test_emotion_and_exclamation_separate():
    assert classify('太慢了，我急用')['emotion'] == 1
    assert classify('订单到了')['emotion'] == 0
    assert classify('你好！')['exclamation'] == 1
    assert classify('你好！')['emotion'] == 0
    assert classify('你好')['exclamation'] == 0


def test_narrative_only_novel_terms():
    assert classify('孩子明天要用')['narrative'] == 1
    assert classify('我儿子要用', goal='给儿子买的')['narrative'] == 0
    assert classify('孩子要用', history='客服：是给孩子的吗')['narrative'] == 0
    assert classify('我想退货')['narrative'] == 0


def test_future_never_enters_narrative_context():
    row = dict(DialogueID=-1, UserSumm=['咨询发货'], Dialogue=[
        dict(speaker='A', utterance='您好'), dict(speaker='Q', utterance='孩子要用'),
        dict(speaker='A', utterance='孩子几岁'), dict(speaker='Q', utterance='生日送人')])
    first = list(contexts(row))[0]
    assert '孩子' not in first['history']
    assert measure(first)['narrative'] == 1
    row['Dialogue'][2]['utterance'] = '孩子生日明天要用'
    assert list(contexts(row))[0] == first


def test_behavioral_and_negative_agent():
    assert classify('嗯', '无法办理')['behavioral'] == 1
    assert classify('那我买其他款式吧', '无法办理')['behavioral'] == 1
    assert classify('我想继续查询进度', '无法办理')['behavioral'] == 0
    assert classify('嗯', '已经办理')['behavioral'] == 0
    assert classify('你好', '请您耐心等待')['negative_agent'] == 1
    assert classify('你好', '已经退款成功')['negative_agent'] == 0
    assert normalize('转 人 工\n') == '转人工'


def test_reviewed_false_positives():
    assert classify('我可以给人工客服备注', first=True)['opening_transfer'] == 0
    assert classify('你是人工客服还是智能客服？')['escalation'] == 0
    assert classify('刚不小心给了一个差评，可以改吗')['escalation'] == 0
    assert classify('那能换个客服吗')['alternative'] == 0
    assert classify('我想换个收货地址')['alternative'] == 0
    assert classify('配送人员会联系我吗')['narrative'] == 0
    assert classify('不能再出差错')['narrative'] == 0
    assert classify('我不着急')['emotion'] == 0
    assert classify('好的', '正在为您查询，请耐心等待')['negative_agent'] == 0
    assert classify('嗯', '充电器不能充电了吗？')['negative_agent'] == 0
    assert classify('好吧', '不支持修改，我为您查询一下')['negative_agent'] == 1


def test_short_reply_boundary_and_long_alternative():
    assert classify('能退货吗啊', '不可以')['behavioral'] == 1
    assert classify('能退货吗啊啊', '不可以')['behavioral'] == 0
    assert classify('', '不可以')['behavioral'] == 0
    assert classify('那我买其他款式吧', '不可以')['behavioral'] == 1


def test_paired_bootstrap_turn_weighting():
    from usersim.behaviors import LABELS, summarize
    records = []
    full = []
    for did, count in ((1,1),(2,3)):
        for turn in range(1,count+1):
            full.append(dict(dialogue_id=did, values={k:int(did==1) for k in LABELS}))
            for source in ('human','baseline','C1','C2'):
                value = int(did==1) if source=='human' else int(did==2)
                records.append(dict(dialogue_id=did,turn=turn,source=source,
                                    values={k:value for k in LABELS}))
    s = summarize(records,full)
    assert s['common_turns'] == 4 and s['dialogues'] == 2
    for source in ('baseline','C1','C2'):
        r=s['comparisons'][source]['emotion']
        assert r['human'] == .25 and r['simulator'] == .75 and r['gap'] == .5
        assert r['low'] == -1 and r['high'] == 1
    assert s['comparisons']['baseline'] == s['comparisons']['C1']
    assert s['conditional']['human']['denominator'] == 1
    assert s['full_csds']['emotion']['dialogue_hits'] == 1
