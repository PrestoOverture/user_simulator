# 空开场校验修复

structure/4293:1首次HTTP200返回空文本，usage为117输入、1输出token。该轮start=0、无客服前文，按原judge_inputs口径本来就排除。保留原生成和请求记录，不重跑、不新增排除规则。校验器仅允许既定不进入判断的开场轮为空，正式判断轮次为空仍停止；单测覆盖两种情况。旧停止日志在archive_empty_opening_stop/。
