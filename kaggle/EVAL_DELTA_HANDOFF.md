### Changed Files
- `usersim/judge_spark.py` — separates `auth_failed` from item transport budgets (including legacy 401/403/11200 records), resets the watchdog clock on process initialization, persists exhausted items as `event=terminal/status=transport_failed`, and stops when cumulative unique terminal failures exceed 1% of unique batch inputs or 50 items.
- `scripts/judge_lora_eval.py` — excludes the entire human/base/LoRA pair when any side is `transport_failed`, reports `judge_transport_failed`, and avoids Gemini requests for excluded pairs.
- `tests/test_spark_judge.py` — mock auth/replacement-key/legacy-cache tests, fresh-process watchdog test, four-timeout continuation and resume, threshold boundaries, and offline rebuilding of original P2 decision/v2 reports in temporary copies.
- `tests/test_lora_eval.py` — verifies transport-failed pair exclusion and report count with mock Spark and zero paid requests.
- `kaggle/EVAL_DELTA_HANDOFF.md` — this Delta handoff.

### Validation
No real Spark/Gemini request was made. Report tests write only under pytest temporary directories.

```sh
.venv/bin/python -m pytest -q tests/test_spark_judge.py tests/test_lora_eval.py
.venv/bin/python -m pytest -q tests/test_spark_judge.py -k existing_p2
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q usersim/judge_spark.py scripts/judge_lora_eval.py tests/test_spark_judge.py tests/test_lora_eval.py
git diff --check
```

Final full suite: **262 passed in 112.02s**. Compileall and `git diff --check` passed.

Targeted tests before adding the offline-rebuild test: **58 passed**. The actual P2 offline-rebuild test: **1 passed**; it invokes each original script's `main()` with `--phase report --report-n 500`, twice, after redirecting only output locations to temporary copies. HTTP client creation is forbidden. Rebuilt report/summary hashes equal the original artifacts:

|Artifact|SHA256|
|---|---|
|correction_decision/report_500.md|f7d81b3b937cbffbe7c619253e5e90ba38e768896d77872f007cf44d42448cf8|
|correction_decision/summary_500.json|6b1decdd408a57c3c0310be842a168903fd5d21fa65abb4b61a71d4110408996|
|correction_v2/report_500.md|82dbc16b2d6e4d95090992dbbf46309d67d5248f6b24588715437d6f28274e6a|
|correction_v2/summary_500.json|4aa7cd9b44ca3e9f5be2f48a961ce3856ac731e77bb206ab30533159bdd2331d|

All **24** existing files under `outputs/lora_eval/` were inventoried before changes and checked after testing: names and SHA256 unchanged. No original output was rebuilt in place.

### Success Conditions
- [x] 401/403 and error code 11200 stop immediately as `auth_failed`; no retry/budget debit. Mock tests cover HTTP 200/400 with code 11200 as well. Replaced credentials resume without deleting old cache rows or auth stop files.
- [x] Legacy auth failures do not consume the four-failure budget; attempts remain monotonically increasing.
- [x] A stale runtime/old watchdog stop does not prevent a new process starting; within-process 60-minute no-success protection still passes its existing simulated-clock test.
- [x] Four non-429 transport failures produce durable `transport_failed`; one failure out of 100 unique inputs allows the other 99 to complete. Resume makes zero requests for terminal items. Legacy exhausted items are terminalized without another request.
- [x] More than 1% or more than 50 transport-failed unique inputs stops the batch; exact 1%/50 boundaries are permitted.
- [x] Pair exclusion/report count tested, with no Gemini requests for invalid pairs. PPI, prompts, parser, model, thinking and timeout remain unchanged.
- [x] P2 offline report/summary hashes preserved in two rebuilds each.
- [ ] **Live** `scripts/judge_lora_eval.py --run-spark --allow-paid` completes and produces the real `outputs/lora_eval/report.md`: deliberately not run, as this Delta forbids live judging and changes to that output directory. Claude must resume and verify this condition.

### Risks & Deviations
- Real completion remains unverified; mock success does not prove it. The broader goal remains active pending Claude's live run.
- Retryable stop records `auth_failed`, `no_success_60_minutes` and the legacy `transport_budget_exhausted` are retained but do not veto an explicit new SparkJudge process. Other stop reasons remain blocking. This is needed for credential replacement and the approved per-process watchdog reset; no stop/cache file was deleted.
- Old response bytes are preserved. Terminalization appends a separate `terminal` event, so old dispatch/response pairing and token usage are not invented or rewritten.
- The 1% denominator is the current batch's unique input fingerprints (aliases do not inflate it); the cumulative numerator is unique transport-failed fingerprints in the same cache/model/thinking setting. Reports count excluded paired turns separately.
- No docs/, pilot/, train/, SFT builder or existing output files were edited. No new research decision is proposed.
