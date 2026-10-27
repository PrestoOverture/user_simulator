# P2 v2 最终 handoff — 2026-10-27

**已完成：20段小批 → 500段四组对比、全部判断、统计、测试和离线审计。** 没有扩展实验、微调或修改docs。

## 结论

低成本指令能明显减少显式负面情绪表达，但对“继续提出要求”的修正有限；两种方法组合没有改善continue。小批中的结构控制优势没有在全量中保持同样幅度，正式结论以500段为准。

|指标|条件|真人比例|模拟器比例|差距/百分点|相对基线的绝对差距缩小及95%区间/百分点|
|---|---|---:|---:|---:|---|
|continue|baseline|47.81%|74.09%|+26.28|+0.00 [0.00, 0.00]|
|continue|situation|47.81%|71.28%|+23.47|+2.81 [1.23, 4.38]|
|continue|structure|47.81%|72.14%|+24.33|+1.95 [0.38, 3.45]|
|continue|combined|47.81%|74.77%|+26.95|-0.67 [-2.30, 0.92]|
|emotion|baseline|10.88%|15.63%|+4.74|+0.00 [0.00, 0.00]|
|emotion|situation|10.88%|11.88%|+1.00|+3.74 [2.47, 4.90]|
|emotion|structure|10.88%|12.53%|+1.65|+3.09 [1.86, 4.30]|
|emotion|combined|10.88%|11.12%|+0.23|+4.51 [2.44, 5.57]|

- continue：情境示例将差距由26.28降至23.47个百分点，约缩小10.7%；结构控制约缩小7.4%。组合组差距26.95个百分点，未改善。
- 情绪表达：情境示例、结构控制、组合分别将差距降至1.00、1.65、0.23个百分点；这是显式表达指标，不能据此宣称整体反应方式已修复。
- 按对话配对bootstrap 2000次，种子20261027；continue分母含unclear。所有比较均报告，未设成功阈值、未作多重比较校正，属探索性结果。

## 实验与数据完整性

- 原500段名单与baseline读取outputs/extended/，未改写；先跑同一名单前20段，之后复用成功缓存扩大到500段，没有按效果重抽样或重跑。
- 三个新条件各4710轮：situation=仅情境示例，structure=仅轮次结构，combined=二者结合。合计14130次Spark请求，14119次HTTP成功（其中1条空开场）、11轮审核拒绝，无请求重试。
- 与真人及四组共同有效4300轮，覆盖全部500段；405轮没有客服前文、5轮至少一组被审核拒绝，按既定口径排除。
- train示例池65531轮，排除全部待测500段及原试跑20段；按上一条客服发言的中文字符/二元组TF-IDF余弦取5例，固定种子补齐。结构从独立池联合抽样字符数与连发条数，structure/combined共享抽样。
- 已逐项核对16831个被引用训练轮次的来源、文本、客服前文、字符数与消息数；未来回复/后续消息变化不影响计划和prompt的测试通过。全部生成prompt指纹通过。
- Spark X2.6关闭thinking；Gemini使用ADR-016已验证且哈希冻结的prompt。来源标签不进入判断请求。
- 21500个逻辑判断输入，对应21392个唯一输入；21356个唯一输入新调用成功，36个旧判断指纹与原始来源记录核对后复用，108个逻辑输入因完整输入相同而去重。重复成功请求0。
- 全量结构指令仍是软约束：structure/combined目标连发条数精确遵守率78.45%/70.72%，目标字数平均绝对误差9.71/11.33字。

## 中途问题与处理

1. 原20美元预算预测不足，按合同停下；用户随后追加充值50美元并明确授权继续。本次保守采用总新增费用上限50美元，未重置原支出起点。授权记录在budget.json，初次停止证据在archive_initial_20/。
2. structure/4293:1首次HTTP200返回空文本（117输入、1输出token）。它start=0、无客服前文，按既定口径本来就不参加判断。修复了对所有轮次一律判空即停的过宽校验；保留空输出、不重跑、不增加排除规则。正式判断轮次为空仍停止，测试覆盖两种情况。证据在empty_opening_fix.md和archive_empty_opening_stop/。
3. Gemini共18次网关错误（17次504、1次502），按已有客户端的重试规则恢复；所有输入最终成功，无解析失败、无重复成功生成/判断。保留完整请求记录，不换模型。

## 用量与费用

