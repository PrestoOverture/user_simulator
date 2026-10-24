# 历史记录

## 用途

按时间记录项目做了什么、结果如何。只追加，不改旧记录。

- 计划和决策点见 [roadmap.md](roadmap.md)。
- 方法决策见 [architecture.md](architecture.md)。新的重要决定写成 ADR 放在那里，这里只记一句并链接。

## 记录格式

```
### YYYY-MM-DD 标题
- 做了什么：
- 结果：
- 下一步：
```

最新记录放在最下面。

## 记录

### 2026-10-25 初始化 docs/
- 做了什么：读完 `misc/` 下 5 个输入文件，写出 docs/ 下 5 个文件（prd、tech_stacks、roadmap、history、architecture）。
  - [项目总览-用户模拟器真实性.md](../misc/项目总览-用户模拟器真实性.md)（主文件）
  - [任务规格-试跑.md](../misc/任务规格-试跑.md)
  - [research-proposal-user-simulator-fidelity_1.md](../misc/research-proposal-user-simulator-fidelity_1.md)
  - [LLM用户模拟器蓝海场景研究.md](../misc/LLM用户模拟器蓝海场景研究.md)（Gemini 报告）
  - [handoff-ai-review-twin_1.md](../misc/handoff-ai-review-twin_1.md)（已放弃方向）
- 结果：只写文档。未写代码，未下载数据，未调用 API。待定项和未核实项已在各文档中标注；文件间冲突写在各文档末尾。
- 下一步：等你确认文档后，进入 P0 试跑（见 [roadmap.md](roadmap.md)）。

### 2026-10-25 去重 docs/；写 CLAUDE.md 和 AGENTS.md
- 做了什么：
  - 去掉 docs/ 内重复内容：标注约定只在 prd.md 定义；判断模型约束、试跑只用 Spark、摘要删结果、"转人工"替代行为只在 architecture.md 的 ADR 中写；对比模型候选只在 tech_stacks.md；成功标准只在 prd.md；每条冲突只在一个文件末尾记录。
  - 新增 [CLAUDE.md](../CLAUDE.md)（Claude 指挥、审查、维护 docs）和 [AGENTS.md](../AGENTS.md)（Codex 写代码），两者通过 `/codex:c2c-workflow` 协作。
- 结果：只改文档。未写代码，未下载数据，未调用 API。
- 下一步：等你确认后进入 P0 试跑。

### 2026-10-25 Spark 接入、建仓库、P0 试跑首版
- 做了什么：
  - 对照官方文档确认 Spark 接口，实测关闭思考的参数（ADR-012）。建私有 GitHub 仓库 `PrestoOverture/user_simulator`。
  - CSDS 从 Google Drive 下载成功。MobileCS、ECD、JDDC 的获取情况已核查（见 [tech_stacks.md](tech_stacks.md) 第 4 节）。
  - Codex 首版试跑：20 段完成；300 段在 1,791/2,816 轮时被内容审核（错误码 10013）拒绝，按停止条件停下。
- 结果：Claude 定了 ADR-013（审核拒绝的轮次剔除）、ADR-014（去分词空格）、ADR-015（修正摘要清洗后重跑）。首版输出存档于 `outputs/archive/v1_segmented/`。
- 下一步：按新设置从头重跑。

### 2026-10-25 P0 试跑重跑完成（20 + 500 段）
- 做了什么：Codex 返工后从头重跑。Claude 复核：pytest 11 项通过，`check_outputs.py` 两套均通过，凭证扫描无泄漏。
- 结果：20 段 201 轮；500 段 4,705 个配对轮次，5 轮被审核拒绝。500 段的主要差距（模拟器 vs 真人，审查前的数字）：短句率 3.0% vs 35.0%；首轮字数 39.0 vs 15.5；首轮编号披露 0.8% vs 24.2%；礼貌词 28.5% vs 17.3%；连发条数 1.11 vs 1.59；升级要求两侧都约 0.1–0.3%。"催促"模拟器 16% vs 真人 3%，疑似关键词误判，待审查。
- 下一步：独立审查（关键词误判、摘要、泄漏）→ 修正方法探索性预跑 → D1。

### 2026-10-25 独立审查、修正方法预跑、D1 材料
- 做了什么：
  - Codex 独立审查（`outputs/review.md`）：泄漏审计 4,911 轮通过；逐条核对 520 段摘要，修正 3 段并重跑 37 轮；修掉关键词误判（"客户经理"算升级、"什么时候"算催促等）后重算。
  - 修正方法探索性预跑（`outputs/correction_pilot/`）：在同一批 500 段上比较基线、C1（5 条随机真实原话）、C2（5 条同 intent 原话）。
  - D1 证据材料（`outputs/d1_brief.md`）：每个指标的真人出现率、差距和 95% bootstrap 区间、关键词误判/漏判、20 vs 500 段方向。
  - Claude 复核：pytest 19 项通过；三套输出校验通过；D1 材料可重复生成；凭证扫描无泄漏。
- 结果（500 段，模拟器 vs 真人）：
  - 表达层差距大且区间不含零：短句率 3.0% vs 35.0%；首轮字数 38.9 vs 15.5；首轮编号披露 1.0% vs 24.2%；连发条数 1.11 vs 1.59；礼貌词 25.9% vs 17.1%。
  - 决策层在真人数据里几乎测不到：升级要求真人 0/500 段；拒绝方案 1.0%，差距区间含零；自我更正 2/4,705 轮。
  - 与预想方向相反：催促（6.6% vs 1.4%）和重复追问（9.3% vs 5.1%）模拟器都多于真人。
  - 修正预跑：原话示例把字数、首轮字数差距缩小约 30–40%，礼貌词差距缩小约 55%；短句率、连发条数、首轮编号披露几乎不变；C2 不比 C1 好。
- 下一步：停在 D1，等用户选核心指标。另有待用户决定：是否申请 MobileCS 全量。
