### Changed Files

- scripts/measure_reaction_deepseek.py — 本 Delta 新增 BJudge：网络层错误和 5xx 最多 3 次总尝试，指数退避；逐次保存 dispatch/response；按 B 全部响应尝试（含历史缓存）计算失败率，超过 2% 或同一条连续失败 3 次就停止。两条旧失败从 attempt 2 续跑，仍计入三次上限。新增 B 专用 parse_b，仅 strip 字段名并拒绝规范化后的重复键，字段集合和取值继续使用冻结的严格校验。共用 usersim/judge.py 未改。
- scripts/measure_reaction_deepseek.py — 修正最终报告的请求计数，按 billing.jsonl 的付费前检查计数，避免旧停止期间未发送任务的 finally 初始化日志被误算为模型请求；不改变统计口径。
- tests/test_reaction_deepseek.py — 本任务最终 29 项测试，新增 ReadTimeout/ProxyError/ConnectError/502/503/504 后重试成功、连续三次失败停止、整体失败率停止、旧失败保留尝试上限、空白字段名通过、非法取值/字段/重复键仍停止等测试。
- outputs/reaction/deepseek/resume_judge_network.json — 保存 Claude 本次授权、解除原因和原 STOPPED.json 全文；随后按授权解除停止。
- outputs/reaction/deepseek/judgments.jsonl、billing.jsonl、response_diagnostics.jsonl、full.log、background_run.json — 后台复用全部 3000 轮生成及 2164 个判断，仅补齐剩余判断；原始尝试和失败证据保留。
- outputs/reaction/deepseek/report.md、summary.json — 已生成完整三方共同有效统计、交互项/区间、预先登记判定和费用。
- outputs/reaction/deepseek/offline_validation.json、completion_audit.json、protected_paths_validation.json、credential_scan.json — 双次无网络重建哈希、缓存/用量/统计核查、受保护目录和凭证扫描证据。
- outputs/reaction/deepseek/handoff.md — 本次最终交接。未改 docs、correction_decision、spark_judge_validation 或它们的代码/测试；未提交 git。

### Validation

- `.venv/bin/python -m pytest -q tests/test_reaction_deepseek.py` — 29 passed in 4.96s。
- `.venv/bin/python -m pytest -q` — 最终 180 passed in 37.95s。较早重跑出现过并发 C 任务缺少 judge_batch 的两项导入失败，在 C 补齐后消失；本任务未修改 C。
- `.venv/bin/python scripts/measure_reaction.py --sim-dir outputs/full_csds/spark --out outputs/reaction/full --report-only` — 最终退出 0；Requests=6245、paired=2999，token 汇总与原报告一致。
- `git diff --exit-code -- outputs/reaction/full` — 退出 0。
- `git diff --exit-code -- usersim/judge.py` — 退出 0。
- `git diff --exit-code -- docs` — 最终退出 0。原有 docs/tech_stacks.md 并发修改已由外部提交 1e17b35（仅文档变更），本任务没有修改或提交 docs。较早的退出 1 已解除。
- DeepSeek `--report-only` 连续重建两次：移除 SANDBOX_API_KEY、SANDBOX_BASE_URL、SPARK_API_KEY，并用 unittest.mock 禁止 httpx.Client、AsyncClient、get、post；两次均退出 0。所有生成、usage、判断、billing、响应诊断日志的 SHA-256 前后不变。
- report.md 两次 SHA-256 均为 `8c44c563b259dd02c143a8a1f8dcb091d68295bd3d29f5cf8496518832fd3047`。
- summary.json 两次 SHA-256 均为 `b4a79214b370343236710fe430b77ec174ff3191541bb42c44d9b376148a73c9`。
- completion_audit.json：3000 轮清单和 prompt 指纹通过；复用 5999 个真人/Spark 判断；3000 个唯一 DeepSeek 判断全部成功，所有原始文本经 B 解析器重新解析与缓存预测一致；3002 个保存的 DeepSeek 成功响应均通过零推理检查。当前数据没有实际空白字段名输出，容错分支由测试验证。
- Gemini 共 4 次失败尝试，均已有成功重试：两条历史 RuntimeError，以及本轮 deepseek:1582:13、deepseek:8835:2 的 504，均在 attempt 2 成功。失败率 4/3004≈0.1332%，未触发 2% 或连续三次门槛。没有未决 dispatch。
- 请求前检查计数：DeepSeek 3011（含原已放弃的 8 次）、Gemini 3003，均不超过 3500。与判断缓存 3004 次 response 的差异来自旧停止前记录了 dispatch 但未实际发出的请求；未清洗或伪造原始记录。
- 最终网关快照 3296.8976 美分，低于 4900；共享账户从本任务最初 2712.6994 美分起差额 $5.841982，按费用上界报告。小批预测 $4.551 已在此前通过低于 $7 门槛。
- 凭证扫描：对本任务脚本、测试及两个产物目录读取 .env 实际 API_KEY 作精确匹配，同时扫描 sk-/Bearer 长凭证模式，无命中；结果见 credential_scan.json，未输出凭证值。
- 项目没有独立声明的 typecheck/build 命令；运行 pytest。

