# P2 决策层控制：PPI / Gemini子样本 / Spark全量

PPI口径为用户事后采纳，依据 docs/architecture.md ADR-018。C预先登记标准未达标：reaction/full 非负面组 emotion 的区间宽度仅缩小16.7%，低于20%（覆盖率全部达标）。授权见 ppi_adoption_authorization.json；不将事后采纳表述为预登记通过。oracle故意违反ADR-001，只作诊断，**不是修正方法**。
500段固定总体；4296共同有效轮次；固定随机抽100段（827轮）。抽样名单在preregistration_ppi.json，先登记后请求。
PPI=Spark全量比例 + M/(nN)×样本对话Gemini−Spark指标和；使用usersim/ppi.py。差距=模拟器−真人；缩小=|基线差距|−|本组差距|。分母含unclear。
按100个抽样对话共同bootstrap 2000次，Spark总体固定；95%百分位区间。目标和遵守率分母是全量已知计数。PPI不裁剪到[0,1]。Spark-only为固定总体描述性估计，不给推断区间。所有比较均报告，不按效果重跑。

## ppi

|指标|组|真人|模拟器|差距及95%区间|绝对差距缩小及95%区间|
|---|---|---:|---:|---|---|
|continue|baseline|0.469041|0.726024|+0.256983 [0.22555865921787704, 0.28724394785847296]|+0.000000 [0.0, 0.0]|
|continue|decision|0.469041|0.688780|+0.219739 [0.19064245810055863, 0.24650837988826824]|+0.037244 [0.00581936685288631, 0.07099627560521404]|
|continue|oracle|0.469041|0.612663|+0.143622 [0.11918063314711352, 0.16806331471135938]|+0.113361 [0.08889082867783983, 0.1389664804469274]|
|emotion|baseline|0.092877|0.154562|+0.061685 [0.04073556797020485, 0.08263500931098695]|+0.000000 [0.0, 0.0]|
|emotion|decision|0.092877|0.149441|+0.056564 [0.03445065176908753, 0.0798417132216015]|+0.005121 [-0.013500931098696461, 0.022579143389199252]|
|emotion|oracle|0.092877|0.161080|+0.068203 [0.04958100558659216, 0.08566108007448789]|-0.006518 [-0.02281191806331473, 0.009776536312849166]|
## gemini_only

|指标|组|真人|模拟器|差距及95%区间|绝对差距缩小及95%区间|
|---|---|---:|---:|---|---|
|continue|baseline|0.457402|0.705307|+0.247905 [0.21066108007448792, 0.28398510242085656]|+0.000000 [0.0, 0.0]|
|continue|decision|0.457402|0.666899|+0.209497 [0.16527001862197394, 0.25139664804469275]|+0.038408 [0.002327746741154546, 0.07335311918063309]|
|continue|oracle|0.457402|0.584264|+0.126862 [0.09427374301675978, 0.15945065176908751]|+0.121043 [0.08961824953445063, 0.15479515828677837]|
|emotion|baseline|0.110568|0.181564|+0.070996 [0.036080074487895714, 0.10707635009310987]|+0.000000 [0.0, 0.0]|
|emotion|decision|0.110568|0.172253|+0.061685 [0.027932960893854747, 0.09660148975791434]|+0.009311 [-0.018621973929236507, 0.03608007448789574]|
|emotion|oracle|0.110568|0.175745|+0.065177 [0.03605097765363127, 0.09660148975791434]|+0.005819 [-0.023277467411545627, 0.03375232774674117]|
## spark_only

|指标|组|真人|模拟器|差距及95%区间|绝对差距缩小及95%区间|
|---|---|---:|---:|---|---|
|continue|baseline|0.495810|0.773743|+0.277933 None|+0.000000 None|
|continue|decision|0.495810|0.734171|+0.238361 None|+0.039572 None|
|continue|oracle|0.495810|0.681331|+0.185521 None|+0.092412 None|
|emotion|baseline|0.098696|0.166201|+0.067505 None|+0.000000 None|
|emotion|decision|0.098696|0.163408|+0.064711 None|+0.002793 None|
|emotion|oracle|0.098696|0.172719|+0.074022 None|-0.006518 None|

## 按目标遵守率（PPI口径）

|组|目标|已知分母|估计|95%区间|
|---|---|---:|---:|---|
|decision|close|1257|0.466189|[0.4383452665075577, 0.4940334128878282]|
|decision|continue|2004|0.825848|[0.7934131736526946, 0.8557884231536926]|
|decision|unclear|1035|0.132367|[0.09842995169082126, 0.17101449275362318]|
|oracle|close|1214|0.746293|[0.7215815485996705, 0.7668863261943987]|
|oracle|continue|2055|0.882725|[0.8608272506082725, 0.9021897810218978]|
|oracle|unclear|1027|0.488802|[0.4206426484907497, 0.5569620253164557]|

