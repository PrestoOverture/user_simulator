### Changed Files
- `scripts/build_sft_data.py` — deterministic CSDS train-split SFT builder; directly calls `usersim.replay.prompt`, `user_turns` and `unsegment`.
- `kaggle/sft_data/{train.jsonl,eval_sample.jsonl,manifest.json}` — 65,698 training turns from 7,983 train dialogues; 50 seeded evaluation turns; source/output hashes, seed 20261109 and character counts. Both frozen `inputs.json` files identify the same 500 excluded dialogues.
- `kaggle/pilot/pilot.py` — single CUDA device, unquantized fp16 frozen Qwen weights, fp32 LoRA parameters with fp16 autocast/GradScaler, gradient checkpointing, target-only cross entropy, 200 training steps, checkpoints every 50 steps, optimizer/scaler/RNG resume, base/adapter paired generation and metrics. CPU smoke uses random GPT2 + actual PEFT LoRA through the same training/resume/generation loop. No weights are downloaded in smoke mode.
- `kaggle/pilot/kernel-metadata.json` — private Python script kernel, GPU/internet enabled, placeholder owner and dataset source.
- `scripts/project_kaggle_cost.py` — projects one epoch and both evaluation generations against 30 GPU hours/week. Uses actual 4,710 merged evaluation turns, rather than the contract's approximate 4,300.
- `tests/test_sft.py` — deterministic rebuild, actual training/evaluation ID disjointness, strict prefix sentinels, replay equality, merged/unsegmented target, and prompt/target loss-mask boundary.
- `kaggle/HANDOFF.md` — this handoff. Existing changes to `docs/` and `README.md` predated this task and were not edited.

### Validation
Run from the repository root:

```sh
.venv/bin/python scripts/build_sft_data.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q scripts/build_sft_data.py scripts/project_kaggle_cost.py kaggle/pilot/pilot.py tests/test_sft.py
```

Original complete pytest run: **236 passed in 64.16s**. Delta validation results are recorded below. No project typecheck/build configuration exists; compileall also passes.

Repeated the exact builder command and compared SHA256 of all three output files: identical.

| File | SHA256 |
|---|---|
| train.jsonl | `354cfdc88c31f5c6130b5b52136dd89d0ea6ab0527ce69f7469fbe7c055ff5fd` |
| eval_sample.jsonl | `98ab51c1745f4088bfbca3c6fda69c3b6354fa87714e964ef9c8812ce5895c00` |
| manifest.json | `49a7fe242be62b2afa486a96af6c95c0b8857af87b499616c1f1f2f97b439fd2` |

Isolated environment setup (already performed locally; no changes to project requirements):

```sh
python3 -m venv /tmp/usersim-d9-venv
/tmp/usersim-d9-venv/bin/pip install torch==2.10.0 transformers==5.3.0 peft==0.18.1 accelerate==1.12.0
```

Exact offline smoke and resume commands:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/pilot/pilot.py --smoke --smoke-steps 2 --output /tmp/usersim-d9-offline
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/pilot/pilot.py --smoke --smoke-steps 3 --resume /tmp/usersim-d9-offline/checkpoint-2 --output /tmp/usersim-d9-offline-resume
.venv/bin/python scripts/project_kaggle_cost.py --metrics /tmp/usersim-d9-offline-resume/pilot_metrics.json
```

Schema validation command (stdlib only):

```sh
.venv/bin/python - <<'PYTHON'
import json, runpy
from pathlib import Path
validate = runpy.run_path('kaggle/pilot/pilot.py')['validate_metrics']
for name, steps in [('usersim-d9-offline', 2), ('usersim-d9-offline-resume', 3)]:
    folder = Path('/tmp') / name
    metrics = json.loads((folder / 'pilot_metrics.json').read_text())
    validate(metrics)
    assert metrics['completed_steps'] == steps
    records = [json.loads(line) for line in (folder / 'generations.jsonl').read_text().splitlines()]
    assert len(records) == 4 and {r['variant'] for r in records} == {'base', 'lora'}
