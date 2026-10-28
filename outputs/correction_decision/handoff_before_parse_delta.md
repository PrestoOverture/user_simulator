### Changed Files
- usersim/correction_decision.py — 新增A专用字段名strip解析包装；字段集合、topic取值、emotion布尔类型及重复键校验保持严格；固定种子的100段抽样函数。未改原目标池、目标措辞或生成prompt。
- scripts/run_correction_decision.py — 禁用旧`--phase full/smoke`的全量Gemini路径；增加PPI阶段入口；预算累计包括新子样本缓存，保留读账重试、49美元及10000次上限和在途余量。
- scripts/run_correction_decision_ppi.py — 离线重解析、100段预登记、Spark全量与C缓存复用、受限Gemini子样本及最多三次瞬时错误尝试、调用只读usersim.ppi的配对统计和三种口径报告。
- scripts/check_correction_decision.py、scripts/check_correction_decision_ppi.py — 核对预登记、两类模型指纹与来源、PPI重算、确定性及凭证；离线核验期间禁止httpx请求。
- tests/test_correction_decision.py — 新增字段名空白/重复键与取值校验、抽样复现、样本外拒绝请求、瞬时重试上限、旧全量入口关闭、C门槛、非等长对话HT/PPI及离线报告确定性测试。
- outputs/correction_decision/ — preregistration_ppi.json、gemini_reparse.json、Gemini子样本预测、Spark缓存、nohup日志、验证证据。旧生成及Gemini原始日志均未改写。

### Validation
- `.venv/bin/python -m pytest -q` — **195 passed in 37.30s**，ppi_full_tests.log；在共享judge_spark.py外部更新后再次运行通过。
- `.venv/bin/python -m pytest -q tests/test_correction_decision.py` — **39 passed in 5.18s**，ppi_tests.log。
- `.venv/bin/python -m py_compile usersim/correction_decision.py scripts/*correction_decision*.py tests/test_correction_decision.py` — 退出0；`git diff --check`退出0。
- `.venv/bin/python scripts/run_correction_decision.py --phase ppi-plan` — 退出0，preregistered=100、api_calls=0。种子20261027；500段中固定抽100段，827轮；不按已有标签或效果选择。
- 8条历史Gemini parse_error全部由A包装离线恢复，api_calls=0；证据gemini_reparse.json含原内容SHA-256、原attempt和解析结果。原始judgments.jsonl SHA-256未改变；7条历史request_error仍保留。
- 两组生成各4300轮保持原SHA-256；v2共同有效4300轮中，既有A生成审核拒绝共同排除1轮，因此Spark判断输入为4299轮×4来源=17196个逻辑输入，不伪造被拒回复。
- gemini_sample_preflight.json：100段、827轮，decision/oracle合计1654个逻辑输入；复用后1398个唯一待补输入，按已测均价预测 **1.920072美元** 左右（以文件精确值为准）；Spark判断后的额外共同审核排除可能继续减少数量。没有新增Gemini请求。
- 已使用nohup启动 **PID 53594**，并发4（≤8），日志ppi_background.log，命令和UTC启动时间见ppi_background.json。已确认Spark新成功判断从25增至125，257个输入从C缓存复用；新Gemini缓存行数0。证据ppi_background_verified.json。判完Spark后，只有C达标才进入付费子样本阶段。
- `git diff --exit-code -- docs usersim/judge_spark.py usersim/ppi.py usersim/judge.py usersim/correction_v2.py scripts/run_correction_v2.py scripts/measure_reaction.py outputs/correction_v2` — 退出0。A没有编辑保护文件、C目录或reaction/deepseek。
- `scripts/check_correction_decision.py --n 500`和`run_correction_decision.py --phase report --report-n 500`当前均退出1：Spark尚未完成，缺少ppi_support.json；日志ppi_check_pending.log、ppi_report_pending.log。没有把不完整结果报为verified=true或正式报告。

后台命令（项目根目录；**当前PID仍存活时不要重复启动**）：

```sh
nohup .venv/bin/python -u scripts/run_correction_decision.py --phase ppi-full --concurrency 4 > outputs/correction_decision/ppi_background.log 2>&1 < /dev/null &
```

`ppi-full`顺序执行Spark→检查C采纳门槛→仅100段Gemini补判→报告。若C尚未完成或未达标，会在任何新付费请求前停止。C达标且旧进程已退出后，可只续子样本，复用全部已有缓存：

```sh
nohup .venv/bin/python -u scripts/run_correction_decision.py --phase gemini-sample --concurrency 4 > outputs/correction_decision/gemini_sample_background.log 2>&1 < /dev/null &
```

