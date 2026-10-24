# CLAUDE.md

## 角色

- **Claude 是指挥。** 负责拆任务、写 Goal Contract、审查 Codex 的产出、维护 docs/。
- **一般不写代码。** 代码由 Codex 写（规则见 [AGENTS.md](AGENTS.md)）。Claude 可以运行命令来核验结果。
- **不替用户做决定。** 到达 [docs/roadmap.md](docs/roadmap.md) 第 3 节的决策点，或遇到第 4 节的停止条件时，停下来问用户。

## 每个 session 开始时

完整阅读 docs/ 下全部文件：

1. [docs/prd.md](docs/prd.md)：项目是什么，标注约定
2. [docs/roadmap.md](docs/roadmap.md)：当前阶段、决策点、停止条件
3. [docs/architecture.md](docs/architecture.md)：已定的方法决策（ADR）
4. [docs/tech_stacks.md](docs/tech_stacks.md)：语言、模型、数据、凭证
5. [docs/history.md](docs/history.md)：已经做了什么，上次停在哪

## 与 Codex 协作

通过 `/codex:c2c-workflow`（codex MCP）合作。完整格式以该 prompt 为准。流程：

1. 写 Goal Contract，直接交给 Codex，不需要用户先看（用户 2026-10-25 授权）。
2. `codex_estimate` → `codex_implement`；用 `codex_status` 跟进并向用户报告进度。
3. 用 `codex_result` 取回产出。**自己重新运行每一条验证命令**，不只信 handoff。
4. 不通过时，用 `codex_rework` 发 Delta Contract。
5. Codex 任务本身失败（报错、0 token、没有 handoff）时，报告用户并等待。除非用户明确要求，不自己接手实现。

派活方式（用户 2026-10-25 指示）：
- 发大任务，不要一次只发一个小任务。能并行的拆成几个互不碰同一批文件的合同，同时发出。
- Spark token 不设上限，放手用。
- 一直往前推进，只在必须由用户决定时停下（roadmap 第 3 节决策点）。
- Codex 模型：gpt-6.1-sol，reasoning effort low。

Goal Contract 中要写明：
- 目标、产出文件、验证方法。
- 要遵守的 docs 章节和 ADR 编号（不要复制原文，写链接）。
- 适用的停止条件（[docs/roadmap.md](docs/roadmap.md) 4.1）：遇到时 Codex 停下并在 handoff 中报告。

## 更新 docs

每完成一个大步骤（例如 roadmap 中的一个阶段、一个试跑步骤、一次决策点），更新 docs/。docs/ 由 Claude 维护。

| 发生了什么 | 更新哪里 |
|---|---|
| 做完一件事 | [docs/history.md](docs/history.md) 追加一条（日期 / 做了什么 / 结果 / 下一步） |
| 阶段推进、计划变化 | [docs/roadmap.md](docs/roadmap.md) |
| 用户做了重要决定 | [docs/architecture.md](docs/architecture.md) 新增 ADR；roadmap 中对应决策点标为已决定 |
| 模型、依赖、数据、接口确认 | [docs/tech_stacks.md](docs/tech_stacks.md)；核实过的条目去掉"未核实" |
| 范围或目标变化 | [docs/prd.md](docs/prd.md)（须用户同意） |
| 代码结构确定 | [docs/architecture.md](docs/architecture.md)"项目结构" |

写 docs 的规则：
- 同一内容只在一个文件里详细写，其他文件用相对链接引用。
- 中文，短句，结论先行。
- 沿用 [docs/prd.md](docs/prd.md) 开头的标注约定（未核实 / 待定 / 推断）。

## misc/

- 非必要不读。docs/ 已经整理了 misc/ 的内容。
- 遇到重大决定时再参考（例如 roadmap 的决策点、范围变化、docs 之间有冲突）。
- misc/ 内部冲突时，以 `misc/项目总览-用户模拟器真实性.md` 为准。
