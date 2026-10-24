# ECD 格式笔记

核验日期：2026-10-25（Asia/Shanghai）。未调用任何模型 API；数据仅由 /opt/homebrew/bin/python3.12 -I 的标准库读取；未安装下载工具、未执行数据包脚本；未随机抽样，样例按文件顺序取。大小先检查后下载/解压，单数据集 5GB 停止阈值未触发。

## 获取状态、来源与许可证

**README 指向的全量压缩包已下载并解压。**

- [作者仓库 README](https://github.com/cooelf/DeepUtteranceAggregation/blob/master/README.md) 的实际链接是 [Google Drive 文件](https://drive.google.com/file/d/154J-neBo20ABtSmJDvm7DK0eTuieAuvw/view?usp=sharing)，不是目录；API 核对仓库 HEAD `f2e15d6600ccda0db32eee98b2ca67c322071964`。
- 实际全量来源：`https://drive.usercontent.google.com/download?id=154J-neBo20ABtSmJDvm7DK0eTuieAuvw&export=download`，提交页面自带的公开下载确认参数后 HTTP200，文件 `E-commerce dataset.zip`。未登录、未申请、未填表；确认页只是 Google 无法扫描大文件的提示，无访问授权绕过。
- 下载前 Drive viewer itemJson 大小54,000,633字节，确认页显示51M；实际响应 Content-Length 和落盘大小均54,000,633。解压前只查ZIP central directory：总解压262,621,362字节（约250.5MiB），没有绝对路径或上层路径；小于5GB，才解压。最初磁盘可用约13GiB。保留ZIP与文本以便复核。
- 同时取得官方 GitHub `ECD_sample/{train,valid,test}` 和根README，源为 `https://raw.githubusercontent.com/cooelf/DeepUtteranceAggregation/master/` 下同名路径。源码预处理文件仅曾下载并作为文本阅读，随后删除，未执行。
- **未发现明确数据许可证**：仓库树没有LICENSE，两个README均未给出开放使用授权/商业许可范围。根README要求使用代码引用 Zhang et al., COLING2018。不能把可下载推定为Apache/MIT许可；正式使用、再分发的权利范围需 Claude/用户确认，本文不替其作决定。

## 规格第1步：五个问题

1. **结构与说话人**：UTF-8纯文本，每行一条候选响应判别样本，以真实 TAB 分列：`label<TAB>context utterance 1<TAB>...<TAB>context utterance N<TAB>response`。第一列1为匹配响应、0为负候选；不是解决问题标签。没有对话ID、speaker、时间戳或原始完整会话边界；正文已中文分词，用空格分词。说话人没有显式标注，不能仅按列奇偶保证用户/客服身份。仓库样例首行候选客服回复前的context可从用户开始，第二条正样本context从“亲 在”（客服语气）开始，因此不能统一把第一列发言设为用户。作者 PreProcess.py 文本只按TAB拼接context，没有恢复speaker；本任务未运行它。
2. **用户连发分开还是合并**：字段只保留 utterance 文本，无原始消息ID、角色或时间信息；不能验证连续 utterance 属于同一说话人，不能确认原始连发是单独记录还是已合并，不能还原连发条数。不自行按标点/分词空格拆消息；该指标的可测性需另行决定。
3. **用户诉求摘要及结果**：无User Summary/目标字段。正样本末列是实际匹配响应，文本可能涉及承诺、物流、退款等结果，但不是诉求摘要；不从整条样本自动抽取目标，避免泄露后续响应。
4. **问题是否解决**：无 solved 标注。`label=1`只表示候选相关/匹配，不表示业务成功；负候选也不是用户放弃。
5. **总量与平均轮数**：下表为全文件实测。一个“轮”明确定义为一个context TAB字段（单条utterance），不等同一问一答，也不是完整原始会话。计数对正样本逐行算，包含重复context；匹配README平均值，不能据此认定拥有百万独立完整对话。

| 全量文件 | 总行数 | 正样本 | 负候选 | 正样本context平均utterance数 | 加正响应后平均发言数 | context最短–最长 | 正样本内去重context数 |
|---|---:|---:|---:|---:|---:|---|---:|
| train.txt | 1,000,000 | 500,000 | 500,000 | 5.509464 | 6.509464 | 1–119 | 386,478 |
| dev.txt | 10,000 | 5,000 | 5,000 | 5.482 | 6.482 | 1–111 | 4,723 |
| test.txt | 10,000 | 1,000 | 9,000 | 5.64 | 6.64 | 1–49 | 969 |

总计1,020,000候选行，506,000条正样本；不是1,020,000个独立完整会话。去重仅对各split正样本的原始context字段tuple作集合，不跨split去重，也不推断会话ID。README报告train/val/test平均5.51/5.48/5.64，与实测均值舍入一致，但宣称min3/max10与实际1–119等不一致；保留原始数据，不按README截断。GitHub小样例train/valid各154行（77正77负，二者字节相同），test1000行（100正900负），不和全量重复累加。

## usersim 适配层：只读检查与字段映射

核对 `usersim/csds.py` 的 `load/user_turns/sample/clean_summary` 和 `usersim/replay.py` 的 `prompt`。**不能直接复用原文件输入**：load只读*.json，其他函数依赖CSDS字段；更关键是ECD缺角色、目标、完整会话边界。

| ECD来源 | 当前usersim目标字段 | 映射与缺口 |
|---|---|---|
| split文件+物理行号 | `DialogueID` | 可构造`ecd:train:行号`，只是样本ID，不是原会话ID |
| train/dev/test文件名 | `split` | 可映射train/val/test（dev→val） |
| 第2列至倒数第2列 | `Dialogue[].utterance` | 原样保留分词空格，作为context；原始消息粒度未知 |
| 末列response | `Dialogue[].utterance` | 仅label=1才可视为匹配续接；label=0不可加入真人历史 |
| 无 | `Dialogue[].speaker='Q'/'A'` | 无法可靠映射，不能仅奇偶分配角色 |
| 无 | `QRole='用户'` | 不能在未核定角色时填入 |
| 无 | `UserSumm: list[str]` | 不可自动推断，目标定义交Claude决定 |
| label | 当前接口无对应字段 | 应保留候选标签，不映射solved，不用负候选做真人续接 |

`user_turns`依赖连续Q消息来得到`start/messages`；本数据不具备此证据。即使转换成JSON形状，也不等于可直接进行用户逐轮重放。重建角色、定义目标、去重/会话拼接、决定是否只用匹配片段均属另行设计，本任务不实现或替Claude决定。`replay.SYSTEM`京东售后角色也与淘宝场景不一致。

## 文件清单与大小

目录 `du -sh data/raw/ecd`：**303M**，包括保留的ZIP、解压文本与GitHub样例/README。磁盘分配量不同于以下逻辑字节，未将重复保留计为额外数据样本。

| 相对路径 | 字节 | SHA256 |
|---|---:|---|
| `E-commerce dataset/README.md` | 650 | `bdbfd90e3399e36ac4edcaaf01e7d18a4d85115f914ab6c3236069c7db1777ad` |
| `E-commerce dataset/dev.txt` | 2,556,423 | `7ee4da6a7144040337b0f3dc999641255d5ef19a035d939ae4a6eafd3cadd7a3` |
| `E-commerce dataset/test.txt` | 2,810,240 | `7c5d5c60fee198e6855a714e1161ed165b564ccb96b4a9db36593e5f854319e6` |
| `E-commerce dataset/train.txt` | 257,254,049 | `7f7923594f2c4efec272850a21ec3c96b3259fae9e3fbb5d20bd4c1ca4199c3d` |
| `E-commerce dataset.zip` | 54,000,633 | `41f09f0312b7611863794fb46d9f7f4061d8171901232ac3c17929295e4878b2` |
| `ECD_sample/test` | 284,753 | `3fd9f60cda25732a19deba1bd376495713ab042849e03bc904b241ca414bebc4` |
| `ECD_sample/train` | 68,199 | `f6612035923432e2d9f8b43e0d19e503c212fe9728c61d5ab16e61a5de90d70e` |
| `ECD_sample/valid` | 68,199 | `f6612035923432e2d9f8b43e0d19e503c212fe9728c61d5ab16e61a5de90d70e` |
| `README.md` | 2,233 | `d404441652eff4d869066f79ed0534a6ea73510d4e1d8c4844df5480f83d9eb4` |

## 3段完整原文样例

ECD没有发布可识别的完整原始会话，不能声称下面是3个完整原始客服会话。以下固定取train/dev/test各第1条label=1的**完整已发布样本**，context与正响应全保留；这是当前发布格式能提供的完整粒度，不伪造缺失会话。代码块内保留实际TAB与分词空格，行末不含额外注释。

### 样例：train.txt 第1行

```text
1	538405926243 买 二份 有没有 少点 呀	亲亲 真的 不好意思 我们 已经 是 优惠价 了 呢 小本生意 请亲 谅解
```

### 样例：dev.txt 第1行

```text
1	要 买 一把 茶刀 看到 有 零食 就 拍 了 点	免邮 了 哦	好 的 谢谢 哦	不 客气 哦
```

### 样例：test.txt 第1行

```text
1	我 去 不 早 说 发韵 达 能 到 我家 那儿 我 就 能 拿到	韵达 不发 的 哦	那要 怎样 这 也 不行 那 也 不行	发 邮政 的 哦
```

## JDDC 当前能否免申请获取（只读核查）

结论：**本次未确认原版全量存在可用的官方免申请下载入口；JDDC 2.1明确需申请。** 不应将此写成所有版本、所有衍生子集绝对无法下载。

- 2026-10-25访问原官方入口 `https://jddc.jd.com/` 和 `https://jddc.jd.com/2019/jddc`，均HTTP200跳转 `https://www.jd.com/error2.aspx?from=shopdomain`，标题“您所访问的页面不存在”；HTTP200不代表数据可下载。[官方2020基线README](https://github.com/jd-aig/nlp_baai/tree/master/jddc2020_baseline) 仍指向旧官网，没有据此取得可用全量地址。[原论文入口](https://aclanthology.org/2020.lrec-1.58/)描述百万级JDDC，不把它与后续版本混同。
- [JDDC 2.1官方README](https://github.com/hrlinlp/jddc2.1) 的 Dataset Application 明确要求填写docx/pdf表格并用机构邮箱发给指定联系人。因此此版本不满足免申请获取；没有填写、提交表格、发送邮件或下载受限数据。
- 搜索还出现 [DialogueCSE衍生数据仓库](https://github.com/wangruicn/DialogueCSE)，其README称其JDDC train/dev/test沿用Apache2.0。这里只把它记作第三方衍生子集线索，不能由它证明原版全量目前可用或原官方授权范围；未下载或寻找受限版本镜像。是否采用子集由Claude决定。
