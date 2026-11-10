"""ADR-021 fp16 LoRA training. CPU smoke never downloads weights."""
import argparse
from contextlib import nullcontext
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import signal
import subprocess
import sys
import tempfile
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


def collate(items, pad_id, device='cpu'):
    import torch
    width = max(len(x['input_ids']) for x in items)
    return {key: torch.tensor([x[key]+[fill]*(width-len(x[key])) for x in items], device=device)
            for key, fill in [('input_ids', pad_id), ('labels', -100)]} | {
        'attention_mask': torch.tensor([[1]*len(x['input_ids'])+[0]*(width-len(x['input_ids']))
                                       for x in items], device=device)}


def length_order(items, seed=SEED):
    order = list(range(len(items)))
    rng = random.Random(seed)
    rng.shuffle(order)
    # Local sorting keeps batches similar in length without a curriculum.
    buckets = [sorted(order[i:i+256], key=lambda j: len(items[j]['input_ids']))
               for i in range(0, len(order), 256)]
    rng.shuffle(buckets)
    return [j for bucket in buckets for j in bucket]


def token_batches(indices, items, budget):
    batches, batch, longest = [], [], 0
    for i in indices:
        n = len(items[i]['input_ids'])
        if batch and (budget == 0 or max(longest, n)*(len(batch)+1) > budget):
            batches.append(batch)
            batch, longest = [], 0
        batch.append(i)
        longest = max(longest, n)
    if batch:
        batches.append(batch)
    return batches


def target_loss(model, batch, smoke=False):
    """Sum next-token CE at supervised positions only, ignoring prompts AND padding."""
    import torch
    labels = batch['labels'][:, 1:]
    positions = torch.where((labels != -100).any(dim=0))[0]
    # A dummy DDP microbatch still participates in the synchronized backward.
    if not len(positions):
        positions = torch.tensor([0], device=labels.device)
    inputs = {k: batch[k] for k in ('input_ids', 'attention_mask')}
    if smoke:
        logits = model(**inputs).logits[:, positions]
    else:
        logits = model(**inputs, logits_to_keep=positions).logits
    return torch.nn.functional.cross_entropy(logits.float().reshape(-1, logits.shape[-1]),
                 labels[:, positions].reshape(-1), ignore_index=-100, reduction='sum')


def disable_fast(model):
    from transformers.models.qwen3_5 import modeling_qwen3_5 as q
    for module in model.modules():
        if isinstance(module, q.Qwen3_5GatedDeltaNet):
            module.causal_conv1d_fn = None
            module.causal_conv1d_update = q.torch_causal_conv1d_update
            module.chunk_gated_delta_rule = q.torch_chunk_gated_delta_rule
            module.recurrent_gated_delta_rule = q.torch_recurrent_gated_delta_rule
            if not isinstance(module.norm, q.Qwen3_5RMSNormGated):
                old = module.norm.weight
                norm = q.Qwen3_5RMSNormGated(module.head_v_dim, eps=module.layer_norm_epsilon).to(old.device, old.dtype)
                norm.weight.data.copy_(old.data)
                norm.weight.requires_grad_(old.requires_grad)
                module.norm = norm


