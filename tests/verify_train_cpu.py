"""Standalone CPU verifier, invoked from pytest using the isolated ML environment."""
import importlib.util
from pathlib import Path
import torch
from transformers import GPT2Config, GPT2LMHeadModel

spec = importlib.util.spec_from_file_location('train', Path(__file__).resolve().parents[1]/'kaggle/train/train.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
torch.set_num_threads(1)
torch.manual_seed(123)
model = GPT2LMHeadModel(GPT2Config(vocab_size=258, n_positions=512, n_embd=8, n_layer=1, n_head=2,
                                  resid_pdrop=0, embd_pdrop=0, attn_pdrop=0))
items = [t.encode(t.SmokeTokenizer(), dict(messages=[dict(role='user', content='历史'*n)], target='目标'*n))
         for n in [1, 3]]
batch = t.collate(items, 0)
for row, item in enumerate(items):
    n, prefix = len(item['input_ids']), item['prompt_length']
    assert (batch['labels'][row, :prefix] == -100).all()
    assert (batch['labels'][row, n:] == -100).all()
    assert (batch['attention_mask'][row, n:] == 0).all()
    assert batch['labels'][row, prefix:n].tolist() == item['labels'][prefix:]
actual = t.target_loss(model, batch, smoke=True)
logits = model(input_ids=batch['input_ids'], attention_mask=batch['attention_mask']).logits
# Independent reference explicitly selects each target's predecessor, never padding/prompt.
reference = sum(torch.nn.functional.cross_entropy(logits[i, x['prompt_length']-1:len(x['input_ids'])-1],
               torch.tensor(x['labels'][x['prompt_length']:]), reduction='sum') for i, x in enumerate(items))
assert torch.allclose(actual, reference)
actual_grad = torch.autograd.grad(actual, model.transformer.wte.weight, retain_graph=True)[0]
reference_grad = torch.autograd.grad(reference, model.transformer.wte.weight)[0]
assert torch.allclose(actual_grad, reference_grad, atol=1e-5)
print('prompt/padding target-only CE and gradient PASS')


# Inject an acceleration failure through the real smoke loop; also exercise the
# --ddp unavailable branch without requiring a local GPU.
import json
import sys
import tempfile
from unittest.mock import patch
original_loss = t.target_loss
calls = 0

def fail_once(*args, **kwargs):
    global calls
    calls += 1
    if calls == 1:
        raise RuntimeError('injected acceleration unavailable')
    return original_loss(*args, **kwargs)

with tempfile.TemporaryDirectory() as directory:
    t.target_loss = fail_once
    try:
        with patch.object(sys, 'argv', ['train.py', '--smoke', '--output', directory]):
            t.main()
    finally:
        t.target_loss = original_loss
    result = json.loads((Path(directory)/'train_metrics.json').read_text())
    assert result['actual']['attention'] == 'eager' and result['actual']['token_budget'] == 0
    assert any('injected acceleration' in reason for reason in result['fallbacks'])
    assert result['completed_steps'] == 2
    with patch.object(sys, 'argv', ['train.py', '--smoke', '--ddp', '--max-hours', '0', '--output', directory]):
        t.main()
    result = json.loads((Path(directory)/'train_metrics.json').read_text())
    assert any('DDP unavailable' in reason for reason in result['fallbacks'])
    assert result['actual']['attention'] == 'eager' and result['actual']['token_budget'] == 0
print('injected acceleration failure and DDP-unavailable fallback PASS')


# Optional end-to-end artifact audit after the handoff's four smoke commands.
if len(__import__('sys').argv) > 1:
    import argparse
    import json
    from peft import PeftModel
    from safetensors.torch import load_file
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', type=Path, required=True)
    artifact_prefix = parser.parse_args().artifacts
    def base():
        return GPT2LMHeadModel(GPT2Config(vocab_size=258, n_positions=512, n_embd=16, n_layer=1,
                                        n_head=2, bos_token_id=1, eos_token_id=1, pad_token_id=0))
    for name in ('final', 'final-resume', 'final-benchmark', 'final-wall'):
        folder = Path(str(artifact_prefix)+'-'+name)
        loaded = PeftModel.from_pretrained(base(), folder/'adapter')
        assert loaded.peft_config['default'].r == 16
        m = json.loads((folder/'train_metrics.json').read_text())
        if name == 'final-wall':
            assert m['stop_reason'] == 'max_hours' and m['completed_steps'] == 0
            assert (folder/'checkpoint-0/state.pt').is_file()
        elif name == 'final-benchmark':
            assert len(m['benchmarks']) == 3
            assert all(b['completed_steps'] == 20 and b['tokens_per_second'] > 0 for b in m['benchmarks'])
        else:
            assert m['completed_steps'] == 3
            assert all(s['samples'] == 16 and not s['nonfinite'] for s in m['steps'])
    a, b = [Path(str(artifact_prefix)+'-'+s) for s in ('final', 'final-resume')]
    x, y = [load_file(p/'adapter/adapter_model.safetensors') for p in (a,b)]
    assert x.keys() == y.keys() and all((x[k] == y[k]).all() for k in x)
    x, y = [json.loads((p/'train_metrics.json').read_text()) for p in (a,b)]
    assert [s['loss'] for s in x['steps']] == [s['loss'] for s in y['steps']]
    print('4 PEFT adapters load; 3x20 benchmark; wall checkpoint-0; exact continuous/resumed losses and weights PASS')
