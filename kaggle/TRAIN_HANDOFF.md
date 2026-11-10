### Changed Files
- `scripts/build_sft_data.py` — adds `--version 2` and fixed-seed one-turn-per-dialogue sampling; cleans target punctuation spacing only, using replay's exact prompt. Default v1 behavior remains unchanged.
- `kaggle/sft_data_v2/{train.jsonl,eval_sample.jsonl,manifest.json,dataset-metadata.json}` — deterministic v2 dataset and private Kaggle dataset metadata for `prestooverture/csds-usersim-sft-v2`.
- `kaggle/train/train.py` — standalone fp16 LoRA kernel, token-budget microbatches, effective batch 16, target-only CE, warmup/cosine, checkpoints/resume, wall-clock exit, three 20-step benchmark configurations, optional supervised torchrun DDP and recorded acceleration fallbacks.
- `kaggle/train/kernel-metadata.json` — private training kernel attached to the v2 dataset.
- `scripts/project_kaggle_cost.py` — retains pilot projection and adds per-configuration epoch wall hours and conservative GPU hours from benchmark metrics.
- `tests/test_sft_train.py` — data determinism/exclusion/exact replay/cleaning, token budgets, cost projection and optional installer failure tests.
- `tests/verify_train_cpu.py` — real CE/gradient comparison excluding prompts/padding, injected runtime failure and unavailable-DDP fallback; optional smoke artifact audit and PEFT reload.
- `kaggle/TRAIN_HANDOFF.md` — this delivery record. No project dependencies were added.

Training data: **7,983 samples**, seed **20261109**, exactly one merged user turn per eligible CSDS train dialogue, excluding the frozen 500 evaluation dialogues. As in v1, eligibility is `QRole == 用户`; train dialogues where Q speaks as 商家/物流师傅/etc. are not ordinary-customer targets. Input histories are not additionally cleaned or truncated. Targets preserve message newlines. The v2 50-turn evaluation sample also receives target-only punctuation cleanup; v1 is untouched.

Using the locally cached official `Qwen/Qwen3.5-4B` tokenizer: **1,790,425 total tokens**, including chat template and target EOS; **80,700 supervised target tokens**. Character estimate in the manifest is explicitly approximate. Training recomputes exact counts in `train_metrics.json`. Among sampled training targets, punctuation-adjacent horizontal whitespace occurs **3,294/7,983 = 41.2626832% before cleanup; 0/7,983 = 0% after cleanup** (after existing unsegment, before additional cleanup).

### Validation
Run from repository root. No project typecheck/build configuration exists; compileall is the syntax/build check.

```sh
.venv/bin/python scripts/build_sft_data.py --version 2
shasum -a 256 kaggle/sft_data_v2/* > /tmp/usersim-t-v2.sha256
.venv/bin/python scripts/build_sft_data.py --version 2
shasum -a 256 -c /tmp/usersim-t-v2.sha256
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q scripts/build_sft_data.py scripts/project_kaggle_cost.py kaggle/train/train.py tests/test_sft_train.py tests/verify_train_cpu.py
git diff --check
```

Final full pytest result: **249 passed in 165.31s (0:02:45)**. Compileall and `git diff --check` pass. Repeated builder SHA256 comparison passes for all four v2 files. Original v1 hashes, captured before implementation and verified unchanged:

| v1 file | SHA256 |
|---|---|
| train.jsonl | `354cfdc88c31f5c6130b5b52136dd89d0ea6ab0527ce69f7469fbe7c055ff5fd` |
| eval_sample.jsonl | `98ab51c1745f4088bfbca3c6fda69c3b6354fa87714e964ef9c8812ce5895c00` |
| manifest.json | `49a7fe242be62b2afa486a96af6c95c0b8857af87b499616c1f1f2f97b439fd2` |

| v2 file | SHA256 |
|---|---|
| train.jsonl | `f05282439a5e52b99365ba828ed0d6623544b43a207f704a9bd450230720d1ed` |
| eval_sample.jsonl | `a2ada2ffa946b788b40dadb0456ad7a014b1d7be114417a608cabf8c34e048c5` |
| manifest.json | `dbfb2ef42b26378d0d8ec00e0a5556ef52573275c93612ea738acb79ec41a265` |
| dataset-metadata.json | `d478aa6d1f57579ae96893cc61407a3f70bc5e5fa14b3cd23c0075a45e52f8b6` |

