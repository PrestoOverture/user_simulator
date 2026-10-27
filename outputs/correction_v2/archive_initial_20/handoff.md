# P2 v2 handoff — 2026-10-27

**状态：20段小批完成且校验通过；因全量费用预测超出用户20美元上限，停在扩大前。500段正式比较未完成。**

## 已完成

- 实现并运行四组比较：复用已有baseline；新增situation（情境示例）、structure（轮次结构）、combined（二者结合）。没有根据效果调prompt或重跑成功样本。
- 使用原500段名单前20段，小批176个用户轮次；三组各176次Spark调用，共528条新回复，全部成功、无审核拒绝、无空输出。基线没有改写。
- 排除14个客服尚未发言的轮次；162轮具有真人及四组共同有效判断。810个逻辑判断输入，复用1条旧判断、4个输入跨来源完全相同，实际新增805次Gemini调用，全部成功，无重试或缺失usage。
- 500段共4710轮的检索/结构计划已冻结。独立train池65531轮，排除全部待测500段及原试跑20段。离线逐项核对16831个实际引用的独立训练轮次，来源及字段一致。
- Spark关闭thinking，默认temperature；Gemini沿用ADR-016已验证的冻结prompt，未更换模型。检索补齐、结构抽样、bootstrap种子20261027；原名单种子20261026。
- 未修改docs、未微调、未新增数据集、未调用其他付费模型、未启动其他研究分支。没有提交或推送git；交付文件在工作区，均为新增文件。

## 小批结果（不能代替500段正式结论）

按对话配对bootstrap 2000次；continue分母含unclear；未作多重比较校正。差距=模拟器−真人。

|指标|真人|baseline|situation|structure|combined|
|---|---:|---:|---:|---:|---:|
|continue比例|47.53%|67.90%|70.99%|58.02%|71.60%|
|显式负面情绪比例|6.79%|12.96%|14.81%|11.11%|13.58%|

- structure的continue绝对差距由20.37降至10.49个百分点，缩小9.88个百分点，95%区间 **[-0.65, 18.82]**；情绪差距缩小1.85个百分点，区间 **[-2.60, 5.44]**。两项改善区间均含零。
- situation与combined两项差距的点估计均未改善；其相对基线变化的区间也都含零。
- 结构指令为软约束：structure/combined的目标连发条数精确遵守率78.98%/73.86%；目标字数平均绝对误差9.94/12.44字。不能宣称严格控制成功。
- 小批为固定名单前20段，非重新抽出的代表性样本；本轮没有设“成功阈值”，不据此选赢家或修改既有核心结论。

## 停止证据与费用

- 小批Spark：216590 token，全部528次usage已返回；Gemini：805次，794655 token（输入597013，输出197642；输出包含推理），无未知usage。
- 网关累计费用起点7.006142美元，最终8.076172美元，本次新增 **1.07003美元**。小批闸门早一刻读到1.06983美元；最终账单多0.00020美元，为结算延迟，不是额外模型调用。
- 全量预算闸门预估：4300个基线有效且客服已回应的轮次；已知真人/基线尚缺8239个唯一判断输入，新三组尚需最多12414个，合计预计20653个。按小批实扣均价估算，预计本次总费用 **28.52美元（不加余量）/32.63美元（含15%余量）**。
- 新输出可能去重或被审核拒绝；预测保守假设新输入唯一且成功。没有用未知的潜在节省绕过预算闸门。
- `--phase full`以预期的非零退出码1停在**全量生成之前**，未启动剩余480段生成或判断。证据：`preflight_cost_projection.json`、`STOPPED.md`、`full.log`。未缩样、未换判断模型。

## 文件

- 实现：`usersim/correction_v2.py`、`scripts/run_correction_v2.py`。
- 校验与测试：`scripts/check_correction_v2.py`、`tests/test_correction_v2.py`。
- 本目录：`contract.md`、`inputs.json`、`plans.json`、`plan_audit.json`、`implementation_sha256.json`记录合同、输入、计划、代码指纹。
- 本目录`situation/`、`structure/`、`combined/`：逐轮生成及Spark实际请求用量；baseline仍读取`outputs/extended/`。
- `judge_inputs_20.json`、`judgments.jsonl`、`reused_judgments.json`：冻结判断输入、实际请求响应、复用来源。
- `report_20.md`、`summary_20.json`、`paired_judgments_20.json`、`examples_20.md`：四组报告、统计、逐轮判断、按固定规则选取的案例。
- `generation_audit_20.json`、`smoke_check.json`、`verification.json`、`rebuild_check.json`、`tests.log`：质量门槛与离线验证。
- `budget.json`、`preflight_cost_projection.json`、`smoke.log`、`full.log`、`STOPPED.md`：用量、账单及停止证据。

## 验证命令及输出

均从项目根目录执行；以下验证不调用模型API。

```sh
.venv/bin/python -m pytest -q
# 86 passed in 32.79s

.venv/bin/python scripts/run_correction_v2.py --phase plan
# Frozen plan and strict-prefix audit passed; no API requests.

.venv/bin/python scripts/run_correction_v2.py --phase report --report-n 20
# 返回162配对轮次的统计JSON；完整输出见report_rebuild.log。

.venv/bin/python scripts/check_correction_v2.py --n 20
# verified=true, n=20, independent_source_turns_checked=16831,
# paired_turns=162, spent_usd=1.07003, api_calls=0；凭证扫描通过。

.venv/bin/python -m py_compile scripts/run_correction_v2.py scripts/check_correction_v2.py usersim/correction_v2.py
git diff --exit-code -- docs
git diff --check
# 以上均退出0，无错误输出。
```

另外连续两次离线重建，报告、统计、逐轮判断、案例、生成审计5个文件SHA-256完全一致，API调用0次；证据见`rebuild_check.json`。

## 未完成与需决定

- 剩余480段的三组生成、500段正式判断和完整统计均未做，不能交付500段结论。
- 需要用户/Claude决定：提高Gemini预算，或另立合同调整规模/评估设计。**不要直接重跑full期待绕过当前20美元闸门**；当前代码会再次按20美元上限停止。
- 若扩大，继续使用已冻结的同一500段名单、同一检索计划及已有成功缓存；不要重抽样或根据小批结果更换实验臂。
- 检索为字面TF-IDF相似，不是语义嵌入；结构控制来自独立池的无条件联合分布。方法本身的取舍与进一步修正交由Claude/用户，不在本合同内另行探索。

## 建议由Claude更新docs

- history：追加本次实现、小批结果、实扣费用和预算停止证据。
- roadmap：记录用户已确认P2 v2；当前完成小批，扩大受预算限制，500段未完成。
- tech_stacks：累计网关费用更新为8.076172美元左右。
- architecture：如采纳本实现，补记情境检索、示例隔离、结构抽样和配对评估的实际实现；不因20段结果改动ADR-016的核心结论。

本次没有直接修改docs。