- 网关累计费用起点7.006142美元，当前27.126994美元，本次新增 **20.120852美元（约20.12美元）**，低于用户修订后的50美元上限。包含原20段小批；从追加授权时记录的1.070030美元继续，恢复后的增量约19.050822美元。
- Gemini实际21374次请求：21356次成功、18次5xx重试。已返回18825663 token（输入16079881、输出2745782，含推理）；18个错误响应没有usage，未将其当作已知0用量，费用以网关实扣覆盖。
- Spark已知5915798 token；11次内容审核拒绝没有usage，单独记录。仅Spark主力模型和Gemini判断模型被调用，没有新增DeepSeek或其他付费模型。
- 初次保守预测32.63美元高于最终实扣；正式阶段每次判断均价低于小批。所有预测与账单快照保留，不回改历史。

## 代码和产物

- usersim/correction_v2.py：独立示例池、情境检索、联合结构抽样、三组prompt。
- scripts/run_correction_v2.py：冻结输入、20→500运行、缓存、预算检查、最多8个流水并发判断、统计报告。
- scripts/check_correction_v2.py：离线来源、生成、比例、预算及凭证扫描。
- tests/test_correction_v2.py：未来信息隔离、池隔离、结构独立抽样、预算、并发、停止规则、空开场处理。
- 本目录report_500.md、summary_500.json、paired_judgments_500.json、examples_500.md：最终报告、统计、逐轮判断与固定规则案例。
- inputs.json、plans.json、plan_audit.json、implementation_sha256.json：输入、计划与代码指纹；situation/、structure/、combined/：生成及实际用量。
- judge_inputs_500.json、judgments.jsonl、reused_judgments.json：冻结输入、请求响应及旧缓存来源。
- completion_audit.json、verification_500.json、rebuild_check_500.json、generation_audit_500.json、tests.log、final_check.json：完成审计和验证。
- budget.json、contract.md、full_resume2.log及两个archive目录：授权、账单、完成日志与旧停止证据。20段产物保留作历史，不替代500段结果。

## 验证命令及输出

从项目根目录执行；下列命令均不调用模型API。报告和校验可能重写同内容的离线产物。

```sh
.venv/bin/python -m pytest -q
# 90 passed in 30.63s

.venv/bin/python scripts/run_correction_v2.py --phase plan
# Frozen plan and strict-prefix audit passed; no API requests.

.venv/bin/python scripts/run_correction_v2.py --phase report --report-n 500
# 返回500段、4300配对轮次的统计JSON，完整输出见report_rebuild_500.log。

.venv/bin/python scripts/check_correction_v2.py --n 500
# verified=true, n=500, independent_source_turns_checked=16831,
# paired_turns=4300, api_calls=0；凭证扫描通过。

.venv/bin/python -m py_compile scripts/run_correction_v2.py scripts/check_correction_v2.py usersim/correction_v2.py
git diff --exit-code -- docs
git diff --check
# 均退出0，无错误输出。
```

- 固定输入与账单快照下连续两次离线重建，报告、统计、逐轮判断、案例、生成审计5个文件SHA-256完全一致；rebuild_check_500.json记录api_calls=0。
- completion_audit.json核对全部判断指纹、原始缓存来源、解析结果、最终预测覆盖、无重复成功请求、代码指纹和500段范围。
- 最终没有在跑的实验任务。仅新增工作区文件，没有git提交或推送。

## 未完成、限制与待决定

- 本合同无未完成的实验或必需验证。未扩展到其他模型、数据集、自由对话、微调或发表；它们不属于此次合同。
- 结果为Spark上单次生成、字面相似检索与软结构指令的探索性测量；不能外推所有模拟器，也不测真人直接离开。判断器标准集由单一GPT系标注者标注。
- 是否继续修正方法、如何设成功阈值、是否做跨模型复核，由用户/Claude决定；没有据此自行增加实验。

## 建议由Claude更新docs

- history：追加本轮完整实验、预算修订、故障修复及最终结果。
- roadmap：记录P2 v2四组500段已完成；继续要求差距只小幅缩小，情绪表达差距明显缩小，两方法组合非加性。
- tech_stacks：更新网关累计实扣约27.13美元；账单精确快照见budget.json。
- architecture：如采纳本实现，记录情境检索、结构抽样、隔离、预算与空开场处理；不因本实验改写ADR-016的既有测量结论。

本次未直接修改docs。
