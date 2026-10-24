import json
import asyncio
import httpx
import pytest
from usersim.csds import clean_summary, user_turns
from usersim.replay import prompt
from usersim.spark import Spark
from usersim.metrics import measure


def dialogue():
    return dict(UserSumm=['用户询问配送时间。'],Dialogue=[
        dict(speaker=s,utterance=f'唯一消息{i}专属',turn=i)
        for i,s in enumerate(['A','Q','Q','A','A','Q','A','Q','Q'])])


def test_no_target_or_future_leakage():
    row=dialogue()
    for turn in user_turns(row):
        rendered=json.dumps(prompt(row,turn),ensure_ascii=False)
        for i,msg in enumerate(row['Dialogue']):
            if i < turn['start']:
                assert msg['utterance'] in rendered
            else:
                assert msg['utterance'] not in rendered


def test_merge_consecutive_messages():
    turns=user_turns(dialogue())
    assert [t['start'] for t in turns]==[1,5,7]
    assert '\n'.join(turns[0]['messages'])=='唯一消息1专属\n唯一消息2专属'
    assert len(turns[-1]['messages'])==2


def test_remove_summary_outcome():
    clean,removed=clean_summary(['用户要求退款，最终退款成功。用户同意赔偿京豆。'])
    assert clean=='用户要求退款'
    assert clean_summary(['用户查询检测结果。用户表示接受检测结果。'])[0]=='用户查询检测结果'
    assert removed==['最终退款成功','用户同意赔偿京豆']
    assert clean_summary(['用户询问退款是否成功。'])[0]=='用户询问退款是否成功'
    assert clean_summary(['用户反馈退货不成功。'])[0]=='用户反馈退货不成功'


def test_metric_spaces_and_first_turn():
    result=measure('谢 谢！\n好 的',[],True,2)
    assert result['char_count']==4
    assert result['politeness']==1
    assert result['message_count']==2
    assert result['short']==1
    assert measure('好的',['好的'],False,1)['repeated']==1


def test_spark_payload_usage_and_retry(tmp_path,monkeypatch):
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    calls=[]
    async def handler(request):
        calls.append(json.loads(request.content))
        if len(calls)==1:
            return httpx.Response(429,json={'error':'rate limit'})
        return httpx.Response(200,json={'usage':{'prompt_tokens':10,'completion_tokens':3},'choices':[{'message':{'content':'好'}}]})
    async def no_sleep(seconds):
        pass
    monkeypatch.setattr('usersim.spark.asyncio.sleep',no_sleep)
    async def run():
        client=Spark(tmp_path)
        await client.close()
        client.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert await client.generate([{'role':'user','content':'你好'}],'test')=='好'
        await client.close()
    asyncio.run(run())
    assert len(calls)==2
    assert all(p['thinking']=={'type':'disabled'} and p['model']=='spark-x2.6' and p['stream'] is False and 'temperature' not in p and 'max_tokens' not in p for p in calls)
    records=[json.loads(s) for s in (tmp_path/'request_usage.jsonl').read_text().splitlines()]
    assert records[0]['http_status']==429
    assert records[1]['input_tokens']==10
    assert 'test-secret' not in (tmp_path/'request_usage.jsonl').read_text()


def test_spark_exhaustion_stops(tmp_path,monkeypatch):
    monkeypatch.setenv('SPARK_API_KEY','test-secret')
    async def handler(request):
        return httpx.Response(500)
    async def no_sleep(seconds):
        pass
    monkeypatch.setattr('usersim.spark.asyncio.sleep',no_sleep)
    async def run():
        client=Spark(tmp_path)
        await client.close()
        client.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(RuntimeError,match='after 4 attempts'):
            await client.generate([],'test')
        with pytest.raises(RuntimeError,match='Stopped'):
            await client.generate([],'next')
        await client.close()
    asyncio.run(run())
    assert len((tmp_path/'request_usage.jsonl').read_text().splitlines())==4


def test_moderation_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv('SPARK_API_KEY', 'test-secret')
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(400, json={'error': {'code': 10013, 'message': '内容审核拒绝'}})
    async def run():
        client = Spark(tmp_path)
        await client.close()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert await client.generate([], 'moderated-test') is None
        assert not client.failed
        await client.close()
    asyncio.run(run())
    assert len(calls) == 1
    record = json.loads((tmp_path/'request_usage.jsonl').read_text())
    assert record['status'] == 'moderated'
    assert record['error_code'] == 10013
    assert record['error_message'] == '内容审核拒绝'


def test_prompt_unsegments_only_chinese_neighbors():
    row = dict(UserSumm=['用 户 询 问 Spark API 12 34 的 配 送。'], Dialogue=[
        dict(speaker='A', utterance='请 使 用 English words 12 34\n保 留 换 行'),
        dict(speaker='Q', utterance='未 来 内 容')])
    content = prompt(row, user_turns(row)[0])[1]['content']
    assert '用户询问 Spark API 12 34 的配送' in content
    assert '请使用 English words 12 34\n保留换行' in content
    assert '未来内容' not in content


