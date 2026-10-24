### Changed Files
- requirements.txt — Python 3.12 虚拟环境依赖 httpx、pytest。
- usersim/{csds,replay,metrics,report,spark}.py — 分离数据适配、严格前缀重放、规则指标、报告和Spark请求；并发与指数退避，逐请求用量。
- config/keywords.json — 暂定关键词与更正规则。
- scripts/{spark_check,run_pilot,check_outputs}.py — 连通性实测、可复用入口和基于原数据的输出核验。
- tests/test_pipeline.py — 6项测试覆盖历史不泄漏、连发合并、摘要结果删除、指标、请求参数、重试及耗尽停止。
- outputs/ — 20段标准9文件、原始生成、逐请求日志、摘要审计及人工抽查；种子20261025，201用户轮次。
- outputs/extended/ — 300段抽样与中途结果；种子20261026，计划2816轮，保存1791轮。STOPPED.md记录停止证据。缺少最终对照表、指标、用量汇总和报告。

### Validation
- `.venv/bin/python -m pytest -q` — 6 passed in 0.14s，退出码0。
- `.venv/bin/python -m compileall -q usersim scripts tests` — 无输出，成功。本仓库没有另外声明的类型检查或构建命令。
- `.venv/bin/python scripts/check_outputs.py outputs outputs/extended` — 退出码1；20段通过（201轮，空输出0）；300段缺少side_by_side.csv。
- `set -a; . ./.env; grep -rlF "$SPARK_API_KEY" outputs usersim scripts tests` — 无输出，grep退出码1表示未找到泄漏。
- `.venv/bin/python scripts/spark_check.py` — 退出码0；并发1/2/4/8共29次均HTTP200。
- `.venv/bin/python scripts/run_pilot.py --n 20 --seed 20261025 --out outputs` — 退出码0；201次生成，输入76814、输出3608，总80422 token，91.77秒。
- `.venv/bin/python scripts/run_pilot.py --n 300 --seed 20261026 --out outputs/extended --exclude outputs/sampled_ids.json --baseline outputs --concurrency 8` — 退出码1；3757:6连续4次HTTP400，触发合同停止。未继续调用。
- 样本复核 — ID分别20/300，交集0；用户轮数范围分别6–20、6–26。
- 未修改docs/、misc/、CLAUDE.md、AGENTS.md或并行任务指定路径，未git commit。

### Success Conditions
- [x] pytest通过，包含合同指定的三项测试，另测试Spark参数与停止行为。
- [ ] 两目录输出核验退出码0 — 20段通过；300段因API停止尚未完成，核验正确返回非0。
- [x] 凭证扫描无输出。
- [ ] 两份report.md逐条回答6问，且扩大样本报告对比两组 — 20段报告已含6问及20个命中单元人工抽查；扩大样本报告尚未生成。
- [ ] 完整Goal Contract — API重试耗尽且发现摘要漏删，未宣称完成。

### Risks & Deviations
- 必须由Claude/用户决定Spark恢复后的下一步；未自动重试整批或换模型。HTTP400正文未记录，原因未知。
- 2403摘要含客服提出方案后的“可以接收发票复印件”，10197含“不再退货”，当前删除规则漏删；531的“用户表示接受检测结果”已在扩大试跑前修正并删除。
- 20段中的10049“最后请客服帮助取消订单”被规则过删。未选择性重生成；已经请求用户决定是否允许修正规则并废弃、重跑受影响轮次，这将偏离“每轮只生成一次”。
- 4条取消记录没有终态status或供应商token；失败/取消请求的0不是已证实零消耗，扩大样本用量只有已知下界673627 token。
- 当前check_outputs额外要求没有失败尝试，严于合同允许重试成功的情形；本次确已耗尽重试，必须停下，未以改校验器绕过。
- 排序明确使用绝对相对差距，以便同时展示字数、条数和比例；稀疏规则的相对差距可能很大，未据此决定核心研究指标。
- 尚未核验账户剩余额度和有效期；短批次未测出限速上界。20段线性外推全CSDS约3514万token，只是体量估算。
- 建议Claude后续更新docs/中的CSDS实际角色/分词/脱敏情况、失败停止证据和摘要清洗决定；本任务未直接修改docs/。
