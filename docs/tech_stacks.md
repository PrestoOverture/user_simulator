# 技术栈

> 来源：`misc/` 下 5 个输入文件。语言与依赖以 [任务规格](../misc/任务规格-试跑.md) 为准，其余冲突以 [项目总览](../misc/项目总览-用户模拟器真实性.md) 为准。
> 标注约定见 [prd.md](prd.md) 开头。项目目标见 [prd.md](prd.md)；方法决策见 [architecture.md](architecture.md)；计划见 [roadmap.md](roadmap.md)。

## 1. 语言与依赖

- **语言：** Python 3.11+。
- **形式：** 普通脚本。不引入 agent 框架。
- **不过度工程化：** 试跑目标是看清数据、跑通管线。
- **随机性：** 所有随机抽样固定随机种子，并记录在输出中。
- **关键词列表：** 放在单独的配置文件中，方便修改。
- **已知用到的标准库：** `difflib`（重复追问的字符相似度，任务规格第 4 步）。
- **第三方依赖：** **待定**。输入文件未列出。试跑时按需决定。

## 2. 模型

模型分工的决定见 [architecture.md](architecture.md) ADR-003、ADR-006。

| 角色 | 模型 | 状态 |
|---|---|---|
| 模拟器（主力） | 讯飞星辰 Spark 2.6 | 已定。10 亿 token 免费额度；有效期、速率限制、研究用途许可待第 0 步确认 |
| 对比模型（一两个低价模型，小样本） | **待定**（[roadmap.md](roadmap.md) D3） | Gemini 报告提到的候选（**未核实**）：DeepSeek-V3.2、Qwen3.5、GPT-4o-mini |
| 参照模型 | MIMESIS-4B / 9B（Meta 开源） | **待定**（[roadmap.md](roadmap.md) D4） |
| 判断模型 | **待定**（[roadmap.md](roadmap.md) D5） | 输入文件未给出候选 |

**调用参数：**
- temperature 使用官方默认值，并记录。
- 试跑 token 上限 200 万。接近上限时的处理见 [roadmap.md](roadmap.md) 4.1。

**MIMESIS 的 Gemini 描述（全部未核实）：** arXiv 2610.09484；权重在 Hugging Face `phanviethoang1512/MIMESIS-9B` 与 `MIMESIS-4B`；基于 Qwen3.5-9B / 4B 微调；Apache-2.0 许可；代码 `github.com/VietHoang1512/mimesis`。

## 3. Spark 接口

- 地址、鉴权方式、参数名：**待第 0 步确认**。以讯飞官方文档为准，不凭记忆编写。
- 第 0 步结果写入 `outputs/spark_check.md`。

## 4. 数据源

所有链接和描述来自 Gemini 报告，**均未核实**。执行时以实际下载到的数据为准。

| 名称 | 内容 | 获取方式 | 用途 | 核实状态 |
|---|---|---|---|---|
| CSDS | 京东售后客服真实对话，带人工标注的用户诉求摘要 | GitHub `xiaolinAndy/CSDS`，README 中有百度网盘和 Google Drive 链接 | 第一阶段主数据 | 未核实（试跑第 1 步核实） |
| MobileCS | 中国移动客服通话转录，含 Turnback（改口）标注 | GitHub `SereTOD/SereTOD2022` | 第一阶段辅助 | 未核实 |
| ECD | 淘宝客服对话，约 100 万对 | GitHub `cooelf/DeepUtteranceAggregation` | 第一阶段辅助 | 未核实 |
| JDDC | 京东客服对话，100 万+ | 需要申请；官方入口可能已不可用 | 备用 | 未核实 |
| WildChat-4.8M（英文子集） | 真实用户与 ChatGPT 的对话，含 hashed_ip、header、时间戳 | Hugging Face `allenai/WildChat-4.8M` | 第二阶段 | 未核实 |
| WildChat 中文部分 | 真实中国用户面对 AI 的对话 | 同上 | 第一阶段交叉对照（[roadmap.md](roadmap.md) 风险 1）。是否使用：**待定** | 未核实 |
| MIMESIS-4B/9B | Meta 开源的专用用户模拟器 | Hugging Face，见第 2 节 | 第一阶段参照模型 | 未核实 |

### 4.1 CSDS 注意事项

- 用户诉求摘要可能包含对话结果。处理规则见 [architecture.md](architecture.md) ADR-009。
- 格式检查清单见 [任务规格 第 1 步](../misc/任务规格-试跑.md)。

### 4.2 WildChat 英文子集清洗规则

来自 Gemini 报告对 arXiv 2605.29018 的转述，**未核实**：
- 只用 2024 年 9 月之前的数据。
- hashed_ip 和 header 必须一致。
- 剔除模板化流量。

为什么不用中文子集见 [architecture.md](architecture.md) ADR-004。

## 5. 凭证管理

- 只用环境变量。
- 不写进代码，不写进日志。
- 环境变量名称：**待定**（取决于第 0 步确认的 Spark 鉴权方式）。

---

## 附：冲突与差异

1. **MIMESIS 来源。** 研究提案第 3 节标注 MIMESIS"仅见新闻报道"，且只提到 9B。项目总览和 Gemini 报告给出 arXiv 2610.09484 和 4B/9B 两个版本。以项目总览为准，但仍属未核实。
2. **MIMESIS 托管账号。** Gemini 给出的 Hugging Face 路径在个人账号 `phanviethoang1512` 下，不在 Meta 组织下。需核实是否为官方发布。
3. **WildChat 中文部分。** 项目总览第 3 节说第二阶段只用英文子集；第 9 节又建议第一阶段用中文部分做交叉对照。两者用途不同（后者不需要串联同一用户），不算直接矛盾。但项目总览第 5 节数据源表没有列出中文部分，故此处标为待定。
