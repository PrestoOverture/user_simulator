### Changed Files
- scripts/run_correction_decision_ppi.py — 门控改为C status=complete且A授权decision=adopt_ppi_post_hoc；保存C实际未达标状态和授权哈希；预测超过3美元停止；付费前锁定样本登记哈希；报告开头如实说明ADR-018事后采纳及reaction/full非负面emotion宽度缩小16.7%<20%。
- scripts/check_correction_decision_ppi.py — 离线核验授权、C完成状态/实际结果/来源哈希、登记哈希；不再错误要求C预登记达标。
- scripts/supervise_correction_decision.py — 支持等待现有前台样本进程后缓存续跑；授权及3美元预测停止不会被守护绕过。
- tests/test_correction_decision.py — 增加缺授权/C未完成不初始化付费客户端、事后授权及等待现有样本测试；保留登记样本外拒绝请求测试。
- outputs/correction_decision/ — 保存本轮运行、登记审计、测试及交接证据；旧停止标记归档post_hoc_previous_stop.md。提前运行离线report产生的样本未齐标记另归档post_hoc_offline_report_pending_stop.md；已核对精确内容和在线请求成功状态，此标记并非模型流水线停止。Claude提供的授权文件未改，原100段登记未改。

### Validation
- .venv/bin/python -m pytest -q — 218 passed in 54.53s（post_hoc_pytest.log）。
- .venv/bin/python -m py_compile scripts/run_correction_decision_ppi.py scripts/check_correction_decision_ppi.py scripts/supervise_correction_decision.py tests/test_correction_decision.py — exit 0。
- git diff --check — exit 0。
- git diff --exit-code -- docs usersim/correction_v2.py scripts/measure_reaction.py outputs/correction_v2 usersim/judge.py usersim/judge_spark.py usersim/ppi.py scripts/run_correction_v2.py — exit 0。
- .venv/bin/python scripts/check_correction_decision.py --n 500 — 当前exit 1，样本请求在途，dispatch/response尚未配齐；post_hoc_check_pending.log。不是verified=true。
- .venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500 — 当前exit 1，Registered Gemini sample incomplete；post_hoc_report_pending.log。尚不能验证正式报告两次哈希。
- 前台命令：.venv/bin/python -u scripts/run_correction_decision.py --phase gemini-sample --concurrency 8 > outputs/correction_decision/post_hoc_sample.log 2>&1，PID 14686。因实测吞吐预测超过30分钟前台时限，按合同交由后台守护。
- nohup .venv/bin/python -u scripts/supervise_correction_decision.py --concurrency 8 --wait-pid 14686 > outputs/correction_decision/post_hoc_supervisor.log 2>&1 < /dev/null & — 守护PID 15465，已实际确认两个进程存活，成功判断从36增长至120以上（post_hoc_growth.json）。不要另起并行样本任务。
- 守护等待当前样本退出，再用ppi-full从缓存续跑；不会并发第二个样本进程。发生明确停止条件或无法确认已付费dispatch时仍停，不盲目重发。当前样本输出post_hoc_sample.log；后续缓存续跑输出ppi_guard_worker.log。
- 最终守护自动执行如下命令，输出guard_report_1.log、guard_report_2.log、guard_check_500.log、guard_pytest.log、guard_protected_diff.log，汇总guard_verification.json（必须passed=true）：

```sh
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500
.venv/bin/python scripts/check_correction_decision.py --n 500
.venv/bin/python -m pytest -q
git diff --exit-code -- docs usersim/correction_v2.py scripts/measure_reaction.py outputs/correction_v2
```

两次report后分别用`shasum -a 256 outputs/correction_decision/report_500.md outputs/correction_decision/summary_500.json`核对。若两个进程都退出且无停止条件，续跑命令为`nohup .venv/bin/python -u scripts/supervise_correction_decision.py --concurrency 8 > outputs/correction_decision/post_hoc_supervisor.log 2>&1 < /dev/null &`。有STOPPED.md或.stop.json时先交回审查，不清除后盲跑。

### Success Conditions
- [x] 全套测试通过；缺授权或C未完成时不发Gemini，授权后仍只允许登记样本；原池排除、未来文本隔离、固定目标句、预算测试保留（原排除变异证据见pool_exclusion_mutation.log）。
- [x] 原100段登记在本轮任何新Gemini请求前存在且未变：prepare冻结校验通过；post_hoc_registration_audit.json显示sample日志尚不存在；preregistration_before_paid.json保存哈希；登记文件SHA-256为2d3065907092cff8672bbaaf4e2b911fe6a84e4996e67914d5859af0bb2452f3。
- [x] C status=complete；adoption_passed=false如实保留；Claude授权文件decision=adopt_ppi_post_hoc，引用ADR-018；预算预测1.9200719247648856美元，未超过3美元。每次付费前仍查账，49美元及10000请求上限不变。
- [x] git diff受保护路径exit 0；未改docs、judge_spark.py、ppi.py、judge.py或C目录；未动C进程；未提交git。
- [ ] check_correction_decision.py --n 500 verified=true/api_calls=0 — 样本仍在运行，待守护自动验收。
- [ ] report_500.md与summary_500.json实际连续两次重建哈希一致 — 待样本完成。
- [ ] 正式report_500.md内容齐全（PPI三组两指标及区间、差距缩小、遵守率、目标/实际、费用、事后采纳说明） — 渲染代码已修改并测试，实际全量报告待数据齐全。

### Risks & Deviations
- 未宣告合同完成。按最新Delta允许的慢请求分支后台交接；观测吞吐不足以在前台30分钟完成1398个未缓存判断。守护自动报告/验收不替代尚未发生的成功证据。
- 固定样本、生成、共同支持集、PPI算法和Spark解析规则均未改。旧登记中的预登记采纳要求保留作为历史记录，其门控由本轮明确事后授权取代，不伪装预登记达标。
- C未达标格子明确披露；事后采纳是用户研究决定。共享账户账单差额可能含其他任务，实际请求usage保留。
- 建议Claude在最终验收后按ADR-018记录A实际样本费用与结果；不需要Codex修改docs。其他原停止条件均不变。
