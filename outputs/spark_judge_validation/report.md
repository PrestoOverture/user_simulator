# Spark 判断器验证（合同 C）

状态：complete。仅调用 Spark；离线报告不创建客户端。
预先登记：n=100，在所有数据集/条件/分组的 continue 与情绪差距上覆盖率≥90%，区间宽度中位数较仅 Gemini 缩小≥20%。n=50/100/200，各500次；按对话 bootstrap 2000次；种子20261027。
thinking enabled 是按合同要求对 ADR-012 默认关闭规则的例外；temperature 省略，使用官方默认。Spark 测试集 κ 只报告，不设门槛。
API 参数已核对[讯飞官方 Chat 文档](https://maas.xfyun.cn/doc/guide/3%E3%80%81API%20%E6%8E%A5%E5%8F%A3/3.1%20%E6%98%9F%E7%81%AB%E6%A8%A1%E5%9E%8B%20API/3.1.1%20Chat.html)。
冻结 prompt SHA-256：87d1d40d05a09382cc9a1c47e0963e40a8e0cbe7e5c3e5cb9e00ce50eed1ee8d。请求只包含 goal/history/previous_agent/text，来源、分组和标注均不进入请求。

## 变体选择（仅开发集100轮）

|thinking|指标|准确率|κ|
|---|---|---:|---:|
|disabled|topic|0.860000|0.6682464454976303|
|disabled|emotion|0.930000|0.700342465753425|
|enabled|topic|0.920000|0.8000499875031243|
|enabled|emotion|0.940000|0.788732394366197|

### 开发进度与停止证据

|thinking|已返回/计划|成功|解析失败|moderated|
|---|---:|---:|---:|---:|
|disabled|100/100|100|0|0|
|enabled|100/100|100|0|0|

### Delta Contract：共用解析器与缓存重解析

两个变体均允许剥掉首尾空白、成对的 ** 或 *、以及 ```json ... ``` 外层代码块；剩余内容仍须恰好是一个合法 JSON 对象，字段及取值范围不变，其他多余文本仍为解析失败。
共离线重新解析 175 条已有缓存响应，其中原解析失败 4 条，恢复成功 4 条；没有重新请求，原始缓存字节保留。2%停止线按新解析器计算。
原严格解析的停点见 stop_evidence.json；该文件为历史记录，现行失败比例以本报告及新解析器为准。

选定变体：enabled。话题 κ 相同则选 disabled；测试只评估选定变体一次，缓存复用不发请求。

## 测试集200轮：准确率与 κ

|分组|指标|n|准确率|κ|
|---|---|---:|---:|---:|
|all|topic|200|0.905000|0.762840916182987|
|all|emotion|200|0.945000|0.7983870967741934|
|human|topic|66|0.863636|0.7652173913043477|
|human|emotion|66|0.984848|0.900900900900901|
|spark|topic|66|0.939394|0.7874396135265703|
|spark|emotion|66|0.954545|0.8524590163934428|
|deepseek|topic|68|0.911765|0.6277372262773722|
|deepseek|emotion|68|0.897059|0.6940874035989718|
|negative=False|topic|100|0.870000|0.7182488079757261|
|negative=False|emotion|100|0.990000|0.93573264781491|
|negative=True|topic|100|0.940000|0.8223275096239264|
|negative=True|emotion|100|0.900000|0.7272231314784507|
|human/negative=False|topic|33|0.787879|0.6709401709401709|
|human/negative=False|emotion|33|1.000000|None|
|human/negative=True|topic|33|0.939394|0.876172607879925|
|human/negative=True|emotion|33|0.969697|0.8910891089108912|
|spark/negative=False|topic|33|0.909091|0.689655172413793|
|spark/negative=False|emotion|33|1.000000|1.0|
|spark/negative=True|topic|33|0.969697|0.8910891089108912|
|spark/negative=True|emotion|33|0.909091|0.7441860465116279|
|deepseek/negative=False|topic|34|0.911765|0.6851851851851851|
|deepseek/negative=False|emotion|34|0.970588|0.8411214953271027|
|deepseek/negative=True|topic|34|0.911765|0.5426008968609864|
|deepseek/negative=True|emotion|34|0.823529|0.5984251968503935|

## 自偏差（Spark 比例 − 人工比例）

在各来源共同有效轮次计算；解析失败计入准确率及 κ 的错误类别，但不虚构其 continue/情绪标签。moderated 剔除。

|来源/分组|有效n|continue 偏差|情绪偏差|解析失败n|
|---|---:|---:|---:|---:|
|all|200|0.025|0.035|0|
|human|66|0.07575757575757576|0.015151515151515152|0|
|spark|66|0.030303030303030304|0.045454545454545456|0|
|deepseek|68|-0.029411764705882353|0.04411764705882353|0|
|negative=False|100|0.1|0.01|0|
|negative=True|100|-0.05|0.06|0|
|human/negative=False|33|0.18181818181818182|0.0|0|
|human/negative=True|33|-0.030303030303030304|0.030303030303030304|0|
|spark/negative=False|33|0.09090909090909091|0.0|0|
|spark/negative=True|33|-0.030303030303030304|0.09090909090909091|0|
|deepseek/negative=False|34|0.029411764705882353|0.029411764705882353|0|
|deepseek/negative=True|34|-0.08823529411764706|0.058823529411764705|0|

## 与 Gemini 的一致率和 κ

输入数量：{'reaction/full': 5999, 'correction_v2': 21500}；去重后的独立输入：27203。相同内容复用 Spark 判断。

|数据集|来源/条件|分组|指标|有效n|一致率|κ|未完成|moderated|
|---|---|---|---|---:|---:|---:|---:|---:|
|correction_v2|baseline|all|topic|4300|0.866512|0.6613160400789284|0|0|
|correction_v2|baseline|all|emotion|4300|0.938605|0.7718895231542582|0|0|
|correction_v2|combined|all|topic|4300|0.880233|0.6875305573103107|0|0|
|correction_v2|combined|all|emotion|4300|0.947674|0.7378486409856784|0|0|
|correction_v2|human|all|topic|4300|0.824651|0.721385393984616|0|0|
|correction_v2|human|all|emotion|4300|0.939767|0.6856636428809321|0|0|
|correction_v2|situation|all|topic|4300|0.868372|0.6878930911369358|0|0|
|correction_v2|situation|all|emotion|4300|0.941628|0.7275089475427581|0|0|
|correction_v2|structure|all|topic|4300|0.876512|0.7063241593234846|0|0|
|correction_v2|structure|all|emotion|4300|0.940000|0.7201412058397503|0|0|
|reaction/full|human|negative|topic|1500|0.926000|0.8603242992935682|0|0|
|reaction/full|human|negative|emotion|1500|0.948667|0.8200542174305924|0|0|
|reaction/full|human|nonnegative|topic|1499|0.825884|0.7237299517634821|0|1|
|reaction/full|human|nonnegative|emotion|1499|0.953969|0.7496035771263398|0|1|
|reaction/full|simulator|negative|topic|1500|0.925333|0.7397301869604824|0|0|
|reaction/full|simulator|negative|emotion|1500|0.937333|0.8298823171717513|0|0|
|reaction/full|simulator|nonnegative|topic|1499|0.865911|0.6517475097494839|0|0|
|reaction/full|simulator|nonnegative|emotion|1499|0.939960|0.755722619965887|0|0|

## PPI 公式与抽样

每个数据集独立抽样；M 为全量有效对话数，N_g 为组 g 全量配对轮次。d^S、d^G 分别是同一轮次模拟器指标减真人指标，按对话汇总为 D^S、D^G。
PPI 点估计：Δ̂ = Σ全量 D^S / N_g + M/(n N_g) Σ抽样对话 (D^G − D^S)。只用 Gemini：Δ̂_G = M/(n N_g) Σ抽样对话 D^G。
这是以已知全量轮次分母归一化的 Horvitz–Thompson 估计；均匀无放回抽对话时，配对矫正项使点估计精确设计无偏，即使对话长度不同。样本估计可能超出比例差的范围，不截断。
每次抽取 n 段对话（同一数据集各条件/指标共用样本），再有放回重采样这些完整对话 2000 次，Spark 全量项及 N_g 固定，取2.5%/97.5%百分位。未加有限总体修正，区间可能保守。n=50/100/200 各重复500次。
所有指标在同一批配对轮次上计算；任一侧 moderated 或解析失败的轮次从配对分析剔除。correction_v2 沿用已有五侧共同有效配对池，再剔除 Spark 不完整配对。Gemini 全量参照在该支持集重算；原始支持集和剔除数如下。
{"reaction/full": {"original_pairs": 3000, "excluded_pairs": 1}, "correction_v2": {"original_pairs": 4300, "excluded_pairs": 0}}

## PPI 覆盖率与区间宽度

采纳标准的宽度缩小=1−PPI区间宽度中位数/Gemini-only区间宽度中位数。另在 summary 中报告逐次配对相对缩小的中位数；该辅助指标遇 Gemini-only宽度为0时记缺失并单列。

|数据集|条件|组|指标|n|覆盖率|PPI中位宽度|Gemini中位宽度|中位缩小|零宽样本数|抽样轮次中位数|
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
|ppi_correction_v2_100|baseline|all|continue|100|0.968|0.061628|0.089535|0.3116883116883118|0|857.0|
|ppi_correction_v2_100|baseline|all|emotion|100|0.960|0.043023|0.063953|0.32727272727272727|0|857.0|
|ppi_correction_v2_100|situation|all|continue|100|0.956|0.061628|0.083721|0.26388888888888906|0|857.0|
|ppi_correction_v2_100|situation|all|emotion|100|0.940|0.044215|0.060465|0.2687500000000025|0|857.0|
|ppi_correction_v2_100|structure|all|continue|100|0.958|0.061628|0.096512|0.36144578313252995|0|857.0|
|ppi_correction_v2_100|structure|all|emotion|100|0.974|0.046512|0.061628|0.24528301886792447|0|857.0|
|ppi_correction_v2_100|combined|all|continue|100|0.960|0.058140|0.094186|0.38271604938271575|0|857.0|
|ppi_correction_v2_100|combined|all|emotion|100|0.970|0.044186|0.062791|0.29629629629629617|0|857.0|
|ppi_correction_v2_200|baseline|all|continue|200|0.992|0.043634|0.063372|0.3114678899082586|0|1722.0|
|ppi_correction_v2_200|baseline|all|emotion|200|0.996|0.030814|0.045945|0.32932616260676983|0|1722.0|
|ppi_correction_v2_200|situation|all|continue|200|0.994|0.044186|0.059331|0.25526702596766204|0|1722.0|
|ppi_correction_v2_200|situation|all|emotion|200|0.986|0.031977|0.042442|0.24657534246575352|0|1722.0|
|ppi_correction_v2_200|structure|all|continue|200|0.986|0.044193|0.068038|0.3504593035676148|0|1722.0|
|ppi_correction_v2_200|structure|all|emotion|200|0.990|0.032573|0.043619|0.25324891702765806|0|1722.0|
|ppi_correction_v2_200|combined|all|continue|200|0.994|0.041860|0.066875|0.3740491197565746|0|1722.0|
|ppi_correction_v2_200|combined|all|emotion|200|0.984|0.031395|0.044186|0.2894736842105262|0|1722.0|
|ppi_correction_v2_50|baseline|all|continue|50|0.924|0.086047|0.123256|0.30188679245283034|0|429.0|
|ppi_correction_v2_50|baseline|all|emotion|50|0.944|0.060465|0.088372|0.3157894736842105|0|429.0|
|ppi_correction_v2_50|situation|all|continue|50|0.948|0.086047|0.116279|0.26000000000000034|0|429.0|
|ppi_correction_v2_50|situation|all|emotion|50|0.950|0.062849|0.082587|0.23900035198873637|0|429.0|
|ppi_correction_v2_50|structure|all|continue|50|0.924|0.086047|0.132616|0.35116177115300184|0|429.0|
|ppi_correction_v2_50|structure|all|emotion|50|0.940|0.062849|0.086047|0.2695945945945962|0|429.0|
|ppi_correction_v2_50|combined|all|continue|50|0.950|0.081395|0.130233|0.375|0|429.0|
|ppi_correction_v2_50|combined|all|emotion|50|0.944|0.062791|0.088372|0.28947368421052644|0|429.0|
|ppi_reaction_full_100|simulator|negative|continue|100|0.932|0.182967|0.282767|0.3529411764705882|0|60.0|
|ppi_reaction_full_100|simulator|negative|emotion|100|0.914|0.166333|0.232867|0.2857142857142857|0|60.0|
|ppi_reaction_full_100|simulator|nonnegative|continue|100|0.930|0.216794|0.299600|0.27638888888888924|0|60.0|
|ppi_reaction_full_100|simulator|nonnegative|emotion|100|0.918|0.166444|0.199733|0.16666666666666663|0|60.0|
|ppi_reaction_full_200|simulator|negative|continue|200|0.952|0.133067|0.199600|0.33333333333333337|0|120.0|
|ppi_reaction_full_200|simulator|negative|emotion|200|0.944|0.116433|0.174650|0.33333333333333337|0|120.0|
|ppi_reaction_full_200|simulator|nonnegative|continue|200|0.936|0.158122|0.216378|0.2692307692307693|0|120.0|
|ppi_reaction_full_200|simulator|nonnegative|emotion|200|0.946|0.116511|0.133155|0.125|0|120.0|
|ppi_reaction_full_50|simulator|negative|continue|50|0.830|0.266133|0.399200|0.33333333333333337|0|30.0|
|ppi_reaction_full_50|simulator|negative|emotion|50|0.814|0.232867|0.332667|0.30000000000000004|0|30.0|
|ppi_reaction_full_50|simulator|nonnegative|continue|50|0.918|0.332889|0.432755|0.23076923076923084|0|30.0|
|ppi_reaction_full_50|simulator|nonnegative|emotion|50|0.816|0.233022|0.266311|0.1250000000000001|4|30.0|

## reaction/full 交互项判定一致性

按 ADR-016：continue 的负面组差距 − 非负面组差距区间不含0，且负面组差距绝对值更大，才限定在被拒之后。两组按同一对话重采样。情绪不参与判定。
- ppi_reaction_full_100：共同支持集全量判定=用普遍定义；原报告=用普遍定义；与共同支持集一致比例=0.992；与原报告一致比例=0.992。逐次交互项点估计、区间和判定见 ppi_reaction_full_100.json。
- ppi_reaction_full_200：共同支持集全量判定=用普遍定义；原报告=用普遍定义；与共同支持集一致比例=0.994；与原报告一致比例=0.994。逐次交互项点估计、区间和判定见 ppi_reaction_full_200.json。
- ppi_reaction_full_50：共同支持集全量判定=用普遍定义；原报告=用普遍定义；与共同支持集一致比例=0.982；与原报告一致比例=0.982。逐次交互项点估计、区间和判定见 ppi_reaction_full_50.json。

## 预先登记采纳标准

是否达标：False。不根据结果调整标准、n 或统计方法。

## 解析失败、moderated 与 Spark token

响应状态：{'ok': 27602, 'request_error': 122, 'rate_limited': 1, 'moderated': 1}；解析失败比例=0.000000%（分母为返回内容或审核拒绝的终结响应）；超过2%立即停止，不重试内容、不改 prompt。
实际返回 token：{'prompt_tokens': 15044987, 'completion_tokens': 13504333, 'total_tokens': 28549320, 'reasoning_tokens': None}；缺失 usage 请求数=124；enabled 有 27502 个响应未返回推理 token 明细，reasoning_tokens 记 null 而非0。reasoning_tokens 为 completion_tokens 子项，不重复加总；缺失用量不视为0，完整计费量在有缺失时未知。
moderated 输入：[{'dataset': 'reaction/full', 'condition': 'human', 'dialogue_id': 2069, 'turn': 11}]。10013 不重试；配对两侧剔除，存在内容选择偏差。

逐条判断及实际 usage 在 dev_disabled.jsonl、dev_enabled.jsonl、test.jsonl、full.jsonl；输入映射在 comparison_inputs.json；所有 PPI 模拟逐次结果在 ppi_*.json。
