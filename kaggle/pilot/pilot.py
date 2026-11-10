"""Single-device D9 pilot. --smoke is offline CPU with a tiny random GPT2 + LoRA."""
import argparse
from contextlib import nullcontext
import hashlib
import json
import math
from importlib import metadata
import platform
import tempfile
from pathlib import Path
import random
import subprocess
import sys
import time

MODEL_ID = 'Qwen/Qwen3.5-4B'
DEPENDENCIES = ['transformers==5.3.0', 'peft==0.18.1', 'accelerate==1.12.0']
SEED = 20261109


def prepare_dependencies(torch, install):
    from packaging.version import Version
    before = str(torch.__version__)
    print(f'torch before install: {before}', flush=True)
    if Version(before) < Version('2.4.0'):
        raise RuntimeError(f'Preinstalled torch {before} does not satisfy transformers==5.3.0 '
                           '(requires torch>=2.4); refusing to upgrade torch')
    if install:
        # Constraints prevent pip's transitive resolution from replacing the image stack.
        with tempfile.TemporaryDirectory() as directory:
            constraints = Path(directory)/'image-torch.txt'
            pins = []
            for name in ('torch', 'torchvision', 'torchaudio'):
                try:
                    pins.append(f'{name}=={metadata.version(name)}')
                except metadata.PackageNotFoundError:
                    pass
            constraints.write_text('\n'.join(pins)+'\n')
            subprocess.check_call([sys.executable, '-m', 'pip', 'install',
                                   '--constraint', str(constraints), *DEPENDENCIES])
        # A fresh interpreter sees disk changes; the current imported torch is cached.
        after = subprocess.check_output(
            [sys.executable, '-c', 'import torch; print(torch.__version__)'], text=True).strip()
    else:
        after = str(torch.__version__)
    print(f'torch after install: {after}', flush=True)
    if after != before:
        raise RuntimeError(f'Torch changed during installation: {before} -> {after}; stop')
    return dict(before=before, after=after)


def environment_info(torch, installation):
    count = torch.cuda.device_count()
    return dict(python=platform.python_version(), torch=str(torch.__version__),
                transformers=metadata.version('transformers'), peft=metadata.version('peft'),
                accelerate=metadata.version('accelerate'), cuda=torch.version.cuda,
                gpu_name=torch.cuda.get_device_name(0) if count else None,
                visible_gpu_count=count,
                gpu_total_memory_bytes=[torch.cuda.get_device_properties(i).total_memory
                                        for i in range(count)], torch_installation=installation)