print('schema and paired generation PASS')
PYTHON
```

Smoke produces `pilot_metrics.json` and four generation records (two fixtures × base/LoRA). `validate_metrics` in the kernel checks schema version 1, mode, sequential step numbers, finite-or-null losses, nonfinite flags, positive step/generation timing and throughput, and memory counts. Checkpoint 2 resumes at step 3. Additional continuous three-step run compared exact adapter tensors and all three loss values against resumed training: equal. Smoke generated no nonfinite values; peak CUDA memory is correctly 0 on CPU.

Official model verification:
- HF repository: **`Qwen/Qwen3.5-4B`**; [official model card](https://huggingface.co/Qwen/Qwen3.5-4B), including the “Instruct (or Non-Thinking) Mode” section. Verified by reading the model card and HF API, not inferred from the name.
- [Official chat template](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/chat_template.jinja) supports `enable_thinking=False`; used for both SFT prefixes and evaluation. Explicit `return_dict=False` is necessary for Transformers 5.3.0's token-list API.
- Text-only class `Qwen3_5ForCausalLM` exists in pinned Transformers 5.3.0 and supports loading language weights while ignoring the vision encoder. Its installed source was inspected. No substitute model or quantization path is used.
- Kaggle installs only the direct requirements **transformers 5.3.0, peft 0.18.1, accelerate 1.12.0**. It retains image-provided torch/torchvision/torchaudio, pinning their installed distribution versions as pip constraints. Transformers 5.3.0 requires torch>=2.4 (verified in installed `dependency_versions_table.py` and `utils/import_utils.py`); older torch causes an explicit error before pip, without upgrade. Before/after `torch.__version__` is logged; a fresh subprocess checks the post-install version to avoid cached imports, and any change aborts. The existing isolated local smoke environment still has torch 2.10.0; the setup command above is for that local environment only, never Kaggle. Transitive dependencies remain pip-resolved within these constraints.
- Training character count: 24,411,286 prompt + 1,083,977 target = **25,495,263 characters**. Manifest estimates one token per character, explicitly labeled approximate. Separately counted every sample with the actual HF tokenizer: **16,320,405 total tokens**, of which **696,760 target tokens including EOS**. Real GPU pilot repeats exact counting and stores `train_token_counts`; cost projection prefers those exact counts.

Claude's kernel push command (NOT executed): first upload the three files in `kaggle/sft_data/` as the private dataset `<your-kaggle-username>/csds-usersim-sft`, retaining original data licensing/access conditions. Replace `USER_PLACEHOLDER` in kernel metadata:

```sh
export KAGGLE_OWNER='your-kaggle-username'
python3 - <<'PY'
import json, os
from pathlib import Path
p = Path('kaggle/pilot/kernel-metadata.json')
m = json.loads(p.read_text())
m['id'] = os.environ['KAGGLE_OWNER'] + '/csds-qwen35-lora-pilot'
m['dataset_sources'] = [os.environ['KAGGLE_OWNER'] + '/csds-usersim-sft']
p.write_text(json.dumps(m, indent=2) + '\n')
PY
kaggle kernels push -p kaggle/pilot
```

Kernel runs without arguments on Kaggle, installs the three pinned application dependencies automatically while preserving the preinstalled torch stack and locates the attached dataset. Only `cuda:0` is used even if two T4s are exposed. Outputs are `/kaggle/working/pilot_metrics.json`, `/kaggle/working/generations.jsonl`, plus `checkpoint-{50,100,150,200}/`. To resume when a checkpoint is available, invoke `python pilot.py --resume /path/to/checkpoint-50` in the GPU environment; the total remains 200 steps. Attach/download the previous checkpoint as needed; no checkpoint attachment is automatic.

Delta verification: **238 passed in 66.62s** for the full pytest suite; compileall and `git diff --check` pass (no Kaggle push performed):

```sh
.venv/bin/python -m pytest -q
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/pilot/pilot.py --smoke --smoke-steps 2 --output /tmp/usersim-d9-delta
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/pilot/pilot.py --smoke --smoke-steps 3 --resume /tmp/usersim-d9-delta/checkpoint-2 --output /tmp/usersim-d9-delta-resume
.venv/bin/python scripts/project_kaggle_cost.py --metrics /tmp/usersim-d9-delta-resume/pilot_metrics.json
.venv/bin/python -m compileall -q kaggle/pilot/pilot.py tests/test_sft.py
```

Delta changes are limited to `pilot.py`, regression tests for installation/environment, and affected handoff text. CPU 2-step training, step-3 resume and four paired generation records pass, with unchanged loss values. Metrics validation and cost projection pass. Startup logs and `environment` contain Python, torch/transformers/peft/accelerate versions, CUDA version, device-0 GPU name, visible GPU count and a list of each card's total memory in bytes. CPU smoke reports Python 3.14.2, torch 2.10.0, CUDA/GPU null and zero visible GPUs. Resume refreshes environment data rather than retaining the previous checkpoint's machine information. Installer regression tests cover preserved stack constraints, changed-torch rejection and too-old-torch rejection; mocked GPU tests cover both visible cards. Exact builder command was repeated twice: all three data file hashes remain unchanged.

### Success Conditions
- [ ] Delta: Kaggle GPU imports and training start — awaiting Claude's new Kaggle run; local CPU validation cannot prove this condition. Kernel v1 failed after replacing the image torch stack; this revision removes that replacement path.
- [x] Exact builder command creates three artifacts; repeat hashes identical (above).
- [x] New and original tests pass; required leakage/isolation/mask assertions included.
- [x] Offline CPU 2-step training, checkpoint resume, paired generation and metrics schema checks pass; continuous/resumed weights and loss match.
- [x] Cost script accepts smoke metrics and emits Markdown hours/%/30-hour comparison. CPU numbers are explicitly not T4 performance estimates.
- [x] Handoff includes verified HF ID/source, dependency pins, training size/token counts and exact Kaggle kernel push command.

### Risks & Deviations
- Delta Kaggle GPU success remains unverified because this contract forbids pushing; Claude must push and check startup environment plus the first training step. No training parameters or data changed.
- No GPU, Qwen weight download, Kaggle credentials, Kaggle push or paid model calls were used. T4 fit, CUDA compatibility, actual fp16 overflows and real throughput remain the purpose of the Kaggle pilot; CPU smoke cannot establish them. Slow PyTorch fallback for Qwen's hybrid layers may dominate runtime.
- Prompt message contents exactly reuse replay.py; the model-specific chat template adds its assistant/non-thinking prefix. Human target uses existing Chinese-between-character unsegmentation, preserving English/numeric whitespace and multi-message newlines. Target EOS is also supervised; prompt/template tokens are excluded. No replay history is truncated.
- Generation samples with temperature 0.7/top_p 0.8/top_k 20 (official non-thinking recommendations), without an arbitrary production output cap; the model context remainder is the hard limit and `hit_token_limit` is recorded. Smoke alone caps output at four tokens.
- Training uses only original train split, QRole=用户, excluding the 500 frozen evaluation dialogues; held-out CSDS val/test are not added to training.
- Cost extrapolation assumes the fixed 50-turn sample represents the full 500-dialogue set. It includes generation prefill through measured seconds/sample but excludes setup/download/checkpoint I/O. Manifest's character estimate excludes template overhead; real pilot's exact tokenizer count supersedes it. Warmup and sample-length differences can affect estimates.
- Suggested docs updates for Claude: actual merged training/evaluation turn counts, measured tokenizer totals, pinned pilot dependencies and eventual T4 metrics. No docs files were modified here.
