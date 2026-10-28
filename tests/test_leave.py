import copy
import importlib.util
from pathlib import Path

import httpx
import pytest

from usersim import replay
from usersim.leave import (LEAVE_INSTRUCTION, prompt, classify, select, closing_only, interval)


def row(i=1):
    return dict(DialogueID=i,split='test',QRole='用户',UserSumm=['咨询配送'],Dialogue=[
        dict(speaker='Q',utterance='开始'),dict(speaker='A',utterance='客服前缀'),
        dict(speaker='Q',utterance='目标轮禁止泄漏'),dict(speaker='A',utterance='未来禁止泄漏')])


def test_prompt_same_structure_and_no_future():
    r=row()
    p=dict(start=2,kind='continue')
    messages=prompt(r,p)
    assert '客服前缀' in messages[-1]['content']
    assert '目标轮禁止泄漏' not in str(messages)
    assert '未来禁止泄漏' not in str(messages)
    left=copy.deepcopy(r); left['Dialogue']=left['Dialogue'][:2]
    assert messages==prompt(left,dict(start=2,kind='leave'))
    expected=replay.prompt(r,{'start':2})
    expected[-1]['content']+='\n'+LEAVE_INSTRUCTION
    assert messages==expected
    assert LEAVE_INSTRUCTION=='如果你此时不想再回复（问题已解决、不想继续、或者放弃），只输出：[离开]'
    assert messages[-1]['content'].count(LEAVE_INSTRUCTION)==1


@pytest.mark.parametrize('text,expected',[(' \n[离开]\t','leave'),('[离开]谢谢','mixed'),('我再问一下','stay'),('','stay'),('[离开] [离开]','mixed')])
def test_classification(text,expected):
    assert classify(text)==expected


def test_sampling_reproducible_and_prefixes():
    rows=[]
    for i in range(310):
        r=row(100000+i); r['Dialogue'] += [dict(speaker='Q',utterance='第二次'),dict(speaker='A',utterance='再见')]
        rows.append(r)
    a=select(rows,n=20)
    assert a==select(rows,n=20)
    assert a!=select(rows,n=20,seed=123)
    assert len(a['points'])==40
    assert {p['dialogue_id'] for p in a['points'] if p['kind']=='leave'} == {p['dialogue_id'] for p in a['points'] if p['kind']=='continue'}
    assert a['population']['continue_positions']==620
    for p in a['points']:
        r=rows[p['dialogue_id']-100000]
        assert p['messages']==prompt(r,p)
        assert r['Dialogue'][p['start']-1]['speaker']=='A'
        if p['kind']=='continue':
            assert r['Dialogue'][p['start']]['speaker']=='Q'
        else:
            assert p['start']==len(r['Dialogue'])


def test_stop_small_population():
    with pytest.raises(RuntimeError,match='<300'):
        select([row()])


def test_closing_only():
    assert closing_only('亲，感谢您的支持！')
    assert closing_only('还有其他问题吗？')
    assert not closing_only('退款要等三天，感谢您的支持')
    assert not closing_only('不能退款')


def test_bootstrap_and_empty():
    records=[dict(dialogue_id=1,outcome='leave'),dict(dialogue_id=1,outcome='stay')]
    assert interval(records,'leave')==dict(hits=1,n=2,rate=.5,low=.5,high=.5)
    assert interval([],'leave')['rate'] is None


def test_spark_429_and_moderation(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('run_leave',Path('scripts/run_leave.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setenv('SPARK_API_KEY','test-placeholder')
    codes=iter([429,429,429,429,200])
    payloads=[]
    def handler(request):
        import json
        payloads.append(json.loads(request.content))
        status=next(codes)
        return httpx.Response(status,json={'usage':dict(prompt_tokens=1,completion_tokens=1,total_tokens=2), 'choices':[{'message':{'content':'[离开]'}}]})
    client=module.Generator(tmp_path/'requests.jsonl','disabled',concurrency=4,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client,'_wait',lambda delay:None)
    point=dict(id='x',input_sha256='hash',messages=prompt(row(),dict(start=2)))
    try:
        result=client.generate(point,[])
        assert result['status']=='ok' and result['attempt']==5
        assert client.concurrency==1
        previous=module.recover_requests(tmp_path/'requests.jsonl')
        assert client.generate(point,previous)==result
        assert all(p['thinking']=={'type':'disabled'} and p['model']=='spark-x2.6' for p in payloads)
    finally:
        client.close()
    client=module.Generator(tmp_path/'moderation.jsonl','disabled',transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'error':{'code':10013}})))
    try:
        result=client.generate(point,[])
        assert result['status']=='moderated' and result['attempt']==1
    finally:
        client.close()
