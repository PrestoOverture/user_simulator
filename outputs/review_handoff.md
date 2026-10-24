### Changed Files
- scripts/audit_leakage.py — 独立从原始CSDS消息边界重建并检查4911个prompt；污染或指纹不一致非0退出（本合同前一轮已写入）。
- config/summary_overrides.json、config/summary_reviews.json — 修正1538、3278、9150的任务目标及理由，其余517条保留。
- config/keywords.json、usersim/metrics.py — 修复申请、客户经理、普通询时、技术不行、宽泛自我更正等明确误判，保留逐项理由。
- usersim/spark.py — 移除从.env直接读凭证的回退，只读环境变量。
- scripts/run_pilot.py — 增加无API调用的--refresh-only；核验缓存prompt后重建指标/报告并保留原运行耗时。
- scripts/rerun_review_changes.py — 只重跑输入变化的37轮，保留被替换的生成和请求费用。
- scripts/sample_keyword_review.py — 固定种子按每个指标/来源/命中状态抽样，拒绝覆盖已冻结标注。
- scripts/audit_review_evidence.py — 独立核对9812条指标、520原始摘要、680条抽样可复现性、37轮替换与累计成本。
- scripts/write_review.py — 从冻结证据、逐条判断和当前统计重建review.md及前后对比表。
- usersim/report.py、outputs/review_notes.md、outputs/extended/review_notes.md — 两套报告使用审查后的规则说明，移除作为当前数字引用的旧抽查。
- tests/test_pipeline.py — 增加污染CLI失败、关键词反例/正例、3条摘要保留诉求、凭证仅环境变量测试；验证thinking关闭且无max_tokens。
- outputs/metrics_by_turn.csv、outputs/metrics_summary.md、outputs/report.md — 20段修正规则后重算，模型输出保持。
- outputs/extended/generations.jsonl、side_by_side.csv、request_usage.jsonl、summary_audit.json、token_usage.json、metrics_by_turn.csv、metrics_summary.md、report.md — 500段替换37轮输出，重建一致的摘要/请求/指标/成本/报告。
- outputs/review.md、outputs/review_evidence/ — 五项审查结论、520条摘要记录、680条逐条判断、修复前基线、旧37条生成/费用及新37次请求证据。
- outputs/review_handoff.md — 本次结构化交接。

### Validation
- `.venv/bin/python scripts/audit_leakage.py outputs outputs/extended` — 退出0：201 + 4710 = 4911轮（含moderated），严格真实前缀通过。
- `.venv/bin/python -m pytest -q` — 退出0：16 passed in 3.88s；含未来客服/模拟器污染拒绝与CLI非0测试。
- `.venv/bin/python scripts/check_outputs.py outputs outputs/extended` — 退出0：20/500段，有效配对201/4705轮，空输出0。
- `.venv/bin/python scripts/audit_review_evidence.py` — 退出0：9812指标行、520原始摘要、680条可复现且已标注样本，累计费用一致。
- `.venv/bin/python -m compileall -q usersim scripts tests` — 退出0；仓库无额外声明的build/typecheck。
- 环境载入后 `.venv/bin/python scripts/rerun_review_changes.py outputs outputs/extended` — 退出0：仅37个过期prompt重跑；37次成功，新增已知14302 token，18.0704秒，无max_tokens。
- `.venv/bin/python scripts/run_pilot.py --n 20 --seed 20261025 --out outputs --refresh-only` — 退出0；201轮缓存重建，无API。
- `.venv/bin/python scripts/run_pilot.py --n 500 --seed 20261026 --out outputs/extended --exclude outputs/sampled_ids.json --baseline outputs --refresh-only` — 退出0；4710轮缓存核对并重算，5轮审核剔除不变，无API。
- `.venv/bin/python scripts/write_review.py` — 退出0；报告与冻结样本/最终均值一致。
- 凭证仅在内存做字节扫描，outputs/（排除archive）、usersim/、scripts/、config/、tests/零匹配；未输出密钥。
- `git diff --name-only -- docs misc CLAUDE.md AGENTS.md data outputs/archive outputs/data_format_notes_mobilecs.md outputs/data_format_notes_ecd.md` — 无输出；未git commit。

### Success Conditions
- [x] 泄漏命令退出0并打印≥4906轮：实际4911；未来客服内容、模拟器历史及篡改指纹被测试拒绝，CLI非0。
- [x] 修复后pytest与check_outputs命令均退出0：16项测试通过，201/4705有效配对一致。
- [x] outputs/review.md包含五项方法、结果、修复、未修问题/原因；五个关键词指标真人与模拟器分别的误判率、全部抽中漏判例、明确抽样分母及修复清单；20/500段全部指标前后对比表。
- [x] 520条摘要逐条核对，3条目标修正后37轮已重跑；680条固定种子关键词样本逐条判断，未用模型API作判断；两套指标与报告已重算。
- [x] Changed Files / Validation / Success Conditions / Risks & Deviations结构化handoff已交付。

### Risks & Deviations
- 关键词仍有上下文误判和漏判，尤其拒绝/自我更正；修正前分层样本复用评估新规则，不是独立泛化测试。所有剩余样例及原因见review.md与keyword_samples.json。
- 首轮编号仍为既有占位符/连续6位数字代理，非有效订单号；重复追问仍为字符相似代理，未自行调整正式研究定义。
- 5个moderated和1个超时请求的token未知；当前两套已知1396407 token，加回被替换的14441，累计已知1410848 token。旧请求费用未丢弃，全部成本均为已知下界。
- 历史HTTP请求体未独立保存，泄漏检查为原始前缀重建及记录指纹核对；thinking检查为请求代码/捕获JSON测试及参数记录，未宣称可核查供应商内部执行。
- 全量520条原始摘要和目标逐条阅读；对21段歧义对话回看原始消息，未将其描述为全量对话双人审读。
- 未修改受保护路径，未新增模型/框架，未git commit。建议Claude记录审查后的指标和成本；正式指标选择仍由Claude/用户决定。