本次启动命令（已完成，无需再次续跑）：

```sh
nohup /Users/kwang/projects/user_simulator/.venv/bin/python -u /Users/kwang/projects/user_simulator/scripts/measure_reaction_deepseek.py --concurrency 8 >> /Users/kwang/projects/user_simulator/outputs/reaction/deepseek/full.log 2>&1 < /dev/null &
```

本次 PID 49110，2026-10-27T17:59:11Z 启动；18:14:42Z 已观察到进程不存在、全部判断完成、无 STOPPED.json，日志最后输出 common_valid=2999、excluded=1、decision=用普遍定义。full.log 保留此前多轮停止历史，最后这一段已完成。

Claude 可复跑的离线报告/验收命令（均在项目根目录；不需要恢复付费进程）：

```sh
env -u SANDBOX_API_KEY -u SANDBOX_BASE_URL -u SPARK_API_KEY .venv/bin/python scripts/measure_reaction_deepseek.py --report-only
shasum -a 256 outputs/reaction/deepseek/report.md outputs/reaction/deepseek/summary.json > /tmp/reaction-deepseek-sha-first.txt
env -u SANDBOX_API_KEY -u SANDBOX_BASE_URL -u SPARK_API_KEY .venv/bin/python scripts/measure_reaction_deepseek.py --report-only
shasum -a 256 outputs/reaction/deepseek/report.md outputs/reaction/deepseek/summary.json > /tmp/reaction-deepseek-sha-second.txt
diff -u /tmp/reaction-deepseek-sha-first.txt /tmp/reaction-deepseek-sha-second.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/measure_reaction.py --sim-dir outputs/full_csds/spark --out outputs/reaction/full --report-only
git diff --exit-code -- outputs/reaction/full
git diff --exit-code -- usersim/judge.py
git diff --exit-code -- docs
```

### Success Conditions

- [x] 本 Delta：瞬时错误重试成功、连续三次失败停止、空白字段名解析通过、非法取值仍失败；实际两次新 504 也自动重试成功。
- [x] 原合同测试：完整 pytest 最终 180 passed；原清单不一致负例、逐轮 Spark prompt 指纹一致、实际双字段请求体和零推理规则测试保留并通过。
- [x] full `--report-only` 与 HEAD 不变，整个目录 git diff 退出 0。
- [x] DeepSeek 离线两次重建 SHA-256 完全一致；移除凭证并禁止网络，原始日志未变化。
- [x] DeepSeek 报告内容齐全：两组 continue/情绪差距、交互项和 95% CI、预先登记判定、Spark 同口径对照、共同有效/剔除数、推理 token=0 和费用均已核查。
- [x] 测量结果：共同有效 2999 轮（负面1500、非负面1499），唯一剔除为 Spark 审核拒绝 2069:11；Spark 数字与 full 精确一致。DeepSeek continue 差距 +27.5333/+23.7492 个百分点，交互项 +3.7842 个百分点，95% CI [-0.1442,+7.7724]，含零，按预先登记规则“用普遍定义”；情绪差距 +14.2667/+7.0714 个百分点，不参与判定。
- [x] 本任务凭证扫描无命中；共用 usersim/judge.py 未修改，受保护任务文件未触碰。
- [x] 原合同 docs 整体 git diff 退出 0：最终检查已通过；原有并发修改已在外部提交 1e17b35，本任务未修改或提交 docs。原合同所有验收条件均已满足。

### Risks & Deviations

- 本次在 30 分钟内完成全部剩余判断、报告和离线验收，未重新生成 DeepSeek、未重判真人/Spark、未更换模型或调整统计/判定口径。
- docs 的既有并发差异已通过外部提交 1e17b35 解除；最终 docs/full/共用 judge.py 三项差异检查全部退出 0。本次 Delta 两项 Failed Success Conditions 及原合同全部条件均已验证通过，无剩余阻塞。
- 失败率口径为 B 累计 response 尝试（含历史缓存）中的非成功比例；旧失败计入每条连续三次尝试上限。原始失败保留，缓存复用后未挑选模型输出。
- 旧停止造成的未发送 finally 初始化日志仍在 request_usage.jsonl，不能把其行数视为付费请求数；报告已改按付费前记录计数。原放弃的 8 次无可用响应按先前 Claude Delta 授权不纳入有效生成的推理 token 核验；其费用和请求计数仍保留。
- 费用是共享账户累计差额上界，包含可能的其他任务和诊断用量，不能独立归因成本。审核剔除带来少量选择偏差，逐轮重放无法观察真人直接离开。
- 建议 docs：由 Claude 在验收后更新 history/roadmap，记录 DeepSeek 也按预先登记规则使用普遍定义；本任务未直接修改 docs。