def install_fast(torch, enabled):
    result = dict(requested=enabled, installed=False, reason=None)
    if not enabled:
        return result
    before = str(torch.__version__)
    try:
        # Resolve kernel dependencies (including fla-core), but constrain the
        # image stack and use its existing torch for extension builds.
        with tempfile.TemporaryDirectory() as directory:
            constraints = Path(directory)/'image-torch.txt'
            pins = []
            for package in ('torch', 'torchvision', 'torchaudio'):
                try:
                    pins.append(f'{package}=={metadata.version(package)}')
                except metadata.PackageNotFoundError:
                    pass
            constraints.write_text('\n'.join(pins)+'\n')
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--constraint', str(constraints),
                            '--no-build-isolation', 'flash-linear-attention', 'causal-conv1d'],
                           check=True, timeout=300, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        result['installed'] = True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, 'stdout', None) or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode(errors='replace')
        result['reason'] = f'{type(exc).__name__}: {detail[-1000:]}'
    after = subprocess.check_output([sys.executable, '-c', 'import torch; print(torch.__version__)'], text=True).strip()
    if before != after:
        raise RuntimeError(f'Torch changed: {before} -> {after}')
    result['versions'] = {}
    for package in ('flash-linear-attention', 'fla-core', 'causal-conv1d'):
        try:
            result['versions'][package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            result['versions'][package] = None
    return result


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--smoke-steps', type=int, default=2)
    p.add_argument('--install', action='store_true')
    p.add_argument('--data', type=Path, default=Path('/kaggle/input/csds-usersim-sft-v2'))
    p.add_argument('--output', type=Path, default=Path('/kaggle/working'))
    p.add_argument('--resume', type=Path)
    p.add_argument('--benchmark', action='store_true')
    p.add_argument('--ddp', action='store_true')
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--fallback-reason', default=None, help=argparse.SUPPRESS)
    p.add_argument('--attention', choices=['sdpa', 'eager'], default='sdpa')
    p.add_argument('--no-fast-kernels', action='store_true')
    p.add_argument('--token-budget', type=int, default=4096,
                   help='Maximum padded tokens per microbatch; 0 means batch size 1; long singletons are not truncated')
    p.add_argument('--max-hours', type=float, default=11)
    p.add_argument('--checkpoint-every', type=int, default=25)
    return p.parse_args()


def run(args, started):
    import torch
    rank = int(os.environ.get('RANK', 0)) if args.worker else 0
    world = int(os.environ.get('WORLD_SIZE', 1)) if args.worker else 1
    local_rank = int(os.environ.get('LOCAL_RANK', 0)) if args.worker else 0
    if world > 1:
        from datetime import timedelta
        torch.cuda.set_device(local_rank)
        torch.distributed.init_process_group('nccl', timeout=timedelta(minutes=10))
    installation = prepare_dependencies(torch, rank == 0 and
        (args.install or (not args.smoke and Path('/kaggle').exists())))
    fast = install_fast(torch, not args.smoke and not args.no_fast_kernels) if rank == 0 else None
    if world > 1:
        values = [fast]
        torch.distributed.broadcast_object_list(values, src=0)
        fast = values[0]
    import transformers.utils.import_utils as imports
    if not args.smoke:
        try:
            if args.no_fast_kernels:
                raise ImportError('fast kernels disabled by argument')
            from causal_conv1d import causal_conv1d_fn
            from fla.modules import FusedRMSNormGated
            from fla.ops.gated_delta_rule import chunk_gated_delta_rule
        except Exception as exc:
            fast['import_fallback'] = f'{type(exc).__name__}: {exc}'
            imports.is_flash_linear_attention_available = lambda: False
            imports.is_causal_conv1d_available = lambda: False
    environment = environment_info(torch, installation)
    environment['optional_kernels'] = fast
    print('environment: '+json.dumps(environment), flush=True)
    from peft import LoraConfig, get_peft_model, get_peft_model_state_dict, set_peft_model_state_dict
    from transformers import AutoTokenizer, Qwen3_5ForCausalLM, GPT2Config, GPT2LMHeadModel
    if not args.smoke:
        from transformers.models.qwen3_5 import modeling_qwen3_5 as q
        fast_names = ('causal_conv1d_fn', 'causal_conv1d_update', 'chunk_gated_delta_rule',
                      'fused_recurrent_gated_delta_rule', 'FusedRMSNormGated')
        fast_functions = {k: getattr(q, k) for k in fast_names}
    torch.manual_seed(SEED)
    random.seed(SEED)
    device = torch.device('cpu' if args.smoke else f'cuda:{local_rank}')
    if args.smoke:
        torch.set_num_threads(1)
        tokenizer = SmokeTokenizer()
        records = [dict(messages=[dict(role='user', content='售后'+('问题'*i))],
                        target='退货'+('吗'*i)) for i in range(32)]
        targets = ['c_attn']
    else:
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA required; CPU validation requires --smoke')
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        if not (args.data/'train.jsonl').exists():
            candidates = list(Path('/kaggle/input').rglob('train.jsonl'))
            candidates = [p for p in candidates if 'v2' in str(p)]
            if len(candidates) != 1:
                raise ValueError('Specify --data: v2 mount ambiguous or missing')
            args.data = candidates[0].parent
        records = read_lines(args.data/'train.jsonl')
        targets = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'in_proj_qkv', 'in_proj_z',
                   'in_proj_b', 'in_proj_a', 'out_proj', 'gate_proj', 'up_proj', 'down_proj']
    encoded = [encode(tokenizer, r) for r in records]
    fingerprint = hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    counts = dict(total=sum(len(x['input_ids']) for x in encoded),
                  target=sum(x['target_tokens'] for x in encoded))
    order = length_order(encoded)
    windows = [order[i:i+16] for i in range(0, len(order), 16)]
    args.output.mkdir(parents=True, exist_ok=True)
    metrics = dict(schema_version=2, mode='cpu_smoke' if args.smoke else 'cuda_fp16', model_id=MODEL_ID,
                   environment=environment, seed=SEED, train_samples=len(records), train_sha256=fingerprint,
                   train_token_counts=counts, steps=[], benchmarks=[], overflow_steps=[], completed_steps=0,
                   fallbacks=([args.fallback_reason] if args.fallback_reason else []),
                   settings=dict(lora_rank=16, lora_alpha=32, lora_dropout=0.05, learning_rate=1e-4,
                                 warmup_ratio=0.03, scheduler='cosine', effective_batch=16, epochs=1,
                                 dtype='fp16' if not args.smoke else 'fp32', world_size=world))
    configs = [('eager_single', 'eager', False, 0), ('sdpa_grouped', 'sdpa', False, args.token_budget),
               ('sdpa_fast_grouped', 'sdpa', True, args.token_budget)] if args.benchmark else [
               ('train', args.attention, not args.no_fast_kernels, args.token_budget)]
    for name, attention, use_fast, budget in configs:
        torch.manual_seed(SEED)
        if args.fallback_reason and args.fallback_reason.startswith('DDP failed'):
            attention, use_fast, budget = 'eager', False, 0
        actual = dict(attention=attention, fast_kernels=False, token_budget=budget, world_size=world)
        if args.smoke:
            base = GPT2LMHeadModel(GPT2Config(vocab_size=258, n_positions=512, n_embd=16,
                            n_layer=1, n_head=2, bos_token_id=1, eos_token_id=1, pad_token_id=0,
                            attn_implementation=attention))
        else:
            for k, value in fast_functions.items():
                setattr(q, k, value if use_fast else None)
            q.is_fast_path_available = use_fast and all(fast_functions[k] is not None for k in fast_names)
            try:
                base = Qwen3_5ForCausalLM.from_pretrained(MODEL_ID, torch_dtype=torch.float16,
                                                         attn_implementation=attention).to(device)
            except Exception as exc:
                if attention == 'eager' and not use_fast:
                    raise
                metrics['fallbacks'].append(f'{name}: accelerated load failed: {type(exc).__name__}: {exc}')
                actual['attention'] = 'eager'
                for k in fast_names:
                    setattr(q, k, None)
                q.is_fast_path_available = False
                torch.cuda.empty_cache()
                base = Qwen3_5ForCausalLM.from_pretrained(MODEL_ID, torch_dtype=torch.float16,
                                                         attn_implementation='eager').to(device)
            from transformers.models.qwen3_5 import modeling_qwen3_5 as q
            actual['attention'] = base.config._attn_implementation
            actual['fast_kernels'] = bool(use_fast and q.is_fast_path_available)
            if not actual['fast_kernels']:
                disable_fast(base)
                if use_fast:
                    metrics['fallbacks'].append(f'{name}: fast kernel unavailable; using torch fallback')
        model = get_peft_model(base, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                                                target_modules=targets, task_type='CAUSAL_LM')).to(device)
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
        model.enable_input_require_grads()
        model.config.use_cache = False
        for param in model.parameters():
            if param.requires_grad:
                param.data = param.data.float()
        optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=1e-4)
        limit = 20 if args.benchmark else args.smoke_steps if args.smoke else len(windows)
        warmup = max(1, math.ceil(limit*0.03))
        def lr_factor(step):
            if step < warmup:
                return (step+1)/warmup
            return 0.5*(1+math.cos(math.pi*(step-warmup)/max(1, limit-warmup)))
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_factor)
        scaler = torch.amp.GradScaler('cuda', enabled=not args.smoke)
        start = 0
        if args.resume and not args.benchmark:
            state = torch.load(args.resume/'state.pt', map_location=device, weights_only=False)
            old = state['metrics']
            if old['train_sha256'] != fingerprint or old['mode'] != metrics['mode']:
                raise ValueError('Resume data/mode mismatch')
            if state['limit'] != limit:
                # Smoke permits increasing its small fixture horizon.
                if not args.smoke:
                    raise ValueError('Resume schedule mismatch')
            set_peft_model_state_dict(model, state['adapter'])
            optimizer.load_state_dict(state['optimizer'])
            scheduler.load_state_dict(state['scheduler'])
            scaler.load_state_dict(state['scaler'])
            metrics['steps'] = old['steps']
            metrics['overflow_steps'] = old['overflow_steps']
            metrics['fallbacks'] = old['fallbacks'] + metrics['fallbacks']
            start = old['completed_steps']
            metrics['completed_steps'] = start
            metrics['resume_from_step'] = start
            rng = state['rng_by_rank'][rank % len(state['rng_by_rank'])]
            torch.set_rng_state(rng['cpu'].cpu())
            if not args.smoke:
                torch.cuda.set_rng_state(rng['cuda'].cpu(), device)
        wrapped = torch.nn.parallel.DistributedDataParallel(model, device_ids=[local_rank]) if world > 1 else model
        model.train()
        run_steps = []
        stop_reason = 'epoch_complete'

        def sync():
            if not args.smoke:
                torch.cuda.synchronize(device)

        def save(step):
            rng = dict(cpu=torch.get_rng_state(), cuda=None if args.smoke else torch.cuda.get_rng_state(device))
            rngs = [None]*world
            if world > 1:
                torch.distributed.all_gather_object(rngs, rng)
            else:
                rngs = [rng]
            if rank == 0:
                model.save_pretrained(args.output/'adapter')
                if not args.smoke:
                    tokenizer.save_pretrained(args.output/'adapter')
                if args.benchmark:
                    dump(args.output/'train_metrics.json', metrics)
                    return
                dest = args.output/f'checkpoint-{step}'
                dest.mkdir(exist_ok=True)
                torch.save(dict(adapter=get_peft_model_state_dict(model), optimizer=optimizer.state_dict(),
                    scheduler=scheduler.state_dict(), scaler=scaler.state_dict(), rng_by_rank=rngs,
                    metrics=metrics, limit=limit), dest/'state.tmp')
                (dest/'state.tmp').replace(dest/'state.pt')
                dump(args.output/'train_metrics.json', metrics)

        for step in range(start, limit):
            expired = torch.tensor(int(time.monotonic()-started >= args.max_hours*3600), device=device)
            if world > 1:
                torch.distributed.all_reduce(expired, op=torch.distributed.ReduceOp.MAX)
            if expired.item():
                stop_reason = 'max_hours'
                break
            window = windows[step % len(windows)]
            shards = [token_batches(window[r::world], encoded, actual['token_budget']) for r in range(world)]
            micro_count = max(map(len, shards))
            denominator = sum(encoded[i]['target_tokens'] for i in window)
            # Runtime acceleration failure retries this window with unchanged weights/RNG.
            while True:
                optimizer.zero_grad(set_to_none=True)
                rng_cpu = torch.get_rng_state()
                rng_cuda = None if args.smoke else torch.cuda.get_rng_state(device)
                if not args.smoke:
                    torch.cuda.reset_peak_memory_stats(device)
                sync()
                began = time.perf_counter()
                loss_sum = torch.zeros((), device=device)
                try:
                    for micro in range(micro_count):
                        indices = shards[rank][micro] if micro < len(shards[rank]) else []
                        batch = collate([encoded[i] for i in indices] or [encoded[window[0]]],
                                        tokenizer.pad_token_id or tokenizer.eos_token_id, device)
                        if not indices:
                            batch['labels'].fill_(-100)
                        with (wrapped.no_sync() if world > 1 and micro < micro_count-1 else nullcontext()):
                            with (nullcontext() if args.smoke else torch.autocast('cuda', dtype=torch.float16)):
                                loss = target_loss(wrapped, batch, args.smoke)
                            loss_sum += loss.detach()
                            scaler.scale(loss*world/denominator).backward()
                    break
                except Exception as exc:
                    if world > 1 or (actual['attention'] == 'eager' and not actual['fast_kernels'] and actual['token_budget'] == 0):
                        raise
                    metrics['fallbacks'].append(f'{name}: runtime {type(exc).__name__}: {exc}; single eager batch1 fallback')
                    actual.update(attention='eager', fast_kernels=False, token_budget=0)
                    model.set_attn_implementation('eager')
                    if not args.smoke:
                        disable_fast(model)
                        torch.cuda.empty_cache()
                        torch.cuda.set_rng_state(rng_cuda, device)
                    torch.set_rng_state(rng_cpu)
                    shards = [token_batches(window, encoded, 0)]
                    micro_count = len(shards[0])
            scaler.unscale_(optimizer)
            bad = any(not bool(torch.isfinite(p.grad).all()) for p in model.parameters() if p.grad is not None)
            if world > 1:
                torch.distributed.all_reduce(loss_sum)
            finite_loss = bool(torch.isfinite(loss_sum))
            if not bad and finite_loss:
                torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            if not args.smoke or not bad:
                scaler.step(optimizer)
            scaler.update()
            if not bad and finite_loss:
                scheduler.step()
            sync()
            seconds = time.perf_counter()-began
            elapsed = torch.tensor(seconds, device=device)
            peak = torch.tensor(0 if args.smoke else torch.cuda.max_memory_allocated(device), device=device)
            if world > 1:
                torch.distributed.all_reduce(elapsed, op=torch.distributed.ReduceOp.MAX)
                torch.distributed.all_reduce(peak, op=torch.distributed.ReduceOp.MAX)
            tokens = sum(len(encoded[i]['input_ids']) for i in window)
            result = dict(step=step+1, loss=float(loss_sum)/denominator if finite_loss else None,
                          nonfinite=bad or not finite_loss, seconds=elapsed.item(), input_tokens=tokens,
                          target_tokens=denominator, samples=len(window), microbatches=micro_count,
                          tokens_per_second=tokens/elapsed.item(), peak_memory_bytes=peak.item(),
                          learning_rate=optimizer.param_groups[0]['lr'], actual=dict(actual))
            run_steps.append(result)
            if not args.benchmark:
                metrics['steps'].append(result)
                metrics['completed_steps'] = step+1
                if result['nonfinite']:
                    metrics['overflow_steps'].append(step+1)
                if (step+1) % args.checkpoint_every == 0:
                    save(step+1)
                if rank == 0:
                    dump(args.output/'train_metrics.json', metrics)
            if rank == 0:
                print(json.dumps(dict(configuration=name, **result)), flush=True)
            if not finite_loss:
                stop_reason = 'nonfinite_loss'
                break
        if args.benchmark:
            seconds = sum(s['seconds'] for s in run_steps)
            metrics['benchmarks'].append(dict(configuration=name, requested=dict(attention=attention,
                fast_kernels=use_fast, token_budget=budget, world_size=world), actual=dict(actual),
                steps=run_steps, completed_steps=len(run_steps), requested_steps=20,
                tokens_per_second=sum(s['input_tokens'] for s in run_steps)/seconds if seconds else None,
                peak_memory_bytes=max((s['peak_memory_bytes'] for s in run_steps), default=0),
                overflowed=any(s['nonfinite'] for s in run_steps),
                overflow_steps=[s['step'] for s in run_steps if s['nonfinite']],
                stop_reason=stop_reason))
        metrics['stop_reason'] = stop_reason
        metrics['actual'] = dict(actual)
        save(metrics['completed_steps'])
        if run_steps:
            del loss, batch, loss_sum
        del wrapped, model, base, optimizer, scheduler, scaler
        import gc
        gc.collect()
        if not args.smoke:
            torch.cuda.empty_cache()
        if stop_reason != 'epoch_complete':
            break
    if world > 1:
        torch.distributed.destroy_process_group()


