### Changed Files
- usersim/spark.py — HTTP400/10013单次标为moderated；记录供应商error code/message并脱敏凭证；其他错误仍指数退避，耗尽停止；取消和未知用量明确标记。
- usersim/csds.py、usersim/replay.py — 修正摘要结果清洗与逐ID覆盖；历史和目标只删除中文字符间横向分词空格，保留英文/数字间空格及换行。
- config/summary_overrides.json、config/summary_reviews.json — 520段摘要逐条人工核对；9个覆盖目标，其余保留规则结果；原摘要、理由及规则/覆盖目标写入两组summary_audit.json。未调用模型API做摘要核对。
- usersim/metrics.py、usersim/report.py — 首轮编号披露替代8位数字指标；报告审核剔除数/比例/ID、未知用量、20段与500段描述比较及本版人工阅读观察。
- scripts/run_pilot.py — 新输入全部重跑，moderated两侧配对剔除，真实历史保持；保存prompt指纹、审核清单、逐请求用量。
- scripts/check_outputs.py — 校验20/500段、审核日志/计数、重试成功、配对行数、原文/指标、摘要审计及prompt指纹；CSV读取保留模型原始换行。
- scripts/compare_segmentation.py — 只读v1归档，按统一指标比较同一批20段模拟器均值，逐ID列出任务目标变化。
- tests/test_pipeline.py — 新增10013不重试、中文去空格/英文保留、三例摘要、编号披露与CSV换行回归；原测试保留。
- outputs/、outputs/extended/ — 完整9文件、原始生成、审核和摘要审计、逐请求日志及阅读补充。另有outputs/sensitivity_segmentation.md与3段小批outputs/smoke/。

### Validation
- `.venv/bin/python -m pytest -q` — 11 passed in 0.84s，退出码0。
- `.venv/bin/python -m compileall -q usersim scripts tests` — 通过；Python 3.12.13。本仓库无额外声明的类型检查或构建命令。
- `.venv/bin/python scripts/run_pilot.py --n 3 --seed 20261025 --out outputs/smoke --concurrency 4` — 正式大样本前完成，3段24轮、5884 token，无错误/空输出；check()小批核验通过。
- `.venv/bin/python scripts/run_pilot.py --n 20 --seed 20261025 --out outputs --concurrency 8` — 退出码0，201轮、59070 token、52.55秒，ID与v1相同。
- `.venv/bin/python scripts/run_pilot.py --n 500 --seed 20261026 --out outputs/extended --exclude outputs/sampled_ids.json --baseline outputs --concurrency 8` — 退出码0，4710计划轮次、4705成功、5审核拒绝；4711次HTTP尝试，1次ReadTimeout后重试成功；已知1337476 token、1116.24秒。
- `.venv/bin/python scripts/check_outputs.py outputs outputs/extended` — 退出码0：20/500段；有效配对201/4705轮；空输出均0；两组无交集。
- `.venv/bin/python scripts/compare_segmentation.py` — 201个共同有效轮次；20段逐ID目标比较，只有10049目标变化。
- `set -a; . ./.env; grep -rlF "$SPARK_API_KEY" outputs usersim scripts tests` — 无输出（grep退出码1表示未找到）。
- 临时小批副本注入429失败尝试、随后成功 — check()通过，生产输出未修改；真实扩大样本中的超时重试亦通过完整核验。
- 对归档、data/、docs/、misc/、AGENTS.md、CLAUDE.md和并行任务两份notes的55个文件做前后SHA256核对 — 无变化。未git commit。工作树docs/变更为本Delta开始前已有内容，本任务未写入。

### Success Conditions
- [x] 两目录9文件存在且非空；20段seed20261025与v1同ID，500段seed20261026，排除20段，交集0。
- [x] side_by_side行数=模拟器指标行数=非moderated用户轮次：201/4705；每个有效轮次真人/模拟器各一行；两组空输出均0%。
- [x] 10013不重试；真实3757:6–10均单次moderated，5/4710=0.1062%，配对两侧剔除；moderated清单、日志、用量汇总一致。
- [x] 请求记录输入/输出token、请求次数、耗时与全量推算；返回error code/message时保存；重试成功允许通过；未知用量明确说明。
- [x] 新增和原有测试均通过，覆盖防泄漏、连发合并、摘要结果删除、10013、空格、指定3例及编号口径。
- [x] 所有抽中摘要均人工式核对；2403/10197结果已删，10049诉求保留；审计与实际prompt指纹匹配。
- [x] 两份report.md逐条回答6问；500段报告含与20段比较。本版实际阅读例子和误判记录均已补入。
- [x] sensitivity_segmentation.md比较两版模拟器全部指标均值，20个ID逐条对照目标；10049变化明确标出，不作去空格的单因素因果结论。
- [x] 凭证扫描无输出；受保护路径未修改，无新增研究决定、其他模型或agent框架；结构化handoff已交付。

### Risks & Deviations
- 扩大样本为500段，替代原合同300段；输入修正后的全部重跑由Delta及ADR-015明确授权。
- 5次审核拒绝仅涉及3757，剔除可能有商品类别选择偏差；未改写真实数据绕过审核。
- 5个moderated请求及1个超时请求未返回token用量，成本为已知下界；不能将未知消耗当成零。未核验剩余额度或有效期。
- 分词敏感性比较还包含10049的目标修正和两次独立生成，不能单独归因于去空格。
- 字面关键词存在已记录误判（如“客户经理”“申请”“负责到底”），未更改已冻结关键词或选定正式研究指标。20段升级仅1个模拟器正例，报告第二行明确为0/0对照例。
- 初次完整核验发现CSV默认读取把单独CR转成LF；已保留原始换行并加入回归测试，未修改模型输出或重生成。
- 建议Claude在docs/进展记录中录入本次500段统计与成本下界；本任务未修改docs/。