def dump(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def prompt_tokens(tokenizer, record):
    return tokenizer.apply_chat_template(record['messages'], tokenize=True,
                                         add_generation_prompt=True, enable_thinking=False, return_dict=False)


def encode(tokenizer, record):
    """Append target tokens separately: no prompt or template token is supervised."""
    prefix = prompt_tokens(tokenizer, record)
    target = tokenizer.encode(record['target'], add_special_tokens=False)
    if not target:
        raise ValueError('Empty target')
    # EOS is a target termination token, never part of the replay prompt.
    target += [tokenizer.eos_token_id]
    return dict(input_ids=prefix+target, labels=[-100]*len(prefix)+target,
                prompt_length=len(prefix), target_tokens=len(target))


class SmokeTokenizer:
    eos_token_id = 1
    pad_token_id = 0

    def encode(self, text, add_special_tokens=False):
        return [x+2 for x in text.encode('utf-8')]

    def apply_chat_template(self, messages, tokenize, add_generation_prompt, enable_thinking, return_dict):
        assert return_dict is False
        assert enable_thinking is False
        return self.encode(''.join(m['role']+':'+m['content']+'\n' for m in messages)+'assistant:\n<think>\n\n</think>\n\n')

    def decode(self, ids, skip_special_tokens=True):
        return bytes(x-2 for x in ids if x >= 2).decode('utf-8', errors='replace')


def validate_metrics(m):
    assert m['schema_version'] == 1 and m['mode'] in ('cpu_smoke', 'cuda_fp16')
    env = m['environment']
    assert all(isinstance(env[k], str) for k in ('python', 'torch', 'transformers', 'peft', 'accelerate'))
    assert len(env['gpu_total_memory_bytes']) == env['visible_gpu_count']
    assert env['torch_installation']['before'] == env['torch_installation']['after']
    assert len(m['steps']) == m['completed_steps']
    for i, s in enumerate(m['steps'], 1):
        assert s['step'] == i and isinstance(s['nonfinite'], bool)
        assert s['loss'] is None or math.isfinite(s['loss'])
        assert s['seconds'] > 0 and s['tokens_per_second'] > 0
        assert s['peak_memory_bytes'] >= 0
    for g in m['generation'].values():
        assert g['samples'] > 0 and g['seconds'] > 0 and g['generated_tokens'] > 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--install', action='store_true')
    p.add_argument('--data', type=Path, default=Path('/kaggle/input/csds-usersim-sft'))
    p.add_argument('--output', type=Path, default=Path('/kaggle/working'))
    p.add_argument('--resume', type=Path)
    p.add_argument('--smoke-steps', type=int, default=2)
    args = p.parse_args()
    import torch
    installation = prepare_dependencies(
        torch, args.install or (not args.smoke and Path('/kaggle').exists()))
    environment = environment_info(torch, installation)
    print('environment: '+json.dumps(environment), flush=True)
    from peft import LoraConfig, get_peft_model
    from transformers import AutoTokenizer, Qwen3_5ForCausalLM, GPT2Config, GPT2LMHeadModel

    torch.manual_seed(SEED)
    random.seed(SEED)
    smoke = args.smoke
    if smoke:
        torch.set_num_threads(1)
        tokenizer = SmokeTokenizer()
        base = GPT2LMHeadModel(GPT2Config(vocab_size=258, n_positions=256, n_embd=16,
                                        n_layer=1, n_head=2, bos_token_id=1, eos_token_id=1,
                                        pad_token_id=0))
        train = [dict(request_id=f'smoke:{i}', messages=[dict(role='user', content='售后')], target='退货') for i in range(4)]
        evaluation = train[:2]
        targets = ['c_attn']
        device = torch.device('cpu')
    else:
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA required; use --smoke for offline CPU validation')
        torch.cuda.set_device(0)
        device = torch.device('cuda:0')
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        # Text-only class loads the language weights, omitting the vision encoder.
        base = Qwen3_5ForCausalLM.from_pretrained(MODEL_ID, torch_dtype=torch.float16,
                                                attn_implementation='eager').to(device)
        if not (args.data/'train.jsonl').exists():
            candidates = list(Path('/kaggle/input').rglob('train.jsonl'))
            if len(candidates) != 1:
                raise ValueError('Specify --data: dataset mount is ambiguous or missing')
            args.data = candidates[0].parent
        train = read_lines(args.data/'train.jsonl')
        evaluation = read_lines(args.data/'eval_sample.jsonl')
        targets = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'in_proj_qkv', 'in_proj_z',
                   'in_proj_b', 'in_proj_a', 'out_proj', 'gate_proj', 'up_proj', 'down_proj']
    model = get_peft_model(base, LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05,
                                          target_modules=targets, task_type='CAUSAL_LM'))
    model.to(device)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.enable_input_require_grads()
    model.config.use_cache = False
    # Trainable adapter parameters remain fp32 for GradScaler; frozen base is fp16.
    for param in model.parameters():
        if param.requires_grad:
            param.data = param.data.float()
    optimizer = torch.optim.AdamW([x for x in model.parameters() if x.requires_grad], lr=2e-4)
    scaler = torch.amp.GradScaler('cuda', enabled=not smoke)
    order = list(range(len(train)))
    random.Random(SEED).shuffle(order)
    fingerprint = hashlib.sha256(json.dumps(train, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    metrics = dict(schema_version=1, mode='cpu_smoke' if smoke else 'cuda_fp16', model_id=MODEL_ID,
                   dependencies=DEPENDENCIES, environment=environment, seed=SEED, completed_steps=0, steps=[], generation={},
                   train_samples=len(train), train_sha256=fingerprint,
                   settings=dict(batch_size=1, lora_rank=8, learning_rate=2e-4,
                                 gradient_checkpointing=True, enable_thinking=False,
                                 do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                                 max_new_tokens=4 if smoke else None), resume_from_step=None)
    if not smoke:
        total_tokens = target_tokens = 0
        for record in train:
            item = encode(tokenizer, record)
            total_tokens += len(item['input_ids'])
            target_tokens += item['target_tokens']
        metrics['train_token_counts'] = dict(total=total_tokens, target=target_tokens)
    start = 0
    if args.resume:
        state = torch.load(args.resume/'state.pt', map_location=device, weights_only=False)
        metrics = state['metrics']
        if metrics['train_sha256'] != fingerprint or metrics['mode'] != ('cpu_smoke' if smoke else 'cuda_fp16'):
            raise ValueError('Resume data/mode mismatch')
        from peft import set_peft_model_state_dict
        set_peft_model_state_dict(model, state['adapter'])
        optimizer.load_state_dict(state['optimizer'])
        scaler.load_state_dict(state['scaler'])
        torch.set_rng_state(state['rng'].cpu())
        if not smoke:
            torch.cuda.set_rng_state(state['cuda_rng'].cpu(), device)
        start = metrics['completed_steps']
        metrics['resume_from_step'] = start
    metrics['environment'] = environment
    metrics['dependencies'] = DEPENDENCIES
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    limit = args.smoke_steps if smoke else 200
    if limit < start:
        raise ValueError('Checkpoint exceeds requested steps')

    def sync():
        if not smoke:
            torch.cuda.synchronize(device)

    def checkpoint(step):
        from peft import get_peft_model_state_dict
        dest = out/f'checkpoint-{step}'
        dest.mkdir(exist_ok=True)
        model.save_pretrained(dest)
        state = dict(adapter=get_peft_model_state_dict(model), optimizer=optimizer.state_dict(),
                     scaler=scaler.state_dict(), rng=torch.get_rng_state(),
                     cuda_rng=None if smoke else torch.cuda.get_rng_state(device), metrics=metrics)
        torch.save(state, dest/'state.tmp')
        (dest/'state.tmp').replace(dest/'state.pt')

    model.train()
    for step in range(start, limit):
        encoded = encode(tokenizer, train[order[step % len(order)]])
        batch = {k: torch.tensor([encoded[k]], device=device) for k in ('input_ids', 'labels')}
        if not smoke:
            torch.cuda.reset_peak_memory_stats(device)
        sync()
        began = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        with (nullcontext() if smoke else torch.autocast('cuda', dtype=torch.float16)):
            # Keep only logits that predict target tokens (including EOS).
            # The final prompt position predicts the first target token.
            n = encoded['target_tokens']
            logits = model(input_ids=batch['input_ids'], logits_to_keep=n+1).logits[:, :-1]
            loss = torch.nn.functional.cross_entropy(
                logits.float().reshape(-1, logits.shape[-1]), batch['labels'][:, -n:].reshape(-1))
        finite_loss = bool(torch.isfinite(loss))
        bad_grad = False
        if finite_loss:
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            bad_grad = any(not bool(torch.isfinite(x.grad).all()) for x in model.parameters() if x.grad is not None)
            if not bad_grad:
                torch.nn.utils.clip_grad_norm_([x for x in model.parameters() if x.requires_grad], 1.0)
            scaler.step(optimizer)
            scaler.update()
        sync()
        elapsed = time.perf_counter()-began
        metrics['steps'].append(dict(step=step+1, loss=float(loss.detach()) if finite_loss else None,
                                    nonfinite=not finite_loss or bad_grad, nonfinite_loss=not finite_loss,
                                    nonfinite_grad=bad_grad, seconds=elapsed,
                                    input_tokens=len(encoded['input_ids']), target_tokens=encoded['target_tokens'],
                                    tokens_per_second=len(encoded['input_ids'])/elapsed,
                                    peak_memory_bytes=0 if smoke else torch.cuda.max_memory_allocated(device)))
        metrics['completed_steps'] = step+1
        dump(out/'pilot_metrics.json', metrics)
        if (step+1) % (1 if smoke else 50) == 0:
            checkpoint(step+1)
        print(json.dumps(metrics['steps'][-1]), flush=True)
        if not finite_loss:
            raise RuntimeError('Nonfinite loss recorded; stop pilot')

    model.eval()
    model.config.use_cache = True
    records = []
    metrics['generation'] = {}
    for variant in ('base', 'lora'):
        began = time.perf_counter()
        token_count = prompt_count = 0
        with (model.disable_adapter() if variant == 'base' else nullcontext()):
            for record in evaluation:
                ids = prompt_tokens(tokenizer, record)
                inputs = torch.tensor([ids], device=device)
                sync()
                t0 = time.perf_counter()
                with torch.inference_mode():
                    max_new = 4 if smoke else model.config.max_position_embeddings-len(ids)
                    if max_new <= 0:
                        raise ValueError('Prompt exceeds model context; do not truncate replay history')
                    result = model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                                            max_new_tokens=max_new,
                                            do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                                            eos_token_id=tokenizer.eos_token_id,
                                            pad_token_id=tokenizer.eos_token_id)
                sync()
                seconds = time.perf_counter()-t0
                generated = result[0, len(ids):].tolist()
                token_count += len(generated)
                prompt_count += len(ids)
                records.append(dict(request_id=record['request_id'], variant=variant,
                                    text=tokenizer.decode(generated, skip_special_tokens=True),
                                    generated_tokens=len(generated), prompt_tokens=len(ids), seconds=seconds,
                                    tokens_per_second=len(generated)/seconds,
                                    hit_token_limit=len(generated) == max_new))
        seconds = time.perf_counter()-began
        metrics['generation'][variant] = dict(samples=len(evaluation), generated_tokens=token_count,
                                             prompt_tokens=prompt_count, seconds=seconds,
                                             tokens_per_second=token_count/seconds,
                                             samples_per_second=len(evaluation)/seconds)
    validate_metrics(metrics)
    (out/'generations.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records))
    dump(out/'pilot_metrics.json', metrics)


if __name__ == '__main__':
    main()
