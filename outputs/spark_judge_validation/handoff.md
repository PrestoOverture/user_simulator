### Changed Files
- `usersim/judge_spark.py` — 公共 `judge_batch` 保留原参数和默认值，仅增加可选测试时钟/睡眠/抖动注入；429不计普通传输预算、无限重试，5秒指数退避至300秒并加固定种子的随机抖动；并发逐次减半至1、连续200条新成功后加1至8。共享60分钟无成功监控持久化；独占缓存写锁支持进程中断后的安全续跑。
- `scripts/validate_spark_judge.py` — 增加守护模式与自动离线验收；保留原解析失败2%停止线及普通传输/5xx四次预算。JSON产物采用原子替换，防止中断时把半个PPI文件当作已完成；未更改PPI统计方法或预登记。
- `scripts/run_spark_judge_full.sh` — 新增可执行的小型守护入口，加载环境后运行 `--supervise-full`；意外退出从缓存恢复，真实停止条件不重启；全量和PPI结束后执行离线验收并保存输出。
- `tests/test_spark_judge.py` — 新增429预算豁免、指数退避上限、并发减半/200成功恢复、模拟时钟60分钟停止、重启不清零无成功计时、历史429重置、孤立dispatch恢复和守护重启/停止测试。
- `outputs/spark_judge_validation/launch_background.py`、`run_remaining.sh` — nohup独立会话改为启动守护入口；后台只运行full/PPI/验收，不重跑开发集或测试集。
- `outputs/spark_judge_validation/` — 新增 adaptive_* 基线、授权重置、增长和验证记录；更新报告及本交接。未修改 correction_decision、reaction/deepseek、冻结prompt/测试集、PPI实现、预登记或docs；任务期间docs有外部提交，当前受保护diff为0。

### Validation
- `.venv/bin/python -m pytest -q`：**203 passed in 37.85s**，见 `adaptive_tests.log`。
- `.venv/bin/python -m pytest -q tests/test_spark_judge.py tests/test_ppi.py`：**45 passed**。模拟7次429+3次503后成功，普通预算仍为第4次；连续429在模拟时间3600秒停止；并发8→4→2→1及第200条成功后恢复均覆盖。
- `.venv/bin/python -m py_compile usersim/judge_spark.py usersim/ppi.py scripts/validate_spark_judge.py tests/test_spark_judge.py tests/test_ppi.py`、`bash -n scripts/run_spark_judge_full.sh`：退出码0。
- 原两个429耗尽输入均恢复成功，旧attempt=1/2/3/4原样保留；新成功记录为 **wire attempt=5、transport_attempt=1**。旧429被新规则豁免，没有修改原日志或重发已有成功条目。见 `adaptive_reset_audit.json`、`adaptive_growth_check.json`、`adaptive_verification.json`。
- 已启动nohup独立会话：守护PID **54725**、PPID=1、PGID=54725；worker PID **54727**。跨启动器退出后的独立调用确认存活且缓存持续增长。
- 最终核对快照：full **3295 ok、28条历史request_error、1条新rate_limited**，较本次开始2843条增加452条成功。实际并发已从8降到4，并在连续200条成功后恢复到5；最终核对时距上一条成功约0.45秒，没有stop标记。动态速率与当前并发以 `full.jsonl.runtime.json` 为准，不承诺固定完成时刻。
- 冻结prompt、test.jsonl、selection.json、preregistration.json、analysis_plan.json、PPI实现/原测试及usersim/judge.py、usersim/spark.py SHA-256与本轮基线一致。测试集没有重跑。
- 当前检查点 `--report-only` 连续两次SHA-256一致，验证子进程移除了两个API key；见 `adaptive_rebuild_check.json`。完整实验结束后守护脚本会自动再做此项。
- `git diff --exit-code -- docs usersim/judge.py usersim/spark.py`：退出码 **0**。实际环境凭证和key格式扫描无命中；见 `adaptive_verification.json`。docs在本轮期间的外部提交未由本任务改动或回滚。

启动/续跑入口（现在已运行，勿重复启动）：
```sh
cd /Users/kwang/projects/user_simulator
.venv/bin/python outputs/spark_judge_validation/launch_background.py
```
启动器实际执行 `nohup /bin/bash /Users/kwang/projects/user_simulator/scripts/run_spark_judge_full.sh`，设置独立进程会话、stdin=/dev/null、stdout/stderr追加到 `outputs/spark_judge_validation/background.log`。守护脚本内部运行：
```sh
.venv/bin/python -u scripts/validate_spark_judge.py --supervise-full --concurrency 8
```
只调用Spark，不构造付费模型客户端。concurrency是初始值，自适应范围1..8；A和C进程各自维护自己的状态，不共享全局并发控制。

监控命令：
```sh
ps -p "$(cat outputs/spark_judge_validation/background.pid)" -o pid=,ppid=,pgid=,etime=,args=
tail -n 30 outputs/spark_judge_validation/background.log
cat outputs/spark_judge_validation/supervisor_state.json
cat outputs/spark_judge_validation/full.jsonl.runtime.json
```
`supervisor_state.json`记录worker_pid、阶段和重启次数；`.runtime.json`记录当前并发、连续成功数、last_success_wall_time、429次数及policy。日志追加保留历史：旧版429停止Traceback不是当前进程状态，以新policy和实际PID/状态文件为准。

