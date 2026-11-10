import importlib.util
import json
from pathlib import Path

from scripts.build_sft_data import (build_v2, clean_target, evaluation_ids, PUNCT_SPACE)
from scripts.project_kaggle_cost import project
from usersim.csds import load, user_turns
from usersim.replay import prompt

spec = importlib.util.spec_from_file_location('formal_train', Path(__file__).resolve().parents[1]/'kaggle/train/train.py')
trainer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trainer)


def test_v2_deterministic_one_per_dialogue_replay_and_cleaning(tmp_path):
    manifest = build_v2(tmp_path)
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert manifest == build_v2(tmp_path)
    assert first == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    records = [json.loads(s) for s in (tmp_path/'train.jsonl').read_text().splitlines()]
    excluded = evaluation_ids()
    expected = {r['DialogueID']: r for r in load() if r['QRole'] == '用户' and r['split'] == 'train'
                and r['DialogueID'] not in excluded}
    assert len(records) == len(expected) == len({r['dialogue_id'] for r in records})
    assert {r['dialogue_id'] for r in records} == set(expected)
    assert not set(expected) & excluded
    for r in records:
        row = expected[r['dialogue_id']]
        turn = user_turns(row)[r['turn']-1]
        assert r['messages'] == prompt(row, turn)
        assert r['target'] == clean_target('\n'.join(turn['messages']))
        assert not PUNCT_SPACE.search(r['target'])
    assert manifest['target_punctuation_spaces']['after_ratio'] == 0


def test_target_horizontal_only():
    assert clean_target('你 好 ， 世 界 ! 好 ?\n好 \n， 好\r\n再 见 。 A B 12 34') == '你好，世界!好?\n好 \n，好\r\n再见。 A B 12 34'


def test_token_budget_and_order():
    items = [dict(input_ids=[1]*n) for n in [5, 3, 20, 4, 2]]
    order = trainer.length_order(items)
    batches = trainer.token_batches(order, items, 10)
    assert sorted(i for b in batches for i in b) == list(range(5))
    assert all(len(b) == 1 or len(b)*max(len(items[i]['input_ids']) for i in b) <= 10 for b in batches)
    assert trainer.length_order(items) == order


def test_collate_mask_without_torch_dependency():
    # Run real CE/gradient tests with torch in the contract's dedicated environment.
    import subprocess
    subprocess.run(['/tmp/usersim-d9-venv/bin/python', str(Path(__file__).with_name('verify_train_cpu.py'))], check=True)


def test_benchmark_projection():
    m = dict(mode='cpu_smoke', train_token_counts=dict(total=36000), benchmarks=[dict(
        configuration='sdpa', actual=dict(world_size=2), tokens_per_second=10,
        completed_steps=20, overflow_steps=[])])
    result = project(m, dict(token_estimate=dict(train_total=1)))
    assert '1.000 | 2.000' in result and 'NOT a T4' in result


def test_optional_install_failure_preserves_torch(monkeypatch):
    from types import SimpleNamespace
    import subprocess
    seen = []
    monkeypatch.setattr(trainer.metadata, 'version', lambda name: '2.11.0+cu130')
    def fail(command, **kwargs):
        seen.append(command)
        assert '--constraint' in command and '--no-build-isolation' in command
        assert 'torch==' in Path(command[command.index('--constraint')+1]).read_text()
        assert 'torch' not in command
        raise subprocess.CalledProcessError(1, command, output='T4 kernel compile unavailable')
    monkeypatch.setattr(trainer.subprocess, 'run', fail)
    monkeypatch.setattr(trainer.subprocess, 'check_output', lambda *a, **k: '2.11.0+cu130\n')
    result = trainer.install_fast(SimpleNamespace(__version__='2.11.0+cu130'), True)
    assert not result['installed'] and 'T4 kernel compile unavailable' in result['reason']
    assert len(seen) == 1
