# ADR-016 判断器验证

provider=sandbox；model=gemini-3.8-flash；temperature 使用官方默认（省略参数）；接受 Gemini 默认推理。

种子 20261026；共同有效的配对轮次池；仅抽取客服已经回应的轮次。六个来源×负面分层各50条；开发100、测试200，按对话隔离。最近6条真实消息为严格轮前历史。
标注仅阅读 annotation_view.json，来源与负面分组另存 source_map.json；300条人工式标注及理由在首次请求前冻结，SHA-256 见 annotation_freeze.json。单一 Codex 标注者。
读取超时允许同一输入必要重试一次，原请求保留，缺失usage单列；不重新评估已成功样本。解析失败在同一输入上重试一次；仍失败记 parse_error，计入准确率分母和混淆矩阵错误列。κ以 parse_error 为仅预测侧类别计算。
开发集共评估1版 prompt，定稿为v1；5条试跑计入v1开发集100条内。测试只对定稿版本评估一遍，缓存重建不发请求。

## prompt_v1

样本 100；实际请求 100；解析失败响应 0；最终 parse_error 0。
prompt SHA-256：87d1d40d05a09382cc9a1c47e0963e40a8e0cbe7e5c3e5cb9e00ce50eed1ee8d

|指标|准确率|Cohen κ|
|---|---:|---:|
|topic|94.00%|0.839915|
|emotion|97.00%|0.885671|

### topic 混淆矩阵（行=冻结标注，列=Gemini）

|标注\预测|close|continue|unclear|parse_error|
|---|---:|---:|---:|---:|
|close|15|1|0|0|
|continue|3|74|0|0|
|unclear|0|2|5|0|

### emotion 混淆矩阵（行=冻结标注，列=Gemini）

|标注\预测|False|True|parse_error|
|---|---:|---:|---:|
|False|83|3|0|
|True|0|14|0|

### 分组一致率

|分组|n|topic|emotion|两个标签均一致|
|---|---:|---:|---:|---:|
|human|34|91.18%|97.06%|88.24%|
|spark|34|97.06%|100.00%|97.06%|
|deepseek|32|93.75%|93.75%|87.50%|
|negative=False|50|92.00%|96.00%|88.00%|
|negative=True|50|96.00%|98.00%|94.00%|
|human/negative=False|17|88.24%|94.12%|82.35%|
|human/negative=True|17|94.12%|100.00%|94.12%|
|spark/negative=False|17|94.12%|100.00%|94.12%|
|spark/negative=True|17|100.00%|100.00%|100.00%|
|deepseek/negative=False|16|93.75%|93.75%|87.50%|
|deepseek/negative=True|16|93.75%|93.75%|87.50%|

已返回的实际 token 用量：{'prompt_tokens': 76236, 'completion_tokens': 19730, 'total_tokens': 95966, 'reasoning_tokens': 18826}。未返回 usage 的请求：0（读取超时）；不把缺失用量记为0。reasoning_tokens 是 completion_tokens 的子项，不重复加总。

### 分歧例子

每个指标按盲ID升序取前3个分歧，非额外标注或改标。

- topic / J169：客服「目前的促销活动只有购买[数字]-[数字]件时享受单件价￥[数字]」；用户「然后刚才我点立即抢购，然后变购物车了，该怎么购买呢。对了，你们这是京东自营吗？能用支付宝支付吗？」；标注=continue；Gemini={'topic': 'close', 'emotion': False}。冻结理由：在同次购买中追问下单操作及支付；无情绪。
- topic / J176：客服「我看下」；用户「返修的时候有装耳机的盒子」；标注=unclear；Gemini={'topic': 'continue', 'emotion': False}。冻结理由：只补充返修时包装盒信息，未提出要求；无情绪。
- topic / J201：客服「具体时间无法保证哦」；用户「那等到有货了，你们一般发什么快递啊？」；标注=continue；Gemini={'topic': 'close', 'emotion': False}。冻结理由：继续问同一缺货商品的发货快递；无情绪。
- emotion / J041：客服「无法保证具体时间呢亲亲」；用户「那它为啥一直不动啊？是丢件了还是卡在哪个环节了？」；标注=False；Gemini={'topic': 'continue', 'emotion': True}。冻结理由：继续追问物流停滞原因；无明确情绪表达。
- emotion / J189：客服「亲爱哒，这边应该是空气温度比较高凝固了呢」；用户「那现在怎么办啊 我真的打不开 用不了啊  / 能给我换一个吗」；标注=False；Gemini={'topic': 'continue', 'emotion': True}。冻结理由：强调无法打开并要求换货；本轮未明确表达情绪，不沿用历史郁闷。
- emotion / J257：客服「您好，看您购买的商品也是咱们的热销商品之一，建议您保留或者可以赠送他人呢」；用户「不能保留，也不能赠人只能退怎么退?我买的s码怎么留着」；标注=False；Gemini={'topic': 'continue', 'emotion': True}。冻结理由：拒绝保留或送人的办法，坚持退货；未明确说出负面情绪。
## test_v1

