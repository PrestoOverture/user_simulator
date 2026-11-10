### Changed Files
- `scripts/build_lora_eval_inputs.py` — exports the frozen 500-dialogue P2 population through `replay.prompt`, without sampling.
- `kaggle/eval_data/{eval_turns.jsonl,manifest.json}` — 4,710 prompts, original `dialogue_id:turn` IDs, input/output SHA256.
- `kaggle/eval_gen/{gen.py,kernel-metadata.json}` — standalone Qwen fp16 base/PEFT generation, protected torch installation, environment log, length batches with left padding, 256-token cap, durable append/resume, zero-based shards and wall-clock guard.
- `scripts/judge_lora_eval.py` — frozen 100-dialogue Gemini registration, fingerprint reuse, explicit paid authorization, P2 Spark judging and unchanged P2 PPI/bootstrap computation; historical P2 decision/v2 results read directly.
- `tests/test_lora_eval.py` — deterministic export, all prompt equality and future-prefix sentinels, P2 IDs, resume including interrupted append, shard partition, zero paid requests without authorization, mock report.
- `outputs/lora_eval/{report.md,summary.json,projection.json,preregistration_ppi.json}` — local **mock** report and copied registration; not experimental findings.
- `kaggle/EVAL_HANDOFF.md` — reproducible commands and operational limits.

### Validation
Run from repository root. The project declares no typecheck/build command; compileall is used for syntax validation.

```sh
.venv/bin/python scripts/build_lora_eval_inputs.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q scripts/build_lora_eval_inputs.py scripts/judge_lora_eval.py kaggle/eval_gen/gen.py tests/test_lora_eval.py
git diff --check
```

Final full suite: **249 passed in 168.94s**. Compileall and `git diff --check` passed.

Builder was run repeatedly: 500 dialogues / **4,710 records**, byte-identical output. Tests compare **every** message list directly to `usersim.replay.prompt` and mutate the current/future suffix to prove prefix isolation.

SHA256:
- eval_turns.jsonl: `de4432419808268847a2d153746bc4c3ec6af6bec06012716f457a268be5063a`
- manifest.json: `825a8b0f9ce7380e4d955005f01d1b98d59b6ff674592e4a173ac39e85b60901`

CPU smoke uses the existing isolated `/tmp/usersim-d9-venv` (torch 2.10.0, transformers 5.3.0, peft 0.18.1, accelerate 1.12.0). No model download. For a new machine, install these packages in an isolated local environment; **do not install torch on Kaggle**. Use an empty output directory for a fresh smoke:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/eval_gen/gen.py --smoke --variant base --data /tmp/lora-missing.jsonl --output /tmp/lora-eval-cpu --shard 0/2
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/eval_gen/gen.py --smoke --variant base --data /tmp/lora-missing.jsonl --output /tmp/lora-eval-cpu --shard 0/2
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/eval_gen/gen.py --smoke --variant base --data /tmp/lora-missing.jsonl --output /tmp/lora-eval-cpu --shard 1/2
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/eval_gen/gen.py --smoke --variant lora --data /tmp/lora-missing.jsonl --output /tmp/lora-eval-cpu --shard 0/2
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/eval_gen/gen.py --smoke --variant lora --data /tmp/lora-missing.jsonl --output /tmp/lora-eval-cpu --shard 0/2
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/usersim-d9-venv/bin/python kaggle/eval_gen/gen.py --smoke --variant lora --data /tmp/lora-missing.jsonl --output /tmp/lora-eval-cpu --shard 1/2
```

`/tmp/lora-missing.jsonl` must not exist: smoke then uses four built-in short fixtures. LoRA smoke creates actual PEFT weights. Also verified the same six-command sequence in `/tmp/lora-eval-final`, with the LoRA commands adding `--adapter /tmp/usersim-d9-offline/checkpoint-2` to load the prior pilot's actual saved adapter. Repeating shard 0 preserved generation bytes; adding shard 1 yielded exactly four unique IDs for each variant, all token counts ≤256.

To reproduce the saved mock report with complete P2-compatible fake generations:

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
p=Path('/tmp/lora-eval-fixtures')
p.mkdir(exist_ok=True)
rows=json.loads(Path('outputs/correction_decision/ppi_eligible.json').read_text())
for variant in ('base','lora'):
    (p/f'{variant}.jsonl').write_text(''.join(json.dumps(dict(request_id=r['request_id'],variant=variant,text='好的'))+'\n' for r in rows))
PY
.venv/bin/python scripts/judge_lora_eval.py --mock --base /tmp/lora-eval-fixtures/base.jsonl --lora /tmp/lora-eval-fixtures/lora.jsonl
.venv/bin/python scripts/judge_lora_eval.py --plan --output /tmp/lora-eval-plan
```

The plan without real outputs reuses **827** unique Gemini human judgments from the registered sample's **827** eligible turns. Base+LoRA need at most **1,654** new unique judgments before identical-output reuse. P2's measured $0.001373442/judgment gives **$2.271673** expected / **$6.815019** with three transient attempts. Actual outputs can reduce this. Fake “好的” generations give 858 reused unique inputs and 768 new unique inputs; these fixture numbers must not be used to budget real Qwen outputs. Runtime prints exact fingerprint-based counts before any new requests. Default `--max-paid-usd 7` applies a billing stop with the existing $0.50/request reserve, plus P2's shared-account $49 stop. Historical averages/reserves cannot guarantee a provider invoice ceiling.

