# 中文售后客服中的 LLM 用户模拟器失真

测量 LLM 扮演"用户"时与真实用户差在哪里。场景是中文售后客服，数据来自京东 CSDS 的 9,910 段真实对话。

**一句话结论：** 客服回应之后，真人往往就此作罢；模拟器则继续提出要求。差距约 25 个百分点，在 Spark 和 DeepSeek 上都成立，而且靠提示词修正不了。

> 状态（2026-10-30）：第一阶段的测量和免训练修正已完成，正在整理成果（[docs/roadmap.md](docs/roadmap.md) P3）。初稿见 [paper/phase1_draft.md](paper/phase1_draft.md)。文中引用的文献细节尚未逐条核对原文。

## 主要发现

### 1. 反应方式（核心）

每个用户轮次判为 `close`（接受、致谢、换话题）、`continue`（追问、加码、提出新要求）或 `unclear`。全量 CSDS 抽 3,000 轮：

| 客服回应 | 真人 continue | Spark | DeepSeek |
|---|---:|---:|---:|
| 负面回应之后 | 58.8% | 83.7%（+24.9） | 86.3%（+27.5） |
| 非负面回应之后 | 47.0% | 74.2%（+27.2） | 70.8%（+23.7） |

- 判定规则先登记、再看数据。两组差距没有显著区别，所以结论不限于"被拒之后"，而是普遍存在。
- 模拟器也更常把负面情绪说出来，这个差距在被拒之后更大。

### 2. 不会离开

CSDS 中 90% 的对话，用户在客服最后一句话之后就不再回复。在这些点上给模拟器一个明确的 `[离开]` 选项，Spark 也只有 **49%** 选择离开。客服最后一句不是结束语时，只有 40%。

### 3. 免训练修正修不动

| 方法 | continue 差距缩小 |
|---|---|
| 真实原话示例 | 基本不变（但话长、礼貌程度改善） |
| 按情境检索的真实回应示例 | 2.8 个百分点（基线约 +26） |
| 按真实分布控制轮次长度和连发条数 | 2.0 |
| 把目标反应写进指令（不泄漏答案） | 3.7 |
| 直接告诉模拟器真人这一轮的实际反应（oracle 诊断） | 11.3，即只缩小 44% |

情绪表达的差距可以用情境示例基本消除；"继续要求"的倾向不行。模型训练（LoRA）暂不考虑，原因见 [ADR-020](docs/architecture.md)。

### 背景：表达层

模拟器话更长（首轮 38.9 字 vs 真人 15.5 字）、更礼貌、很少说短句（3.0% vs 35.0%）。这是对英文 Sim2Real Gap 结论的中文复现，不是本项目的核心发现。

## 方法

- **逐轮重放（[ADR-001](docs/architecture.md)）：** 保持真实对话的前 t−1 轮不变，包括真人客服的发言，只让模拟器生成第 t 轮。两侧面对的客服完全相同，差异只来自模拟器。
- **配对比较：** 同一上下文下，比较真人和模拟器的反应。区间用按对话的配对 bootstrap（95%）。
- **判断模型与模拟器分离：** 用 Gemini-3.8-Flash 做判断，测试集 κ 0.93 / 0.89。后期改为 Spark 判全量 + Gemini 抽 100 段做 PPI 校正（[ADR-018](docs/architecture.md)，事后采纳，已在论文中说明）。
- **模拟器：** Spark X2.6（主力，关闭思考）；DeepSeek-V4.1-Flash（对比）。

## 目录

```
usersim/   可复用的核心库：数据适配、重放、模型客户端、判断器、指标、PPI
scripts/   各实验的入口脚本
config/    关键词、行为规则、判断器 prompt（哈希锁定）
tests/     pytest
outputs/   各实验的结果和报告
paper/     第一阶段成果初稿
docs/      项目文档（目标、路线图、决策记录、技术栈、进展）
data/raw/  原始数据（不进 git）
```

各模块的说明见 [docs/architecture.md](docs/architecture.md) 的"项目结构"。

## 运行

### 环境

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```

### 数据

从 [CSDS](https://github.com/xiaolinAndy/CSDS) 下载，放到 `data/raw/csds/CSDS/`（`train.json`、`val.json`、`test.json`）。

### 凭证

在项目根目录的 `.env` 中写入以下变量（`.env` 已在 `.gitignore` 中）。脚本只从环境变量读取，运行前要先导出：

```bash
SPARK_API_KEY=...       # 讯飞 Spark X2.6，模拟器
SANDBOX_API_KEY=...     # OpenAI 兼容网关：DeepSeek（对比模拟器）、Gemini（判断模型）
SANDBOX_BASE_URL=...
```

```bash
set -a; source .env; set +a
```

### 不调 API，从缓存重建报告

```bash
.venv/bin/python scripts/run_leave.py --phase report
.venv/bin/python scripts/run_correction_v2.py --phase report
.venv/bin/python scripts/run_correction_decision.py --phase report
.venv/bin/python scripts/measure_reaction.py --sim-dir outputs/full_csds/spark --out outputs/reaction/full --report-only
```

### 主要实验

| 实验 | 脚本 | 结果 |
|---|---|---|
| 逐轮重放基线 | `run_pilot.py` | `outputs/full_csds/spark/` |
| 反应方式分组测量 | `measure_reaction.py`、`measure_reaction_deepseek.py` | `outputs/reaction/` |
| 判断模型验证 | `validate_judge.py`、`validate_spark_judge.py` | `outputs/judge_validation/`、`outputs/spark_judge_validation/` |
| 修正 v2 | `run_correction_v2.py` | `outputs/correction_v2/` |
| 决策层控制 | `run_correction_decision.py` | `outputs/correction_decision/` |
| 直接离开 | `run_leave.py` | `outputs/leave/` |

付费模型（DeepSeek、Gemini）按量计费。到 2026-10-28 为止，全部实验累计约 35 美元。

## 文档

- [docs/prd.md](docs/prd.md)：项目目标与研究问题
- [docs/roadmap.md](docs/roadmap.md)：阶段与决策点
- [docs/architecture.md](docs/architecture.md)：方法决策（ADR）与代码结构
- [docs/tech_stacks.md](docs/tech_stacks.md)：模型、数据、接口、花费
- [docs/history.md](docs/history.md)：进展记录
