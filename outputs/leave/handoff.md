### Changed Files
- usersim/leave.py — 配对抽样、共用重放 prompt、离开/混合规则、对话 bootstrap 和离线报告。
- scripts/run_leave.py — Spark-only 小批/全量/离线入口、自适应并发、429 不耗预算、缓存续跑及全量后自动双重建验收。
- tests/test_leave.py — 前缀无未来泄漏、两类 prompt 同构、固定措辞、三类输出、配对抽样复现、429/审核及缓存复用测试。
- config/closing_phrases.json — 结束语及可移除语气词；必须无剩余业务文本才命中。
- outputs/leave/ — 配对小批 manifest（含真实 prompt、摘要审核标记、全量分布）、generations.jsonl、requests.jsonl、report.md、summary.json、offline_acceptance.json 和此 handoff。initial_pilot/ 保留首次独立抽样小批原始记录，不纳入正式配对报告。

### Validation
- 续轮离线全量抽样验收：`outputs/leave/full_sampling_validation.json`，两次抽样一致；2,000 段配对、离开/继续各 2,000 点，全部 4,000 个 prompt 与截断真实前缀重建相同，API 请求 0。进程检查未发现运行中的 run_leave.py，且 outputs/leave/full 尚不存在；仍待 Claude 启动。
- `.venv/bin/python -m pytest -q`：最终 233 passed in 46.61s，退出 0；项目未声明独立 build/typecheck 命令。
- `.venv/bin/python scripts/run_leave.py --phase pilot`：配对 10 段 × 2 点 = 20 条成功。纯离开：离开点 4/10，95% CI [0.1,0.7]；继续点 0/10，CI [0,0]。混合均为 0。审核剔除 0；Spark 7,401 token（输入 7,109，输出 292）。
- 重跑 pilot 验证缓存续跑；不增加请求。曾发现复用判断缓存读取器会误解析生成文本，已改用生成专用缓存恢复，并新增缓存复用测试。
- `.venv/bin/python scripts/run_leave.py --phase report` 连续执行两次：report.md SHA-256 `58e40efd58e25ad7af3a77c1e1ebf8a84d196645cca0b59389d8eaa5d36a65bc`；summary.json SHA-256 `0ec4d65658d0323056462764c1ed565f53d3383a43539301d010c69ff35d0508`。
- `git diff --exit-code -- docs usersim/replay.py usersim/spark.py usersim/judge_spark.py`：退出 0。任务开始时 architecture.md 存在外部 ADR-019 修改，后续工作区已显示干净；Codex 未写这些文件。
- `.venv/bin/python -m compileall -q usersim/leave.py scripts/run_leave.py tests/test_leave.py`：退出 0。
- 凭证扫描：对新增代码、配置及 outputs/leave 所有文件扫描环境/.env 中真实 API_KEY/TOKEN/SECRET 值及长 Bearer 字面量，无命中。只读取 Spark 凭证用于调用；未调用付费模型。

### Success Conditions
- [x] 测试覆盖合同列出的泄漏、同构、判定、措辞及复现要求；全套测试 233 passed in 46.61s。
- [x] 20 条 pilot 完成；离线 report 可基于小批重建，小批两次 SHA-256 一致。
- [ ] Claude 启动并完成全量 4,000 点，再核对全量双重建 SHA-256。Codex 按合同未启动全量。
- [x] 小批 report.md 包含全量结束方分布、离开/混合率和区间、负面/结束语分组、审核剔除、Spark token、设计局限。
- [x] 禁改路径 git diff 检查退出 0；凭证扫描无命中。

### Risks & Deviations
- 全量未完成，任务整体尚不能宣告完成。Claude 在自己的 shell 中从项目根目录启动：
  ```sh
  .venv/bin/python scripts/run_leave.py --phase full --out outputs/leave/full
  ```
  中断后使用同一命令续跑。全量生成完成后脚本自动离线重建两次，并将两个 SHA-256 保存为 `outputs/leave/full/offline_acceptance.json`；不同则失败。人工复核可运行：
  ```sh
  .venv/bin/python scripts/run_leave.py --phase report --out outputs/leave/full
  .venv/bin/python scripts/run_leave.py --phase report --out outputs/leave/full
  ```
- 全量用户子集 9,910 段，其中客服结束 8,917、用户结束 993；全量含其他 QRole 共 10,701 段，其中客服结束 9,651、对方结束 1,050。8,917 个离开候选均有继续点，共 65,637 个继续位置；全量将固定种子抽 2,000 段，每段一离开一继续。
- 明确离开选项有利于模拟器；真实离开只是沉默，日志结束也可能是截断。负面及纯结束语为词汇规则，摘要沿用现有清洗/覆盖，未审核摘要可能残留结果。小批不能作为全量结论。
- 首次独立抽样小批耗用 7,680 Spark token，随后改为同ID配对并重跑；本任务两次小批合计 15,081 token。首批归档 summary 的 attempts/unknown_usage 错将 dispatch 计入，正式报告已修正为只计 response；归档请求原始记录未改。
- 不修改 docs。建议 Claude 在 docs 记录合同 D 的全量完成状态、实际结论与设计局限。