Kaggle commands below are for Claude to execute later; **none were executed**. The dataset is private by default. Keep the existing CSDS access/license conditions; `other` does not grant a new license.

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
Path('kaggle/eval_data/dataset-metadata.json').write_text(json.dumps({
 'title':'CSDS usersim frozen evaluation',
 'id':'prestooverture/csds-usersim-eval',
 'licenses':[{'name':'other'}]
},indent=2)+'\n')
PY
.venv/bin/kaggle datasets create -p kaggle/eval_data
.venv/bin/kaggle kernels push -p kaggle/eval_gen
```

Base metadata runs gen.py with defaults. For LoRA, stage a separate kernel with a wrapper (set ADAPTER_PATH to the actual mounted adapter folder containing adapter_config.json and adapter weights):

```sh
export ADAPTER_PATH='/kaggle/input/csds-qwen35-lora-train/adapter'
.venv/bin/python - <<'PY'
import json,os,shutil
from pathlib import Path
stage=Path('/tmp/lora-eval-kernel')
stage.mkdir(exist_ok=True)
shutil.copy('kaggle/eval_gen/gen.py',stage/'gen.py')
m=json.loads(Path('kaggle/eval_gen/kernel-metadata.json').read_text())
m.update(id='prestooverture/csds-qwen35-lora-eval-adapter',code_file='run_eval.py',kernel_sources=['prestooverture/csds-qwen35-lora-train'])
(stage/'kernel-metadata.json').write_text(json.dumps(m,indent=2)+'\n')
(stage/'run_eval.py').write_text('import sys\nsys.argv='+repr(['gen.py','--variant','lora','--adapter',os.environ['ADAPTER_PATH']])+'\n'+Path('kaggle/eval_gen/gen.py').read_text())
PY
.venv/bin/kaggle kernels push -p /tmp/lora-eval-kernel
```

Inspect the actual train kernel output mount before choosing ADAPTER_PATH. A private adapter **dataset** is also supported: add its slug to dataset_sources and pass its mounted folder via --adapter. Both modes read only that folder's PEFT files.

For two T4s, run the following on Kaggle (base shown; add `--variant lora --adapter /actual/adapter` for LoRA). Use separate output directories, since each generation file has an exclusive writer lock:

```sh
python gen.py --device 0 --shard 0/2 --output /kaggle/working/shard0 &
python gen.py --device 1 --shard 1/2 --output /kaggle/working/shard1 &
wait
cat /kaggle/working/shard0/generations_base.jsonl /kaggle/working/shard1/generations_base.jsonl > /kaggle/working/generations_base.jsonl
```

After downloading both variants, the live judging command (NOT executed) is:

```sh
.venv/bin/python scripts/judge_lora_eval.py --base /path/generations_base.jsonl --lora /path/generations_lora.jsonl --run-spark --allow-paid --max-paid-usd 7
```

Omit --allow-paid to guarantee zero paid client construction/requests; missing calibration then prevents a full PPI report. No model fallback. Unresolved paid dispatches stop instead of being silently resent.

### Success Conditions
- [x] Frozen input export: 4,710 IDs, repeat SHA256 equal; all prompt messages equal replay.prompt.
- [x] Full pytest: 249 passed in 168.94s; required new regression coverage passed.
- [x] CPU base + actual PEFT LoRA, saved adapter loading, byte-identical resume, shard union, and mock report verified; exact commands above.
- [x] Gemini reuse/new-request estimates and private dataset/kernel push commands supplied; no push or live judging performed.

### Risks & Deviations
- GPU/Qwen weights, T4 fit, GPU throughput and actual live judges remain untested by design; CPU uses random GPT2 + PEFT. The target pipeline is prepared, not a completed LoRA experiment.
- The 4,710-turn generation population includes initial turns. Judging inherits P2's 4,299 eligible turns (411 exclusions, including no previous agent / historical generation moderation). Additional empty/missing generations and invalid judgments are jointly excluded and counted. P2 v2 historical numbers retain their original 4,300-turn support; decision P2 retains its own support. No historical numbers are recomputed.
- The report explicitly records ADR-018's post-hoc adoption; it does not claim that the original efficiency gate passed.
- P2 compute is reused unchanged by supplying each variant through its existing source slots and exporting only that slot's continue/emotion gaps. Internal adherence/reduction diagnostics are discarded.
- Wall-clock is checked before each batch; a running batch/model setup may overrun the deadline. No replay prefix is truncated to fit memory/context.
- Resume validates inputs/config/adapter hashes, rejects duplicate IDs, and removes only an incomplete final append. Different variant/shard workers should use separate output directories; merge their disjoint shards afterward.
- Paid budget uses P2's live shared-account billing and reserve. A single provider charge or billing lag can overshoot a stop threshold. Request-cost projections are empirical estimates, not guaranteed tariff caps.
- Existing changes in docs/, pilot/, train/, build_sft_data.py and other contracts were not edited. No docs update was made; Claude may document this pipeline and eventual GPU/real-judgment results after review.
