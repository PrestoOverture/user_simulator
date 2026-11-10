"""Length-batched offline evaluation; zero-based --shard i/n, one GPU per process."""
import argparse
import hashlib
import fcntl
import json
import os
from importlib import metadata
import platform
from pathlib import Path
import random
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


def read_records(path, repair_tail=False):
    content = path.read_bytes() if path.exists() else b''
    if content and not content.endswith(b'\n'):
        if not repair_tail:
            raise ValueError(f'Incomplete final record in {path}')
        # An interrupted append is not a committed record. Preserve all complete lines.
        content = content[:content.rfind(b'\n')+1]
        path.write_bytes(content)
    rows = [json.loads(line) for line in content.splitlines()]
    ids = [r['request_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f'Duplicate request_id in {path}')
    return rows


def select(records, shard):
    i, n = map(int, shard.split('/'))
    if not 0 <= i < n:
        raise ValueError('Expected zero-based shard i/n')
    return [r for j, r in enumerate(records) if j % n == i]


def remaining(records, done, shard):
    return [r for r in select(records, shard) if r['request_id'] not in done]


def main():
    began = time.monotonic()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--install', action='store_true')
    p.add_argument('--variant', choices=['base', 'lora'], default='base')
    p.add_argument('--adapter', type=Path)
    p.add_argument('--data', type=Path, default=Path('/kaggle/input/csds-usersim-eval/eval_turns.jsonl'))
    p.add_argument('--output', type=Path, default=Path('/kaggle/working'))
    p.add_argument('--shard', default='0/1')
    p.add_argument('--device', type=int, default=0)
    p.add_argument('--batch-size', type=int, default=4)
    p.add_argument('--max-hours', type=float, default=11)
    args = p.parse_args()
    if args.batch_size < 1 or args.max_hours <= 0:
        p.error('batch-size and max-hours must be positive')
    import torch
    installation = prepare_dependencies(torch, args.install or (not args.smoke and Path('/kaggle').exists()))
    env = environment_info(torch, installation)
    print(json.dumps(env), flush=True)
    from transformers import AutoTokenizer, Qwen3_5ForCausalLM, GPT2Config, GPT2LMHeadModel
    from peft import PeftModel, LoraConfig, get_peft_model
    torch.manual_seed(SEED)
    random.seed(SEED)
    if args.smoke:
        torch.set_num_threads(1)
        tokenizer = SmokeTokenizer()
        model = GPT2LMHeadModel(GPT2Config(vocab_size=258, n_positions=512, n_embd=16,
                    n_layer=1, n_head=2, eos_token_id=1, pad_token_id=0))
        device = torch.device('cpu')
        if args.variant == 'lora':
            if args.adapter:
                model = PeftModel.from_pretrained(model, args.adapter)
            else:
                model = get_peft_model(model, LoraConfig(r=2, target_modules=['c_attn'], task_type='CAUSAL_LM'))
        records = read_records(args.data) if args.data.exists() else [dict(request_id=f'smoke:{i}', messages=[dict(role='user', content='售后'* (i+1))]) for i in range(4)]
    else:
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA required')
        torch.cuda.set_device(args.device)
        device = torch.device(f'cuda:{args.device}')
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        model = Qwen3_5ForCausalLM.from_pretrained(MODEL_ID, torch_dtype=torch.float16, attn_implementation='eager')
        if args.variant == 'lora':
            if not args.adapter:
                raise ValueError('--adapter required for lora')
            model = PeftModel.from_pretrained(model, args.adapter)
        if not args.data.exists():
            candidates = list(Path('/kaggle/input').rglob('eval_turns.jsonl'))
            if len(candidates) != 1:
                raise ValueError('Specify --data: evaluation dataset mount is ambiguous or missing')
            args.data = candidates[0]
        records = read_records(args.data)
    model.to(device).eval()
    tokenizer.padding_side = 'left'
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    args.output.mkdir(parents=True, exist_ok=True)
    dest = args.output/f'generations_{args.variant}.jsonl'
    writer_lock = Path(str(dest)+'.lock').open('a')
    fcntl.flock(writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    meta = args.output/f'run_{args.variant}_{args.shard.replace("/", "of")}.json'
    adapter_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.adapter.glob('adapter*')) if p.is_file()} if args.adapter else {}
    config = dict(adapter_sha256=adapter_hashes, input_sha256=fingerprint, variant=args.variant, adapter=str(args.adapter), smoke=args.smoke,
                  shard=args.shard, seed=SEED, temperature=.7, top_p=.8, top_k=20, max_new_tokens=256, enable_thinking=False)
    if meta.exists() and json.loads(meta.read_text())['config'] != config:
        raise ValueError('Resume configuration mismatch')
    meta.write_text(json.dumps(dict(config=config, environment=env), indent=2)+'\n')
    previous = read_records(dest, repair_tail=True)
    if any(r['variant'] != args.variant or r['input_sha256'] != fingerprint for r in previous):
        raise ValueError('Resume input/variant mismatch')
    done = {r['request_id'] for r in previous}
    pending = []
    for r in remaining(records, done, args.shard):
        if r['request_id'] not in done:
            ids = tokenizer.apply_chat_template(r['messages'], tokenize=True, add_generation_prompt=True,
                                               enable_thinking=False, return_dict=False)
            pending.append((r, ids))
    pending.sort(key=lambda x: len(x[1]))
    with dest.open('a') as f:
        for offset in range(0, len(pending), args.batch_size):
            if time.monotonic()-began >= args.max_hours*3600:
                break
            batch = pending[offset:offset+args.batch_size]
            width = max(len(ids) for _, ids in batch)
            context = getattr(model.config, 'max_position_embeddings', getattr(model.config, 'n_positions', None))
            if context and width+256 > context:
                raise ValueError('Prompt plus output exceeds context; no truncation')
            inputs = torch.tensor([[tokenizer.pad_token_id]*(width-len(ids))+ids for _, ids in batch], device=device)
            mask = torch.tensor([[0]*(width-len(ids))+[1]*len(ids) for _, ids in batch], device=device)
            with torch.inference_mode():
                result = model.generate(input_ids=inputs, attention_mask=mask, max_new_tokens=256,
                    do_sample=True, temperature=.7, top_p=.8, top_k=20,
                    pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id)
            for (r, ids), output in zip(batch, result):
                tokens = output[width:].tolist()
                if tokenizer.eos_token_id in tokens:
                    tokens = tokens[:tokens.index(tokenizer.eos_token_id)+1]
                row = dict(request_id=r['request_id'], variant=args.variant, text=tokenizer.decode(tokens, skip_special_tokens=True),
                           generated_tokens=len(tokens), prompt_tokens=len(ids), hit_token_limit=len(tokens)==256,
                           input_sha256=fingerprint)
                f.write(json.dumps(row, ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
    print(f'{dest}: {len(read_records(dest))} records', flush=True)


if __name__ == '__main__':
    main()
