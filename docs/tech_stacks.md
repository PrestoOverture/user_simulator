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
- **第三方依赖：** `httpx`、`pytest`（试跑时确定，见 `requirements.txt`）。虚拟环境 `.venv` 用 Python 3.12 建（本机 3.11 的 ensurepip 损坏）。

## 2. 模型

模型分工的决定见 [architecture.md](architecture.md) ADR-003、ADR-006。

| 角色 | 模型 | 状态 |
|---|---|---|
| 模拟器（主力） | 讯飞星辰 Spark X2.6（模型 ID `spark-x2.6`） | 已定。10 亿 token 免费额度；用户已确认条款，不再查（2026-10-25） |
| 对比模型（一两个低价模型，小样本） | **待定**（[roadmap.md](roadmap.md) D3） | Gemini 报告提到的候选（**未核实**）：DeepSeek-V3.2、Qwen3.5、GPT-4o-mini |
| 参照模型 | MIMESIS-4B / 9B（Meta 开源） | **待定**（[roadmap.md](roadmap.md) D4） |
| 判断模型 | `gemini-3.8-flash`（D5）；自 2026-10-28 起 Spark 判全量、Gemini 抽样校正（[architecture.md](architecture.md) ADR-018） | Spark 判断用 thinking enabled（ADR-012 的例外） |

**付费模型（用户提供，2026-10-25 接通）：**

- **凭证：** `SANDBOX_API_KEY`；地址为 `SANDBOX_BASE_URL` 加 `/v1`（OpenAI 兼容，`/v1/chat/completions`）。
- **可用模型：** `deepseek-v4.1-flash`、`gemini-3.8-flash`、`glm-5.3-flash`。
- **规则（用户要求）：** 这三个模型按量付费。只在需要它们的地方用（对比模拟器、判断模型），拿到需要的答案后回到 Spark。`SPARK_API_KEY` 只能调 Spark，不能调讯飞平台上的其他模型（那些不免费）。
- **推理模式实测：** `deepseek-v4.1-flash` 对"你好"不推理，但在真实重放 prompt 上默认推理（3 段小批中 89% 的输出 token 是推理）；请求体加 `"thinking": {"type": "disabled"}` 可关闭（实测 reasoning_tokens=0）。**2026-10-27 复测：网关已不再理会这个参数**（reasoning_tokens 85–200，`enable_thinking: false` 也无效）。现在要同时加 `"reasoning_effort": "none"` 才能关闭（响应中没有 reasoning_tokens 字段，也没有 reasoning_content）。每次运行都要逐个响应核对。`gemini-3.8-flash` 和 `glm-5.3-flash` 默认推理，每次约多 60–120 个 token；按网关文档（https://ai.sandboxai.top/docs ，new-api 网关）和各家常见写法逐一试过，均关不掉：Gemini 的 `reasoning_effort` 为 `none`/`minimal`、`google.thinking_config.thinking_budget: 0`；GLM 的 `thinking: {"type": "disabled"}`、`enable_thinking: false`。网关文档中的 `-nothinking`/`-thinking-{budget}` 模型名后缀，本网关未开启（返回 model_not_available）；`/v1/models` 中也没有不推理的版本（2026-10-25）。结论：用 Gemini 当判断模型时，接受每次约 100 个推理 token 的成本，不再试调。
- **计划分工（用户 2026-10-25）：** DeepSeek 做对比模拟器（D3）；Gemini Flash 做判断模型（D5），不用 GPT，因为 1,386 条标准集是 Codex（GPT 系）判的；GLM 备用。

- **实测成本：** DeepSeek 3 段小批（24 轮，开着推理）网关扣费 1.02 美分；500 段按比例约 2 美元（开着推理）。关闭推理后同样 3 段扣费 0.28 美分（含 Claude 1 次测试调用），500 段约 0.55 美元。余额用 `GET {SANDBOX_BASE_URL}/v1/dashboard/billing/usage` 查（`total_usage`，单位美分）。

- **累计花费（截至 2026-10-28 14:20）：** 网关约 34.76 美元；DeepSeek 分组测量约 5.8 美元；决策层控制（合同 A）约 7.6 美元。此前截至 2026-10-27 中午约 27.13 美元。P2 v2 新增 20.12 美元（Gemini 判断约 2.1 万次；快照见 `outputs/correction_v2/budget.json`）。此前 7.0 美元中， DeepSeek B 全量约 0.59 美元；Gemini 判断模型验证约 0.35 美元；ADR-016 分组测量约 6.05 美元；其余为小批与测试。

**调用参数：**
- temperature 使用官方默认值，并记录。
- thinking 关闭（ADR-012）。
- token 不设上限（用户 2026-10-25 取消了任务规格中的 200 万上限）。

