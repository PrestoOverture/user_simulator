### Changed Files
- scripts/run_correction_decision_ppi.py — Spark按缓存累计的终态响应计算解析失败比例，严格超过2%才停止；不重试解析失败内容；parse_error与moderated均使整轮从所有组共同剔除，报告及summary列出各数量与去重总数。Gemini规则未改。
- scripts/check_correction_decision_ppi.py — 独立重建共同有效集合、核对解析缺失剔除和累计阈值；Spark仍使用C解析器，不新增特殊提取规则。
- scripts/supervise_correction_decision.py — A专用单实例守护：无停止条件的意外退出5秒后缓存续跑；保护停止条件及未知付费dispatch；正常完成后自动重建两次报告、比较SHA-256并执行check、pytest和受保护路径检查，输出文件保留。
- tests/test_correction_decision.py — 新增单条低比例失败不停、严格超过2%停止、全组共同剔除、守护异常恢复与停止保护测试。
- outputs/correction_decision/ — 新测试日志、旧停点归档parse_delta_previous_stop.md、守护启动/状态/增长证据。此前完整交接保存在handoff_before_parse_delta.md。

### Validation
- `.venv/bin/python -m pytest -q` — **212 passed in 38.17s**，parse_delta_full_tests.log。
- `.venv/bin/python -m pytest -q tests/test_correction_decision.py` — **48 passed**，parse_delta_tests.log。
- `.venv/bin/python -m py_compile scripts/run_correction_decision_ppi.py scripts/check_correction_decision_ppi.py scripts/supervise_correction_decision.py tests/test_correction_decision.py` — 退出0。
- `git diff --check` — 退出0。
- `git diff --exit-code -- docs usersim/judge.py usersim/judge_spark.py usersim/ppi.py usersim/correction_v2.py scripts/run_correction_v2.py scripts/measure_reaction.py outputs/correction_v2` — 退出0；judge.py/judge_spark.py/ppi.py哈希与本Delta启动前一致。
- 旧缓存1776条ok、1条parse_error，失败比例1/1777≈0.0563%，低于2%。只归档了这一已获准改变规则的旧停点，没有删除失败记录、刷新传输预算、修改解析器或做双JSON提取。
- **nohup守护PID 80050，工作PID 80051**，已脱离当前工具会话。parse_delta_background.json记录精确命令；ppi_supervisor_state.json记录当前running状态、worker_pid、restarts=0。
- 已确认成功判断从1776增至**1828**，parse_error仍为1；该失败指纹的dispatch仍只有1次。旧Gemini日志及共享模型/统计源码SHA-256未变，新Gemini缓存0行。证据parse_delta_baseline.json、parse_delta_growth.json。
- C进程 **54725/54727**仍存活，未发送信号或修改C产物；没有触碰reaction/deepseek。

后台启动命令（项目根目录；当前实例活着时不要再启动）：

```sh
nohup .venv/bin/python -u scripts/supervise_correction_decision.py --concurrency 4 > outputs/correction_decision/ppi_supervisor.log 2>&1 < /dev/null &
```

守护内部启动：

```sh
.venv/bin/python -u scripts/run_correction_decision.py --phase ppi-full --concurrency 4
```

日志和状态：
- ppi_supervisor.log — 守护决策与意外退出续跑。
- ppi_guard_worker.log — Spark/PPI工作进程输出。
- ppi_supervisor_state.json — supervisor/worker PID、阶段、重启次数或停止原因。
- spark_judgments.jsonl、其.runtime.json/.stop.json — C接口原有的缓存、自适应429状态和持久停止条件；不要自行删除停止标记或重置时间。
- STOPPED.md — 流水线停止。非停止条件的意外Python异常归档为guard_unexpected_*.md后恢复；明确停止不会被当作意外退出。

完成后守护自动执行以下步骤，stdout/stderr分别写guard_report_1.log、guard_report_2.log、guard_check_500.log、guard_pytest.log、guard_protected_diff.log；最终guard_verification.json必须passed=true，且包含两次报告/summary的相同SHA-256和所有命令退出0。check还必须输出verified=true、api_calls=0，否则守护标记checks_failed，不重新发模型请求：

```sh
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500
.venv/bin/python scripts/check_correction_decision.py --n 500
.venv/bin/python -m pytest -q
git diff --exit-code -- docs usersim/correction_v2.py scripts/measure_reaction.py outputs/correction_v2
```

也可在后台完全退出后手动运行上述命令，并在两次report之间分别执行：

```sh
shasum -a 256 outputs/correction_decision/report_500.md outputs/correction_decision/summary_500.json
```

### Success Conditions
- [x] 新增测试通过：单条失败在既有低比例基数下继续；恰好2%继续，超过2%停止；解析失败轮次在所有组共同剔除。
- [x] 未修改C解析器，泄漏think标记的样例仍被拒绝；旧失败输入没有重新请求。Gemini字段名strip后的失败仍停止，守护测试保护此规则。
- [x] 单实例守护、异常退出缓存恢复、停止条件不绕过、自动报告/核验流程已实现并测试；nohup启动后实际判断数增长。
- [x] 全套测试、编译、diff检查通过；只改A自己的文件，未触碰C进程或其他任务。
- [ ] 正式`check_correction_decision.py --n 500` verified=true — 全量仍在后台，待守护自动验收。
- [ ] report_500.md/summary_500.json实际两次重建哈希一致 — 待完整数据，守护已配置执行。
- [ ] report_500.md正式PPI内容齐全 — 待完整数据；实现已增加解析缺失共同剔除数量，不能将实现/单元测试替代实际报告验收。

### Risks & Deviations
- 本次按要求在确认增长后交接，不等待全量，也未宣告原Goal完成。不要再启动旧的全量Gemini入口；100段预登记、PPI方法、目标和提示词均未改。
- 解析率分母按C规则取A缓存实际response中的ok/parse_error/moderated；不计传输/429错误，也不把导入的reuse当成新响应。已有基数下1条失败约0.0563%；没有增加额外“冷启动宽限”或放宽2%阈值。
- 单条解析失败作为缺失处理存在选择偏差风险，按Claude决定共同剔除并报告，不补标签、不重试内容。100段名单不变，PPI使用剔除后的已知共同分母。
- 429退避、并发自适应（最多8）、四次非429传输预算、60分钟无成功停止及崩溃缓存恢复均依赖只读judge_spark.py。守护不会清除.stop.json或重置watchdog。未知付费dispatch也会停止，避免盲目重复付费。
- C验收门槛保持不变：status=complete且adoption_passed=true后才补Gemini子样本。C尚未达标时是明确停止，不由守护反复启动付费阶段。其他Gemini解析/预算停止规则仍适用。
- 之前100段/827轮的初步预算约1.92美元，解析缺失可能减少新请求；不重新抽样。费用与预登记详情见前版handoff及现有预登记文件。
- 未提交git、未修改docs。建议Claude在最终验收后记录2%解析失败缺失政策及实际剔除数；不提前写研究结论。
