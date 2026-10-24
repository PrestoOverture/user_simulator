"""Only the real prefix before the target user turn enters history."""
import re
from .csds import clean_summary


def unsegment(text):
    return re.sub(r"(?<=[\u4e00-\u9fff])[^\S\r\n]+(?=[\u4e00-\u9fff])", "", text)

SYSTEM = '你是一位在京东购物后联系售后客服的普通顾客。'
INSTRUCTION = '只输出顾客的下一句话；使用自然、口语化的中文聊天风格；如果要连发多条消息，用换行分隔。'


def prompt(row, turn):
    goal, _ = clean_summary(row['UserSumm'], row.get('DialogueID'))
    history = '\n'.join(('顾客' if u['speaker'] == 'Q' else '客服') + '：' + unsegment(u['utterance'])
                        for u in row['Dialogue'][:turn['start']])
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': f'任务目标：{unsegment(goal)}\n对话历史：\n{history}\n{INSTRUCTION}'}]


def template():
    return f'''# 完整 prompt 模板\n\nsystem: {SYSTEM}\n\nuser:\n任务目标：{{清除结果后的 UserSumm}}\n对话历史：\n{{第t用户轮次起始消息之前的全部真实消息；每条以顾客：或客服：开头}}\n{INSTRUCTION}\n\n模型 spark-x2.6；thinking={{"type":"disabled"}}；temperature 不传（官方默认1），stream=false。不传 max_tokens。每轮仅取一次成功生成，传输/API失败指数退避重试，不为空输出重生成。历史不使用模拟器输出。历史和目标仅删除两个中文字符之间的横向空白，保留换行、英文/数字间空白（ADR-014）。HTTP400且error.code=10013标为moderated且不重试，两侧配对剔除（ADR-013）。摘要使用逐条人工核对与覆盖（ADR-015）。\n\n官方文档：https://maas.xfyun.cn/doc/guide/3、API%20接口/3.1%20星火模型%20API/3.1.1%20Chat.html\n'''