**MIMESIS 的 Gemini 描述（全部未核实）：** arXiv 2610.09484；权重在 Hugging Face `phanviethoang1512/MIMESIS-9B` 与 `MIMESIS-4B`；基于 Qwen3.5-9B / 4B 微调；Apache-2.0 许可；代码 `github.com/VietHoang1512/mimesis`。

## 3. Spark 接口

已于 2026-10-25 对照官方文档确认，并实测调用成功（HTTP 200）。

- **文档：** [API 接入](https://maas.xfyun.cn/doc/guide/1、快速开始/1.1%20API接入.html)、[Chat 接口](https://maas.xfyun.cn/doc/guide/3、API%20接口/3.1%20星火模型%20API/3.1.1%20Chat.html)。
- **地址：** `https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions`（OpenAI 兼容；SDK 的 `base_url` 为 `.../v2`）。
- **鉴权：** `Authorization: Bearer $SPARK_API_KEY`。
- **模型：** `spark-x2.6`。
- **关闭思考：** 请求体加 `"thinking": {"type": "disabled"}`。默认值是 `enabled`；还可取 `auto`。
- **用量字段：** `usage.prompt_tokens`、`completion_tokens`、`total_tokens`；思考模式下另有 `reasoning_tokens`。`max_tokens` 包含推理 token。
- **实测：** 开启思考时一句话回复用了 381 个 completion token、约 7 秒；关闭后约 1–3 秒。
- 第 0 步结果写入 `outputs/spark_check.md`。

## 4. 数据源

所有链接和描述来自 Gemini 报告，**均未核实**。执行时以实际下载到的数据为准。

| 名称 | 内容 | 获取方式 | 用途 | 核实状态 |
|---|---|---|---|---|
| CSDS | 京东售后客服真实对话，带人工标注的用户诉求摘要 | GitHub `xiaolinAndy/CSDS` 的 Google Drive 链接（`gdown --folder`）；已下载到 `data/raw/csds/CSDS/` | 第一阶段主数据 | 已下载（2026-10-25）：train 9,101 / val 800 / test 800 段，71MB；字段含 `Dialogue`、`UserSumm`、`AgentSumm`、`FinalSumm`、`QA`。格式见 `outputs/data_format_notes.md`。全量用户对话中"转人工"类话语出现在 802/9,910 段，绝大多数在第 0–1 条消息，是绕过机器人进入人工的开场；"投诉"仅 1 段、"12315"0 段、"差评"26 段。首个 intent 以政策咨询、配送周期、修改订单为主 |
| MobileCS | 中国移动客服通话转录，含 Turnback（改口）标注 | GitHub `SereTOD/SereTOD2022` | 第一阶段辅助 | 已核实（2026-10-25）：全量需注册、组织者审核、签数据使用协议；只取得公开的 10 段示例（`data/raw/mobilecs/`）。无用户摘要、无解决标注、无 LICENSE。是否申请：**待定**（你决定）。详见 `outputs/data_format_notes_mobilecs.md` |
| ECD | 淘宝客服对话，约 100 万对 | GitHub `cooelf/DeepUtteranceAggregation` → Google Drive ZIP | 第一阶段辅助 | 已下载（2026-10-25）：303MB，`data/raw/ecd/`。**是响应选择格式**（label + 上下文 + 候选回复），无说话人标注、无对话 ID、无摘要、无 LICENSE，不能直接用于逐轮重放的配对比较。详见 `outputs/data_format_notes_ecd.md` |
| JDDC | 京东客服对话，100 万+ | 原官网已失效；JDDC 2.1 需填表并用机构邮箱申请 | 备用 | 已核实（2026-10-25）：无免申请入口。另有第三方衍生子集（DialogueCSE 仓库，自称 Apache-2.0），未下载 |
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
- Spark：`SPARK_API_KEY`，放在项目根目录 `.env`（已在 `.gitignore` 中）。

---

## 附：冲突与差异

1. **MIMESIS 来源。** 研究提案第 3 节标注 MIMESIS"仅见新闻报道"，且只提到 9B。项目总览和 Gemini 报告给出 arXiv 2610.09484 和 4B/9B 两个版本。以项目总览为准，但仍属未核实。
2. **MIMESIS 托管账号。** Gemini 给出的 Hugging Face 路径在个人账号 `phanviethoang1512` 下，不在 Meta 组织下。需核实是否为官方发布。
3. **WildChat 中文部分。** 项目总览第 3 节说第二阶段只用英文子集；第 9 节又建议第一阶段用中文部分做交叉对照。两者用途不同（后者不需要串联同一用户），不算直接矛盾。但项目总览第 5 节数据源表没有列出中文部分，故此处标为待定。
