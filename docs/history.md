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
