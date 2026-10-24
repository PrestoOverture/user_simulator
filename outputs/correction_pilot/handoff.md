### Changed Files
- usersim/anchors.py — 固定种子抽取train用户原话，排除520段评估对话及目标轮次同文；记录C2补齐，复用原prompt。
- scripts/run_correction_pilot.py — 复用run_pilot逐轮生成C1/C2，支持缓存恢复与无API重算；仅写correction_pilot目录。
- scripts/check_correction_pilot.py — 重建示例与prompt、核对全部轮次/配对CSV/指标/审核计数，空输出≥2%非0；同份指标代码重算三条件并生成报告。
- tests/test_anchors.py — 全500段示例池隔离、同文排除、C2 intent、补齐记录和prompt后缀不变测试。
- outputs/correction_pilot/smoke/ — 3段双条件生成与完整校验，均24轮，无审核拒绝/空输出。
- outputs/correction_pilot/protected_sha256.json — 全量运行期间受保护文件的哈希核对清单。

### Validation
- `.venv/bin/python -m pytest -q` — 18 passed in 25.24s，退出0。
- `.venv/bin/python -m compileall -q usersim scripts tests` — 退出0；项目无额外build/typecheck声明。
- 环境变量载入后 `.venv/bin/python scripts/run_correction_pilot.py --n 3 --out outputs/correction_pilot/smoke` — 退出0，C1/C2各24轮，8312/7710已知token。
- `.venv/bin/python scripts/check_correction_pilot.py outputs/correction_pilot/smoke --n 3` — 退出0，三条件各24个有效配对，空输出0。
- 临时复制小批输出并篡改C2 intent后运行同一校验命令 — 非0退出；未修改生产输出。
- `.venv/bin/python scripts/run_correction_pilot.py --n 500` — 正在运行，exec session 11884；最后已确认C1至少200/4710。应继续轮询该session，不因观察超时重启。当前进程启动早于入口末尾自动校验的代码更新，完成后须手动运行全量校验脚本。

### Success Conditions
- [x] pytest通过，含评估池隔离、目标同文排除、C2 intent及有记录补齐。
- [ ] 全量C1/C2与基线配对轮次一致（moderated除外）、空输出<2%：生成中，尚未核验。
- [ ] 全量report.md含三条件均值与差距缩小比例：小批报告已生成；全量待生成后运行`.venv/bin/python scripts/check_correction_pilot.py`。

### Risks & Deviations
- 任务未完成，尚未声明成功；保持目标active，下一轮继续轮询session 11884。
- 既有工作树含前序审查的基线/config修改，本任务未改写这些文件。最终应核对protected_sha256.json和做仅内存凭证扫描。
- 报告按基线绝对差距降序，明确披露单位与公式；每条件取同样的前三个共同有效轮次作并排例子，不按效果筛选。
- C2池不足5条时才随机补齐并逐条记录fallback；当前小批没有补齐。
- 无新增研究决定；未修改docs/，未git commit。建议Claude在最终证据齐全后更新进展记录。