若Spark阶段因传输预算耗尽、解析失败等停止，先检查STOPPED.md及日志；不得自行刷新失败预算或重试已成功内容。获准恢复后才使用ppi-full。**不要再使用旧--phase full，它已被禁用。**

全部判断及后台报告结束后，运行离线验收：

```sh
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500 > outputs/correction_decision/ppi_rebuild_1.log
shasum -a 256 outputs/correction_decision/report_500.md outputs/correction_decision/summary_500.json > outputs/correction_decision/ppi_rebuild_1.sha256
.venv/bin/python scripts/run_correction_decision.py --phase report --report-n 500 > outputs/correction_decision/ppi_rebuild_2.log
shasum -a 256 outputs/correction_decision/report_500.md outputs/correction_decision/summary_500.json > outputs/correction_decision/ppi_rebuild_2.sha256
diff -u outputs/correction_decision/ppi_rebuild_1.sha256 outputs/correction_decision/ppi_rebuild_2.sha256
.venv/bin/python scripts/check_correction_decision.py --n 500
.venv/bin/python -m pytest -q
git diff --exit-code -- docs usersim/correction_v2.py scripts/measure_reaction.py outputs/correction_v2
```

### Success Conditions
- [x] 全套测试及新增解析/抽样/样本外不付费测试通过。原目标池排除和未来文本隔离等测试仍通过；原池排除变异失败证据pool_exclusion_mutation.log保留，未重做已通过工作。
- [x] 8条字段名异常离线恢复，无重请求；100段名单在新请求前冻结；旧全量Gemini入口关闭。
- [x] C批量接口可用且已接入；只写A缓存，Spark thinking enabled、冻结prompt；实际后台进度与C指纹复用已确认；并发4。
- [x] 保护路径git diff退出0；没有修改judge.py、judge_spark.py、ppi.py或C/其他任务产物。
- [ ] `check_correction_decision.py --n 500` verified=true、api_calls=0 — 待长任务完成；代码已核对预登记100段、Spark来源和PPI重算。
- [ ] report_500.md、summary_500.json两次真实重建SHA-256一致 — 待完整数据；离线合成报告的确定性测试已通过，不能替代正式验收。
- [ ] 正式报告内容齐全 — 待完整数据。实现包括baseline/decision/oracle三组PPI差距、绝对差距缩小及配对95%区间；Gemini-only、Spark-only参考；PPI遵守率、目标/实际比例、费用及C前提；目前不宣称全Goal完成。

### Risks & Deviations
- C当前summary.status=incomplete、adoption_passed=null，**不等于通过**。按“前提是C预登记标准达标”处理：免费Spark先跑，新的付费Gemini必须等C的status=complete且adoption_passed=true，并核对其预登记一致。若C未达标，由Claude决定下一步，不自行放宽标准。
- judge_spark.py在本后台启动后被外部C任务更新，A没有编辑它。启动时加载版本与当前文件哈希记录在ppi_launch_baseline.json、ppi_background_verified.json；解析器版本仍为outer_wrappers_v1，PPI源码和冻结prompt未变，当前共享工作树195项测试通过。后台使用启动时加载的版本；若C要求新的共享节流版本，需先确认旧A进程退出再缓存续跑，不能并行写同一缓存。
- 旧Gemini全量判断只作历史缓存，PPI残差严格仅使用预登记100段；不因历史标签或结果重抽样。human/baseline Gemini按v2指纹只读复用；其Spark标签从C复用，C缺失由A自己的Spark缓存补判。
- PPI使用usersim.ppi.estimate与quantile；按100个抽样对话共同bootstrap，Spark总体固定。各指标/遵守率分母使用已知总体计数。HT/PPI估计不裁剪，可能暂超[0,1]；Gemini-only也采用同一已知总体分母的HT口径。Spark-only是固定总体描述，无推断区间。
- Gemini瞬时错误总尝试最多3次（含已有缓存attempt），读账最多6次；字段名容错不重试内容。49美元/10000次上限和4美元在途预留保持；共享账户费用差额不能精确归因，单次0.50美元预留不是网关硬价格上限。新增子样本实际账单与token另存，不重置历史起点。
- 长任务均已脱离前台；按30分钟硬上限在确认进度后交接，不等待全量。不得把仍在进行的后台任务标为已完成；不重启全量Gemini。
- 未提交git，未修改docs。建议Claude在C采纳门槛判定及A全量验收后维护研究记录，区分PPI主口径、两个参考口径和oracle诊断地位。