Exact CPU commands (all offline, using the contract's existing isolated environment):

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/train/train.py --smoke --smoke-steps 3 --checkpoint-every 1 --output /tmp/usersim-t-final
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/train/train.py --smoke --smoke-steps 3 --resume /tmp/usersim-t-final/checkpoint-1 --output /tmp/usersim-t-final-resume
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/train/train.py --smoke --benchmark --output /tmp/usersim-t-final-benchmark
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/train/train.py --smoke --max-hours 0.000000001 --output /tmp/usersim-t-final-wall
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python tests/verify_train_cpu.py --artifacts /tmp/usersim-t
.venv/bin/python scripts/project_kaggle_cost.py --metrics /tmp/usersim-t-final-benchmark/train_metrics.json --manifest kaggle/sft_data_v2/manifest.json
```

Results: batched training uses 16 examples per optimizer step, with multiple microbatches when the padded-token budget requires them. Same-horizon checkpoint-1 resume exactly matches all three continuous losses and every adapter tensor. All three benchmark configurations complete **20 steps each**, reporting positive token/s, zero CPU peak CUDA memory and no CPU overflow. Tiny wall limit exits successfully before training with `stop_reason=max_hours`, `checkpoint-0/state.pt` and a standard `adapter/`. **All four adapters load via `PeftModel.from_pretrained`**. The audit additionally confirms independent target-only CE/gradients, injected acceleration failure -> eager batch1, and unavailable DDP -> single eager. CPU uses tiny random GPT2/fp32 solely for plumbing; production remains Qwen/fp16. PEFT's expected GPT2 Conv1D and missing-base-path warnings do not prevent loading.

Exact offline token-count reproduction:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python - <<'PY'
import importlib.util, json
from pathlib import Path
from transformers import AutoTokenizer
spec = importlib.util.spec_from_file_location('train', 'kaggle/train/train.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
tokenizer = AutoTokenizer.from_pretrained(t.MODEL_ID, local_files_only=True)
items = [t.encode(tokenizer, json.loads(s)) for s in Path('kaggle/sft_data_v2/train.jsonl').read_text().splitlines()]
print(len(items), sum(len(x['input_ids']) for x in items), sum(x['target_tokens'] for x in items))
PY
```

Output: `7983 1790425 80700`.

Kaggle commands below are **for Claude; none were executed**. Local `.venv/bin/kaggle kernels push --help` confirms `--accelerator NvidiaTeslaT4` selects T4 × 2; this flag pins the requested hardware even when the training process uses one card. Use an already-authenticated Kaggle CLI; do not print credentials. Dataset creation defaults to private (no `--public`):

```sh
.venv/bin/kaggle datasets create -p kaggle/sft_data_v2
```

Kaggle script kernels have no metadata argument field, so prepare a standalone benchmark entry script in a temporary directory, keeping the formal kernel unchanged:

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
out = Path('/tmp/usersim-kaggle-benchmark')
out.mkdir(exist_ok=True)
src = Path('kaggle/train/train.py').read_text()
src = src.replace('    args = parse_args()', "    sys.argv[1:1] = ['--benchmark', '--max-hours', '11']\n    args = parse_args()")
(out/'train.py').write_text(src)
m = json.loads(Path('kaggle/train/kernel-metadata.json').read_text())
m['id'] = 'prestooverture/csds-qwen35-lora-benchmark'
m['title'] = 'CSDS Qwen3.5 fp16 LoRA benchmark'
(out/'kernel-metadata.json').write_text(json.dumps(m, indent=2)+'\n')
PY
.venv/bin/kaggle kernels push -p /tmp/usersim-kaggle-benchmark --accelerator NvidiaTeslaT4
.venv/bin/kaggle kernels status prestooverture/csds-qwen35-lora-benchmark
.venv/bin/kaggle kernels output prestooverture/csds-qwen35-lora-benchmark -p /tmp/usersim-t-kaggle-benchmark
.venv/bin/python scripts/project_kaggle_cost.py --metrics /tmp/usersim-t-kaggle-benchmark/train_metrics.json --manifest kaggle/sft_data_v2/manifest.json
```

The benchmark compares eager/batch1, sdpa/grouped, and sdpa/fast/grouped; effective optimizer batch remains 16 in all three. Requested and actual configuration plus fallbacks are recorded separately. To benchmark two T4s, insert `--ddp` in that temporary entry script's argument list. `--ddp` launches `python -m torch.distributed.run --standalone --nproc_per_node=2 ... --worker`, and its supervisor falls back to single eager if workers fail. Direct external torchrun is recognized too, but use the supervised `--ddp` entry for automatic worker-failure fallback.

After Claude reviews T4 timings, overflow calibration, actual kernel status and the remaining weekly allowance, the default formal push is:

```sh
.venv/bin/kaggle kernels push -p kaggle/train --accelerator NvidiaTeslaT4
.venv/bin/kaggle kernels status prestooverture/csds-qwen35-lora-train
.venv/bin/kaggle kernels output prestooverture/csds-qwen35-lora-train -p /tmp/usersim-t-kaggle-train
```

Default formal settings are sdpa preference, attempted fast kernels, token budget 4096, single GPU and 11-hour wall guard. If Claude selects different settings, use the same temporary-entry procedure with `['--attention', 'eager', '--no-fast-kernels', '--token-budget', '0']` for the verified fallback, or add `--ddp` for two GPUs. Do not select a configuration solely from CPU timings. Production resume, once the checkpoint is attached to the next Kaggle run, is `python train.py --resume /path/to/checkpoint-N`; the one-epoch horizon stays unchanged. The same temporary-entry procedure can inject this path when pushing a script kernel. Dataset and checkpoint attachments are separate Kaggle operations, not performed automatically by the kernel.

### Success Conditions
- [x] v2 generation via `.venv/bin/python scripts/build_sft_data.py --version 2`; repeated v2 SHA256 identical; v1 files unchanged against pre-task hashes.
- [x] Final full `.venv/bin/python -m pytest -q`: **249 passed in 165.31s**. New tests cover exactly one sample per eligible dialogue, no overlap with the 500 evaluation dialogues, horizontal punctuation cleanup with newline preservation, every sampled prompt equal to replay, and actual batched CE/gradients excluding prompt and padding.
- [x] CPU batched training, exact resume, three 20-step benchmarks, tiny-wall normal checkpoint exit and four PEFT adapter reloads pass; exact commands above.
- [x] Handoff records v2 sample/token counts, cleanup ratios, v2 dataset creation, benchmark/formal push and projection commands.

### Risks & Deviations
- **No Kaggle push or GPU run was performed, as required.** Actual T4 sdpa/fast-kernel/DDP execution, fp16 calibration/overflow behavior, memory fit, throughput and one-week completion remain for Claude's benchmark and training. Local success conditions validate delivery readiness, not completion of GPU training.
- Application dependency installation preserves the image torch/torchvision/torchaudio versions via constraints and checks torch in a fresh interpreter. Optional kernels resolve dependencies under the same constraints, with `--no-build-isolation` and a 300-second installation timeout; failures/import errors are recorded and use the torch fallback. Package versions are recorded when an installation was attempted. No torch installation command is issued.
- Runtime acceleration failures clear partial gradients, restore RNG, and retry the uncommitted window with eager, torch linear attention and batch1. DDP worker failure resumes the latest complete checkpoint, or restarts the epoch if none exists; this can repeat work since the last checkpoint. Cross-world fallback preserves completed data windows, not bitwise-identical dropout streams. GPU failures remain locally unverified.
- Target CE is token-normalized across each global 16-example window. Final window has 15 examples (499 optimizer attempts total). GradScaler overflow attempts are recorded/skipped, matching pilot behavior; they still consume their data windows. Early fp16 calibration overflows require Claude's review.
- Wall time includes setup in the process and is checked between optimizer windows, so one window plus checkpoint I/O can overrun the nominal limit. Model construction must finish even for an already-expired limit so a valid adapter/checkpoint can be saved. The DDP launcher has an additional 10-minute cleanup allowance and terminates the worker process group on timeout.
- Benchmark projection includes calibration attempts, uses the same seeded initial data windows for each configuration, and excludes downloads/install/checkpoint overhead. Length grouping and later longer samples can change full-epoch speed. Dual-GPU cost is conservatively wall hours × 2; account quota billing and already-used hours must be checked by Claude.
- This task did not edit `docs/`, `kaggle/pilot/`, `kaggle/sft_data/`, `usersim/replay.py`, existing `outputs/`, `kaggle/eval_gen/`, or `scripts/judge_lora_eval.py`, and did not call Spark/paid APIs. Protected hash audit found `docs/history.md` changed during concurrent work; it was not edited or reverted by this implementation. Other pre-existing/concurrent worktree changes were preserved.
- Suggested docs updates for Claude: sampled counts and tokenizer totals, target cleanup ratios, and actual T4 benchmark/training configuration/results after the GPU run.
