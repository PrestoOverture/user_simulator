import asyncio
import json
import httpx
import pytest
from usersim.spark import Spark
from usersim.csds import user_turns
from usersim.replay import prompt
from scripts.run_ablation_a import ablation_prompt, ADDITION, targets, values


@pytest.mark.parametrize('provider,model', [('spark', None), ('sandbox', 'deepseek-v4.1-flash'), ('sandbox', 'other-model')])
def test_provider_payload(tmp_path, monkeypatch, provider, model):
    monkeypatch.setenv('SPARK_API_KEY','spark-test')
    monkeypatch.setenv('SANDBOX_API_KEY','sandbox-test')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://sandbox.example///')
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=8,completion_tokens=5,completion_tokens_details=dict(reasoning_tokens=2)),choices=[dict(message=dict(content='好的'))]))
    async def run():
        client=Spark(tmp_path,provider=provider,model=model,transport=httpx.MockTransport(handler))
        assert await client.generate([], 'one')=='好的'
        await client.close()
    asyncio.run(run())
    request=calls[0]; payload=json.loads(request.content)
    if provider=='sandbox':
        assert str(request.url)=='https://sandbox.example/v1/chat/completions'
        assert request.headers['Authorization']=='Bearer sandbox-test'
        assert payload['model']==model
        if model.startswith('deepseek'):
            assert payload['thinking']==dict(type='disabled')
        else:
            assert 'thinking' not in payload
    else:
        assert payload['thinking']==dict(type='disabled')
        assert payload['model']=='spark-x2.6'
        assert request.headers['Authorization']=='Bearer spark-test'
    record=json.loads((tmp_path/'request_usage.jsonl').read_text())
    if provider=='sandbox':
        assert record['thinking']==(dict(type='disabled') if model.startswith('deepseek') else 'omitted')
    assert record['reasoning_tokens']==2
    assert record['usage']['completion_tokens_details']['reasoning_tokens']==2


def test_sandbox_10013_is_error(tmp_path,monkeypatch):
    monkeypatch.setenv('SANDBOX_API_KEY','sandbox-test')
    monkeypatch.setenv('SANDBOX_BASE_URL','https://sandbox.example')
    async def no_sleep(_): pass
    monkeypatch.setattr('usersim.spark.asyncio.sleep', no_sleep)
    async def run():
        client=Spark(tmp_path,provider='sandbox',model='deepseek-v4.1-flash',transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'error':{'code':10013}})))
        with pytest.raises(RuntimeError,match='after 4 attempts'):
            await client.generate([], 'one')
        await client.close()
    asyncio.run(run())
    records=list(map(json.loads,(tmp_path/'request_usage.jsonl').read_text().splitlines()))
    assert len(records)==4
    assert all(r['status']=='error' for r in records)


def test_ablation_only_specified_changes_and_prefix():
    row=dict(DialogueID=-1,QRole='用户',UserSumm=['用户询问退款。最终退款成功。'],Dialogue=[dict(speaker=s,utterance=f'独有消息{i}') for i,s in enumerate(['Q','A','Q','A','Q'])])
    for turn in user_turns(row):
        base=prompt(row,turn)
        assert ablation_prompt(row,turn,'s1')==base
        s2=ablation_prompt(row,turn,'s2'); s3=ablation_prompt(row,turn,'s3')
        assert s2[0]==s3[0]==base[0]
        assert s2[1]['content']==base[1]['content']+'\n'+ADDITION
        assert s3[1]['content']==base[1]['content'].split('\n',1)[1]
        for setting in ('s1','s2','s3'):
            rendered=json.dumps(ablation_prompt(row,turn,setting),ensure_ascii=False)
            for i,message in enumerate(row['Dialogue']):
                assert (message['utterance'] in rendered)==(i<turn['start'])
            assert '退款成功' not in rendered
    row['Dialogue'][1]['utterance']='这个无法修改'
    row['Dialogue'][3]['utterance']='可以修改'
    selected=targets([row],3)
    assert [c['turn'] for _,ts in selected for _,c in ts]==[2]
    assert values(selected[0][1][0][1],'好吧')['short']==1


def test_ablation_resume_report_mock_transport(tmp_path,monkeypatch):
    from argparse import Namespace
    from scripts import run_ablation_a as runner
    monkeypatch.setenv('SPARK_API_KEY','spark-test')
    row=dict(DialogueID=-1,QRole='用户',UserSumm=['用户询问退款。'],Dialogue=[dict(speaker='A',utterance='这个无法修改'),dict(speaker='Q',utterance='好吧')])
    monkeypatch.setattr(runner,'load',lambda:[row])
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=dict(usage=dict(prompt_tokens=8,completion_tokens=2),choices=[dict(message=dict(content='好吧'))]))
    monkeypatch.setattr(runner,'Spark',lambda out,concurrency:Spark(out,concurrency,transport=httpx.MockTransport(handler)))
    args=Namespace(settings=['s1','s2','s3'],limit_dialogues=3,out=str(tmp_path),concurrency=2,report_only=False)
    asyncio.run(runner.main(args))
    report=(tmp_path/'report.md').read_text()
    assert len(calls)==3
    assert '2000' in report and '20261025' in report
    assert '负面后短句' in report
    args.report_only=True
    asyncio.run(runner.main(args))
    assert len(calls)==3
    assert (tmp_path/'report.md').read_text()==report
