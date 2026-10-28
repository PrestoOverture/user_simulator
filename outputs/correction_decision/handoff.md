### Changed Files
- scripts/run_correction_decision_ppi.py — A缓存独占文件锁；恢复前检查其他存活Python A写入进程（兼容旧版无锁进程）；确认无写入者后为孤立dispatch追加response/status=abandoned_in_flight/usage=null并fsync。已有ok按指纹复用，abandoned输入重新请求时attempt递增；abandoned不冒充瞬时错误，不消耗原最多3次实际响应尝试预算。报告列出abandoned数量及未知费用。
- scripts/supervise_correction_decision.py — 孤立dispatch在无存活写入者时自动恢复，另有写入者时保持unresolved_gemini_dispatch停止；子进程Popen(start_new_session=True)。
- scripts/check_correction_decision_ppi.py — 离线校验abandoned仅出现在A样本缓存且usage为null。
- tests/test_correction_decision.py — 覆盖退出后孤立恢复及attempt=2重发、已有ok不重发、存活写入者停止且缓存不变、独占锁拒绝并发、守护子进程独立会话。
- outputs/correction_decision/ — 保存abandoned_delta_*验证日志与本交接，原handoff归档handoff_before_abandoned_delta.md。未改正式请求缓存，本轮没有付费调用。

### Validation
- .venv/bin/python -m pytest -q tests/test_correction_decision.py — 第一轮58 passed；随后增加独立会话断言，包含在最终全套测试。
- .venv/bin/python -m pytest -q — 222 passed in 41.58s（abandoned_delta_pytest.log）。
- .venv/bin/python -m py_compile scripts/run_correction_decision_ppi.py scripts/check_correction_decision_ppi.py scripts/supervise_correction_decision.py tests/test_correction_decision.py — exit 0。
- git diff --check — exit 0。
- git diff --exit-code -- docs usersim/correction_v2.py scripts/measure_reaction.py outputs/correction_v2 usersim/judge.py usersim/judge_spark.py usersim/ppi.py scripts/run_correction_v2.py — exit 0；未修改受保护路径。
- .venv/bin/python scripts/check_correction_decision.py --n 500 — exit 1，正式缓存尚有孤立dispatch，dispatch/response未配齐；abandoned_delta_check.log。
- .venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500 — 实际运行两次均exit 1：Registered Gemini sample incomplete；abandoned_delta_report_1.log、abandoned_delta_report_2.log。诊断命令生成的样本未齐STOPPED标记已按精确内容核对归档abandoned_delta_offline_report_pending_stop.md，并非新的API停止。
- 全部命令及退出码：abandoned_delta_validation.json。当前只读观察：无存活A写入进程；705条ok、1条request_error、1条孤立dispatch（707次dispatch）。缓存未提前恢复，交由Claude启动时自动处理。

启动命令（由Claude在自己的shell执行；Codex本轮没有启动后台任务）：

```sh
cd /Users/kwang/projects/user_simulator
nohup .venv/bin/python -u scripts/supervise_correction_decision.py --concurrency 8 > outputs/correction_decision/abandoned_delta_supervisor.log 2>&1 < /dev/null &
```

守护会取得自身单实例锁，确认没有活着的缓存写入者，将1条孤立dispatch记录为abandoned；启动独立会话的ppi-full子进程。子进程按指纹复用所有ok缓存，重新预测剩余费用，超过3美元停止；每次付费前的读账重试、49美元及10000次请求上限不变。不要并行启动第二个A流水线；如明确停止，不删除停止标记后盲跑。

日志：abandoned_delta_supervisor.log（守护）、ppi_guard_worker.log（模型流水线）、ppi_supervisor_state.json（状态）。完成后守护自动执行以下验收，写guard_report_1.log、guard_report_2.log、guard_check_500.log、guard_pytest.log、guard_protected_diff.log；guard_verification.json必须passed=true：

```sh
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500
shasum -a 256 outputs/correction_decision/report_500.md outputs/correction_decision/summary_500.json
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500
shasum -a 256 outputs/correction_decision/report_500.md outputs/correction_decision/summary_500.json
.venv/bin/python scripts/check_correction_decision.py --n 500
.venv/bin/python -m pytest -q
git diff --exit-code -- docs usersim/correction_v2.py scripts/measure_reaction.py outputs/correction_v2
```

守护内以hashlib执行上述哈希对比；若验收失败则标记checks_failed，不重跑模型请求。

### Success Conditions
- [x] 新增恢复测试全部通过：孤立dispatch在旧写入者退出后标为abandoned并重发；已有ok绝不重发；活写入者存在时停止；锁拒绝并发写入。
- [x] abandoned usage=null且未知费用明确披露；attempt递增记录；原瞬时错误上限保留；报告代码列出abandoned数量。实际报告待样本齐全。
- [x] 守护子进程start_new_session=True，有测试断言；本轮未启动长期任务，启动命令已交接Claude。
- [x] 全套测试、编译、diff检查通过；只修改A自己的文件；未提交git。
- [ ] check_correction_decision.py --n 500 verified=true/api_calls=0 — 当前数据未齐，不宣告通过。
- [ ] 正式report_500.md、summary_500.json两次离线重建哈希一致 — 两次实际命令因缺样本均失败，待Claude续跑完成后自动验收。
- [ ] 正式PPI报告内容齐全，含ADR-018事后采纳说明及abandoned数量 — 现有模板保持并增加数量，实际报告待补齐样本。

### Risks & Deviations
- 本轮修复完成并测试，但整体Goal未完成。按最新Delta不启动长时间后台任务，等待Claude从自己的shell启动。
- abandoned请求可能已扣费；usage未知，绝不填0。恢复追加response不撤销历史dispatch，也不从10000次上限中扣除；重发仍增加付费调用计数。未知费用与共享账户归因限制保留。
- 活写入者检测结合文件锁及ps命令行，旧版无锁A进程也会阻止恢复；观察失败时不会假定写入者已退出。文件锁覆盖Gemini阶段整个生命周期；守护只在子进程退出后执行缓存恢复。
- 样本登记SHA-256仍为2d3065907092cff8672bbaaf4e2b911fe6a84e4996e67914d5859af0bb2452f3；未更改样本、PPI算法、授权、生成或判断prompt。预估上限仍3美元、账户上限49美元，不放宽解析失败规则。
- 无需本次修改docs；若需要记录中断未知费用政策，由Claude维护。未动C、reaction/deepseek或其他任务进程及目录。