## decision目标/实际比例（PPI口径）

|目标|目标比例|实际估计|95%区间|
|---|---:|---:|---|
|close|0.292598|0.214385|[0.19689827746741154, 0.23184357541899442]|
|continue|0.466480|0.688780|[0.667830540037244, 0.7085661080074488]|
|unclear|0.240922|0.096834|[0.08170391061452514, 0.11312849162011172]|

## 目标原文

- close：本轮你决定就此结束这个问题：接受或确认客服的答复（可以致谢），不再在这个问题上追问或提出新要求。
- continue：本轮你决定在这个问题上继续：追问、表达不接受，或提出新的要求。
- unclear：本轮只提供客服需要的信息或直接回答客服的问题，不表态是否接受。

## 排除、费用与限制

生成前沿用v2排除；Spark判断审核共同排除0轮、解析失败缺失共同剔除3轮（去重共3轮）；解析失败不重试内容，累计比例超过2%才停止；无未来用户文本进入生成提示词，oracle仅加标签对应目标句。
真人/基线Gemini按完整输入指纹从v2复用；Spark按输入指纹从C复用，缺失在A缓存补判。Spark thinking enabled，冻结prompt，来源标签不进入请求。
历史全量Gemini请求保留，当前只对预登记100段的decision/oracle补判；8条字段名换行仅离线重解析，无新请求。
中断在飞请求abandoned_in_flight共1条；usage未知(null)，可能已收费；重发使用递增attempt，已有ok不重发。共享账户账单差额含其他任务，不能精确归因。实际token与usage在逐请求日志。
{"start_total_usd": 27.127588, "limit_usd": 14, "current_total_usd": 34.762072, "spent_usd": 7.634484000000004, "requests": 2555, "checked_at": "2026-10-28T06:18:27.564327+00:00", "account_limit_cents": 4900, "request_limit": 10000, "in_flight_reserve_usd": 4.0, "max_in_flight": 8, "per_request_reserve_usd": 0.5}
{"registered_dialogues": 100, "logical_inputs": 1654, "pending_unique": 693, "reused_logical": 960, "projected_new_usd": 0.9517953103448252, "historical_out_of_sample": "retained for provenance, never requested again"}
新增子样本账单：{"start_total_usd": 32.98023, "shared_account": true, "current_total_usd": 34.762072, "spent_usd": 1.7818420000000046}
逐阶段实际用量（缺失usage不冒充零）：{"judgments.jsonl": {"responses": 1152, "status_counts": {"ok": 1137, "request_error": 7, "parse_error": 8}, "reused_inputs": 0, "tokens": {"prompt_tokens": 864362, "completion_tokens": 272830, "total_tokens": 1137192}, "unknown_usage": 7}, "gemini_sample.jsonl": {"responses": 1403, "status_counts": {"ok": 1398, "request_error": 4, "abandoned_in_flight": 1}, "reused_inputs": 0, "tokens": {"prompt_tokens": 1051894, "completion_tokens": 317298, "total_tokens": 1369192}, "unknown_usage": 5}, "spark_judgments.jsonl": {"responses": 14271, "status_counts": {"ok": 14178, "request_error": 90, "parse_error": 3}, "reused_inputs": 2977, "tokens": {"prompt_tokens": 7721043, "completion_tokens": 6694573, "total_tokens": 14415616}, "unknown_usage": 90}}
C证据：{"adoption_passed": false, "authorization": {"decision": "adopt_ppi_post_hoc", "decided_by": "user", "recorded_by": "Claude", "date": "2026-10-28", "adr": "docs/architecture.md ADR-018", "c_preregistered_result": "not met (coverage passed in all cells; width reduction 16.7% < 20% in reaction/full nonnegative emotion)", "scope": "Contract A may send the preregistered 100-dialogue Gemini sample and report PPI estimates; report must state the post-hoc adoption."}, "authorization_sha256": "2590aaa0e7c8871329a23e6721e66e01f4973dd54d27388ccb0943bfc5d81b37", "status": "complete", "summary_sha256": "5d173a17c431a478a861af42c3c5f0918b65e8a2c7764a321c2f4bcce643c601", "preregistration": {"adoption_n": 100, "bootstrap": "paired dialogue percentile bootstrap; Spark population fixed", "bootstrap_replicates": 2000, "coverage_min": 0.9, "median_width_reduction_min": 0.2, "parse_failure_stop": 0.02, "repetitions": 500, "sample_dialogues": [50, 100, 200], "seed": 20261027, "selection": "highest development topic kappa; tie: disabled"}}