def test_review_findings_summary_cleaning():
    from usersim.csds import load
    rows = {r['DialogueID']: r for r in load()}
    assert clean_summary(rows[2403]['UserSumm'], 2403)[0] == '用户询问自己两个订单的发票是否并为开出'
    goal = clean_summary(rows[10197]['UserSumm'], 10197)[0]
    assert '不再退货' not in goal and '评价' not in goal
    assert '更换' in goal
    assert '最后请客服帮助取消订单' in clean_summary(rows[10049]['UserSumm'], 10049)[0]


def test_first_identifier_definition():
    assert measure('[订单编号]', [], True, 1, 'human')['first_identifier'] == 1
    assert measure('[数字]', [], True, 1, 'human')['first_identifier'] == 1
    assert measure('订单123456', [], True, 1, 'simulator')['first_identifier'] == 1
    assert measure('订单12345', [], True, 1, 'simulator')['first_identifier'] == 0
    assert measure('123 456', [], True, 1, 'simulator')['first_identifier'] == 0
    assert measure('123456', [], True, 1, 'human')['first_identifier'] == 0


def test_output_verifier_preserves_csv_line_endings(tmp_path):
    from scripts.run_pilot import write_csv
    from scripts.check_outputs import read_csv
    rows = [dict(simulator='第一条\r第二条\r\nEnglish words\n123 456')]
    target = tmp_path/'side_by_side.csv'
    write_csv(target, rows)
    assert read_csv(target) == rows


def test_independent_leakage_audit_rejects_contamination():
    import hashlib
    from scripts.audit_leakage import audit_turn
    row = dict(dialogue(), DialogueID=-1)
    messages = prompt(row, {'start': 5})
    digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()
    audit_turn(row, 5, messages, digest)
    for contamination in ('客服：唯一消息6专属', '顾客：模拟器独有输出'):
        polluted = [dict(m) for m in messages]
        polluted[1]['content'] += '\n' + contamination
        polluted_hash = hashlib.sha256(json.dumps(polluted, ensure_ascii=False).encode()).hexdigest()
        with pytest.raises(ValueError, match='contamination'):
            audit_turn(row, 5, polluted, polluted_hash)
    with pytest.raises(ValueError, match='fingerprint'):
        audit_turn(row, 5, messages, 'altered')


def test_review_keyword_false_positives_and_clear_signals():
    def metrics(text):
        return measure(text, [], False, 1)
    assert metrics('我可以申请退款吗')['politeness'] == 0
    assert metrics('你好，可以申请退款吗')['politeness'] == 1
    assert metrics('我们公司的客户经理是谁')['escalation'] == 0
    assert metrics('我给领导汇报一下')['escalation'] == 0
    assert metrics('刚给了差评可以修改吗')['escalation'] == 0
    assert metrics('把你们主管叫来，我要投诉')['escalation'] == 1
    assert metrics('保修多久，什么时候发货，到底什么问题')['urgency'] == 0
    assert metrics('请加急，帮我催一下，尽快处理')['urgency'] == 1
    assert metrics('软件还是不行，我要退款')['refusal'] == 0
    assert metrics('我不接受这个方案')['refusal'] == 1
    assert metrics('是不是发错了，东西不对，我是说这个型号')['correction'] == 0
    assert metrics('我刚才说错了，是另一个订单')['correction'] == 1


def test_spark_credentials_only_from_environment(monkeypatch):
    from usersim.spark import api_key
    monkeypatch.delenv('SPARK_API_KEY', raising=False)
    with pytest.raises(RuntimeError, match='SPARK_API_KEY missing'):
        api_key()


def test_review_summary_overrides_preserve_questions_without_execution():
    from usersim.csds import load
    rows = {r['DialogueID']: r for r in load()}
    expected = {1538: ('应付多少钱', '再下单'), 9150: ('是否需要评价', '已申请退货'),
                3278: ('剩余运费', '商家补偿一部分运费')}
    for identifier, (request, outcome) in expected.items():
        goal, _ = clean_summary(rows[identifier]['UserSumm'], identifier)
        assert request in goal
        assert outcome not in goal
    assert '退款已收到' in clean_summary(rows[3278]['UserSumm'], 3278)[0]


def test_leakage_cli_nonzero_for_contaminated_fingerprints(tmp_path):
    import hashlib
    import subprocess
    import sys
    from pathlib import Path
    from usersim.csds import load
    root = Path(__file__).resolve().parents[1]
    identifier = 5134
    raw = next(r for r in load() if r['DialogueID'] == identifier)
    generations = [json.loads(line) for line in (root/'outputs/generations.jsonl').read_text().splitlines()]
    selected = [g for g in generations if g['dialogue_id'] == identifier]
    (tmp_path/'sampled_ids.json').write_text(json.dumps(dict(ids=[identifier])))
    for contamination in ('客服：未来客服内容', '顾客：模拟器历史污染'):
        copied = [dict(g) for g in selected]
        messages = prompt(raw, user_turns(raw)[0])
        messages[1]['content'] += '\n' + contamination
        copied[0]['prompt_sha256'] = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()
        (tmp_path/'generations.jsonl').write_text('\n'.join(json.dumps(g) for g in copied))
        result = subprocess.run([sys.executable, str(root/'scripts/audit_leakage.py'), str(tmp_path)],
                                cwd=root, capture_output=True, text=True)
        assert result.returncode != 0
        assert 'FAIL:' in result.stderr and 'fingerprint mismatch' in result.stderr