样本 200；实际请求 201；解析失败响应 0；最终 parse_error 0。
prompt SHA-256：87d1d40d05a09382cc9a1c47e0963e40a8e0cbe7e5c3e5cb9e00ce50eed1ee8d

|指标|准确率|Cohen κ|
|---|---:|---:|
|topic|97.00%|0.929370|
|emotion|97.00%|0.885562|

### topic 混淆矩阵（行=冻结标注，列=Gemini）

|标注\预测|close|continue|unclear|parse_error|
|---|---:|---:|---:|---:|
|close|36|0|0|0|
|continue|2|144|1|0|
|unclear|2|1|14|0|

### emotion 混淆矩阵（行=冻结标注，列=Gemini）

|标注\预测|False|True|parse_error|
|---|---:|---:|---:|
|False|166|5|0|
|True|1|28|0|

### 分组一致率

|分组|n|topic|emotion|两个标签均一致|
|---|---:|---:|---:|---:|
|human|66|95.45%|98.48%|93.94%|
|spark|66|98.48%|98.48%|96.97%|
|deepseek|68|97.06%|94.12%|91.18%|
|negative=False|100|96.00%|100.00%|96.00%|
|negative=True|100|98.00%|94.00%|92.00%|
|human/negative=False|33|93.94%|100.00%|93.94%|
|human/negative=True|33|96.97%|96.97%|93.94%|
|spark/negative=False|33|96.97%|100.00%|96.97%|
|spark/negative=True|33|100.00%|96.97%|96.97%|
|deepseek/negative=False|34|97.06%|100.00%|97.06%|
|deepseek/negative=True|34|97.06%|88.24%|85.29%|

已返回的实际 token 用量：{'prompt_tokens': 153273, 'completion_tokens': 36335, 'total_tokens': 189608, 'reasoning_tokens': 34520}。未返回 usage 的请求：1（读取超时）；不把缺失用量记为0。reasoning_tokens 是 completion_tokens 的子项，不重复加总。

### 分歧例子

每个指标按盲ID升序取前3个分歧，非额外标注或改标。

- topic / J013：客服「有什么问题我可以帮您处理或解决呢?」；用户「你好，我刚叫了你们京东的上门取件，结果师傅说他不负责我这片，我这手机急着修呢，这可咋整啊？」；标注=continue；Gemini={'topic': 'unclear', 'emotion': True}。冻结理由：在客服询问后不仅说明问题还要求解决；明确说急着修。
- topic / J018：客服「您耐心等待回复就可以了，亲」；用户「好，那我等短信就行是吧？」；标注=continue；Gemini={'topic': 'close', 'emotion': False}。冻结理由：仍用问句核对等待短信的操作；无负面情绪。
- topic / J037：客服「有您这样的客户真是我们的荣幸」；用户「还有啊，我家是加在5层的，那边人员可以安装吗？」；标注=continue；Gemini={'topic': 'close', 'emotion': False}。冻结理由：就同一送装事项增加五层安装问题；无负面情绪。
- emotion / J001：客服「亲亲退货之后不能开发票的」；用户「那退款到底什么时候能到账啊   / 专员什么时候能处理完   / 审核一般要多久」；标注=False；Gemini={'topic': 'continue', 'emotion': True}。冻结理由：仍追问退款、审核时间；没有明确负面情绪词。
- emotion / J148：客服「这边查到您的鞋子没有标签了，是不支持换货的了哦」；用户「啊？没有标签就不支持换货了吗？可是鞋子我确实没看到标签啊，我拿到手就是这样。」；标注=False；Gemini={'topic': 'continue', 'emotion': True}。冻结理由：反问标签限制并解释没有标签，继续换货诉求；没有明确负面情绪。
- emotion / J167：客服「很抱歉，您的售后小妹是无法调度修改的呢，可以的话，小妹就直接处理，就不会为您升级专员处理的呢」；用户「好吧，那我先把货款支付了，但你们一定要快点处理啊，别让孩子等太久！」；标注=False；Gemini={'topic': 'continue', 'emotion': True}。冻结理由：接受转专员同时要求快处理；只是催促而未明确表达情绪。

## 测试集门槛

话题走向 κ≥0.70 且准确率≥85%：达标。
情绪表达 κ≥0.70：达标。
总体：达标。

全部版本和测试已返回的实际 token 总量：{'prompt_tokens': 229509, 'completion_tokens': 56065, 'total_tokens': 285574, 'reasoning_tokens': 53346}；另有 1 次请求未返回 usage，完整计费总量未知。

本报告仅验证判断器与冻结标准集的一致性，未运行全量行为测量。