守护行为与缓存语义：
- 意外worker退出且没有真实停止标记：5秒后从缓存重启。若上次守护意外退出而旧worker仍活着，先等待旧worker，不启动双写进程。
- 独占 `full.jsonl.lock` 后，才能把前一已退出写者留下的dispatch记为InterruptedProcess/usage未知，再按普通传输预算续跑；不盲目删除dispatch，不伪造预测。进程锁在崩溃时由OS释放。
- 429的每次HTTP调用保留独立递增的 `attempt` 作为日志流水号；四次预算看 `transport_attempt` / 非429错误计数。新429记为rate_limited、budget_consumed=false；原历史429不改写，也不计预算。
- 429等待为base=5×2^k、base上限300秒，再加0..20%固定种子抖动并保持最终等待≤300秒；种子20261027，按输入指纹和重试序号派生，不影响PPI抽样种子。
- 429立即并发减半，最低1；只有新解析成功的响应计入连续成功数，每200条加1，最高8。缓存命中不计作新成功。
- 连续60分钟没有任何新成功时停止，计时跨意外重启保留，不重置；PPI离线阶段不受Spark请求无成功计时影响。原2%解析失败线和非429传输/5xx四次预算仍为真实停止条件。
- 停止条件写入 `full.jsonl.stop.json`，守护阶段变为stopped，不自行重启或清除标记。不要手工清空缓存或stop文件来绕过条件。

公共接口继续兼容：
```python
from usersim.judge_spark import judge_batch
rows = judge_batch(cache_path, items, 8,
    reuse_caches=["outputs/spark_judge_validation/full.jsonl"])
```
原关键字prompt、thinking、reuse_caches、transport、on_result保留；新增可选clock/sleeper/jitter仅用于测试。按冻结prompt+白名单输入指纹、Spark模型和thinking复用；donor只读；返回映射键为调用方输入ID。真实统计停止回调可抛 `JudgeStopped`，守护会保留该停止条件。

全量及原有六组PPI自动结束后，守护执行并保存：
1. 两次 `.venv/bin/python scripts/validate_spark_judge.py --report-only`，两个API key从验证子进程环境中移除；输出 `automatic_report_1.log`、`automatic_report_2.log`。
2. 计算两次report.md/summary.json的SHA-256并比较，写入 `final_sha256_1.txt`、`final_sha256_2.txt` 和总验证JSON。
3. `.venv/bin/python -m pytest -q` → `automatic_pytest.log`。
4. 原有Python编译检查 → `automatic_compile.log`。
5. `git diff --exit-code -- docs usersim/judge.py usersim/spark.py` → `automatic_protected_diff.log`。
6. 凭证扫描、summary.status=complete、六个PPI文件和所有Gemini对照n_pending=0检查，汇总到 **`automatic_verification.json`**。
全部通过时supervisor_state.stage=complete；验收失败时为checks_failed，输出保留供Claude审阅，不通过重跑模型来“刷绿”。Claude仍需审阅最终表格、采纳结论及风险。

### Success Conditions
- [x] 429不消耗普通预算、无限重试、5秒起指数退避至300秒加抖动，历史记录保留；两个原耗尽输入已实际恢复。
- [x] 自适应并发减半至1、连续200成功加1至8；新增模拟和端到端测试通过，实际已观察8→4→5且继续成功。
- [x] 模拟时钟验证60分钟无成功停止；重启不会清零计时；普通错误及5xx仍为四次预算。
- [x] 公共批量接口原函数签名兼容，只增加可选参数；未改冻结prompt、测试集、PPI或预登记。
- [x] nohup守护已启动、脱离前台并持续增长；意外重启、真实停止不重启有测试；离线自动验收已接入并定义输出文件。
- [x] 全套203项测试、编译/语法检查、当前检查点两次离线重建哈希一致、受保护diff=0、凭证扫描无命中。
- [ ] 完整report.md的Gemini一致率、PPI覆盖率/宽度、交互项判定一致性和采纳结论，待后台完成。
- [ ] 最终完整报告的连续两次离线SHA-256验收，待守护在全量/PPI后自动执行。

### Risks & Deviations
- 按本Delta要求写完交接后结束前台任务，不等待全量；整个Goal仍未完成。当前为运行中，不再受旧版“四次429耗尽”的阻断。
- 限速和其他Spark任务的负载会改变实际并发与速度；不按短窗口外推固定完成时间。新规则允许429无限重试，但无成功监控和其他原停止条件仍会停止任务。
- 意外进程中断、历史传输错误和没有usage的响应，其实际token用量可能未知；原记录保留，报告不冒充0。没有重试已成功或解析失败的内容，没有模型回退。
- 本任务没有触碰 correction_decision、reaction/deepseek 或其进程，也没有修改docs；没有git提交。PPI统计与预登记未调整。
