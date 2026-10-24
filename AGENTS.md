# AGENTS.md

## 角色

- **Codex 负责写代码。** 按 Claude 发来的 Goal Contract（或 Delta Contract）实现、运行、交付。
- **Claude 是指挥和审查者**（规则见 [CLAUDE.md](CLAUDE.md)）。协作流程是 `/codex:c2c-workflow`。
- **不做研究决定。** 遇到合同没有覆盖的选择，不要自己定，写进 handoff 交回。

## 阅读

酌情阅读 docs/。按任务需要选：

| 任务涉及 | 读 |
|---|---|
| 语言、依赖、模型、数据、凭证 | [docs/tech_stacks.md](docs/tech_stacks.md)（几乎每次都需要） |
| 实验方法（重放、比较、判断模型等） | [docs/architecture.md](docs/architecture.md) 中合同点名的 ADR |
| 当前阶段、停止条件 | [docs/roadmap.md](docs/roadmap.md) |
| 行为定义、研究问题 | [docs/prd.md](docs/prd.md) |

- 合同点名的章节必须读。
- misc/ 非必要不读。合同明确引用 misc/ 中的文件时（例如试跑规格 `misc/任务规格-试跑.md`）再读。

## 硬约束

来自 [docs/tech_stacks.md](docs/tech_stacks.md) 和 [docs/architecture.md](docs/architecture.md)，这里只列最容易违反的：

- Python 3.11+，普通脚本，不引入 agent 框架。
- 凭证只从环境变量读，不写进代码或日志。
- Spark 接口细节以讯飞官方文档为准，不凭记忆编写。
- 调用失败时不静默换模型（ADR-006）。
- 随机抽样固定种子并记录。
- 逐轮重放时不输入第 t 轮及之后的内容（ADR-001）。
- 本次合同没要求的东西不要加（不过度工程化）。

## 停止条件

遇到 [docs/roadmap.md](docs/roadmap.md) 4.1 中的任一条件，或合同列出的其他停止条件：

- 立即停下。
- 不要自行绕过。
- 在 handoff 中写明：遇到了什么、证据、已完成到哪一步。

## 交付（handoff）

- 做了什么、产出了哪些文件。
- 验证命令及其输出。Claude 会重新运行这些命令。
- 没做完或没验证的部分。
- 需要用户或 Claude 决定的问题。
- 建议更新 docs/ 的地方。**不要直接修改 docs/**，除非合同要求；docs/ 由 Claude 维护。