def main():
    args = parse_args()
    if int(os.environ.get('WORLD_SIZE', 1)) > 1:
        args.worker = True
    started = time.monotonic()
    if args.max_hours < 0 or args.token_budget < 0 or args.checkpoint_every < 1:
        raise ValueError('Invalid wall time, token budget or checkpoint interval')
    # Supervise optional DDP so a failed worker cannot leave the job hung or
    # silently skip data. Retry from the latest complete checkpoint, single eager.
    if args.ddp and not args.worker:
        import torch
        if not args.smoke and torch.cuda.device_count() >= 2:
            forwarded = [x for x in sys.argv[1:] if x != '--ddp']
            command = [sys.executable, '-m', 'torch.distributed.run', '--standalone', '--nproc_per_node=2',
                       str(Path(__file__).resolve()), '--worker', *forwarded]
            try:
                process = subprocess.Popen(command, start_new_session=True)
                try:
                    code = process.wait(timeout=max(1, args.max_hours*3600)+600)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise
                if code:
                    raise subprocess.CalledProcessError(code, command)
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                args.fallback_reason = f'DDP failed: {type(exc).__name__}: {exc}; single eager fallback'
                checkpoints = sorted(args.output.glob('checkpoint-*/state.pt'), key=lambda p: int(p.parent.name.split('-')[-1]))
                if checkpoints:
                    args.resume = checkpoints[-1].parent
                args.attention, args.no_fast_kernels = 'eager', True
                args.token_budget = 0
        else:
            args.fallback_reason = 'DDP unavailable: requires two CUDA GPUs; single eager batch1 fallback'
            args.attention, args.no_fast_kernels, args.token_budget = 'eager', True, 0
        print(args.fallback_reason, flush=True)
    run(args, started)


if __name__ == '__main__':
    main()
