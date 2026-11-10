import copy
import json
import importlib.util
from pathlib import Path

from scripts.build_sft_data import build, evaluation_ids, examples
from usersim.csds import load, user_turns
from usersim.replay import prompt
spec = importlib.util.spec_from_file_location('usersim_pilot', Path(__file__).resolve().parents[1]/'kaggle/pilot/pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
SmokeTokenizer, encode, prompt_tokens = pilot.SmokeTokenizer, pilot.encode, pilot.prompt_tokens


def test_eval_exclusion_and_determinism(tmp_path):
    manifest = build(tmp_path)
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    build(tmp_path)
    assert first == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    train = [json.loads(x) for x in (tmp_path/'train.jsonl').read_text().splitlines()]
    assert not {r['dialogue_id'] for r in train} & evaluation_ids()
    assert manifest['excluded_dialogue_count'] == 500
    evaluation = [json.loads(x) for x in (tmp_path/'eval_sample.jsonl').read_text().splitlines()]
    assert len(evaluation) == 50
    assert {r['dialogue_id'] for r in evaluation} <= evaluation_ids()
    train_ids = {x['dialogue_id'] for x in train}
    assert all(r['split'] == 'train' for r in load() if r['DialogueID'] in train_ids)


def test_prompt_strict_prefix_and_target():
    row = dict(DialogueID='sentinel', UserSumm=['要求退货'], Dialogue=[
        dict(speaker='Q', utterance='之前 消息'), dict(speaker='A', utterance='之前 回复'),
        dict(speaker='Q', utterance='目标 独特'), dict(speaker='Q', utterance='连发 消息'),
        dict(speaker='A', utterance='未来 独特')])
    turn = user_turns(row)[1]
    sample = list(examples([row]))[1]
    assert sample['messages'] == prompt(row, turn)
    assert sample['target'] == '目标独特\n连发消息'
    text = json.dumps(sample['messages'], ensure_ascii=False)
    assert '之前消息' in text and '之前回复' in text
    assert '目标独特' not in text and '未来独特' not in text and '连发消息' not in text
    changed = copy.deepcopy(row)
    for msg in changed['Dialogue'][turn['start']:]:
        msg['utterance'] = '泄漏哨兵'
    assert prompt(changed, turn) == sample['messages']


def test_loss_mask_targets_only():
    tokenizer = SmokeTokenizer()
    record = dict(messages=[dict(role='system', content='提示哨兵'),
                            dict(role='user', content='历史')], target='用户原文\n第二条')
    encoded = encode(tokenizer, record)
    n = len(prompt_tokens(tokenizer, record))
    assert n > 0
    assert encoded['labels'][:n] == [-100]*n
    expected = tokenizer.encode(record['target'])+[tokenizer.eos_token_id]
    assert encoded['labels'][n:] == expected
    assert encoded['input_ids'][n:] == expected
    assert sum(x != -100 for x in encoded['labels']) == len(expected)


def test_install_preserves_image_torch(monkeypatch):
    from types import SimpleNamespace
    seen = []
    monkeypatch.setattr(pilot.metadata, 'version', lambda name: {'torch': '2.8.0+cu128',
                        'torchvision': '0.23.0+cu128', 'torchaudio': '2.8.0+cu128'}[name])

    def install(command):
        seen.append(command)
        pins = Path(command[command.index('--constraint')+1]).read_text()
        assert 'torch==2.8.0+cu128' in pins
        assert 'torchvision==0.23.0+cu128' in pins
        assert 'torchaudio==2.8.0+cu128' in pins
        assert command[-3:] == pilot.DEPENDENCIES
        assert not any(x.startswith(('torch==', 'torchvision==', 'torchaudio==')) for x in command)

    monkeypatch.setattr(pilot.subprocess, 'check_call', install)
    monkeypatch.setattr(pilot.subprocess, 'check_output', lambda *a, **k: '2.8.0+cu128\n')
    assert pilot.prepare_dependencies(SimpleNamespace(__version__='2.8.0+cu128'), True) == {
        'before': '2.8.0+cu128', 'after': '2.8.0+cu128'}
    assert len(seen) == 1
    import pytest
    monkeypatch.setattr(pilot.subprocess, 'check_output', lambda *a, **k: '2.10.0\n')
    with pytest.raises(RuntimeError, match='Torch changed'):
        pilot.prepare_dependencies(SimpleNamespace(__version__='2.8.0+cu128'), True)
    seen.clear()
    with pytest.raises(RuntimeError, match='2.3.1.*requires torch>=2.4'):
        pilot.prepare_dependencies(SimpleNamespace(__version__='2.3.1'), True)
    assert seen == []


def test_environment_reports_all_visible_gpus(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(pilot.metadata, 'version', lambda name: 'test-'+name)
    cuda = SimpleNamespace(device_count=lambda: 2, get_device_name=lambda i: 'Tesla T4',
                           get_device_properties=lambda i: SimpleNamespace(total_memory=15_000_000_000+i))
    env = pilot.environment_info(SimpleNamespace(__version__='2.8.0', cuda=cuda,
                                               version=SimpleNamespace(cuda='12.8')), {})
    assert env['gpu_name'] == 'Tesla T4' and env['visible_gpu_count'] == 2
    assert env['gpu_total_memory_bytes'] == [15_000_000_000, 15_000_000_001]
    assert env['cuda'] == '12.8' and env['torch'] == '2.8.0'
    assert all(env[k] for k in ('python', 'transformers', 'peft', 'accelerate'))
