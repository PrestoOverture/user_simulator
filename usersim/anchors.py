"""Deterministic train-only quotation anchors for the correction pilot."""
import random
from .csds import user_turns
from .replay import prompt, unsegment, template

LABEL = '真实顾客说话示例，仅供参考说话方式'


def intent(row):
    return row['QA'][0]['intent']


def pool(rows, excluded):
    return [dict(dialogue_id=r['DialogueID'], turn=t, intent=intent(r),
                 text=unsegment('\n'.join(u['messages'])))
            for r in rows if r['split'] == 'train' and r['QRole'] == '用户'
            and r['DialogueID'] not in excluded
            for t, u in enumerate(user_turns(r), 1)]


def select(pool_items, target, condition, seed):
    rng = random.Random(f"{seed}:{condition}:{target['DialogueID']}")
    target_texts = {unsegment('\n'.join(t['messages'])) for t in user_turns(target)}
    eligible = [a for a in pool_items if a['dialogue_id'] != target['DialogueID'] and a['text'] not in target_texts]
    same = [a for a in eligible if a['intent'] == intent(target)]
    chosen = rng.sample(same, min(5, len(same))) if condition == 'C2' else rng.sample(eligible, 5)
    result = [dict(a, fallback=False) for a in chosen]
    if len(result) < 5:
        remaining = [a for a in eligible if a not in chosen]
        result += [dict(a, fallback=True) for a in rng.sample(remaining, 5-len(result))]
    return dict(dialogue_id=target['DialogueID'], intent=intent(target), examples=result)


def anchored_prompt(row, turn, anchor):
    messages = prompt(row, turn)
    prefix = LABEL + '：\n' + '\n'.join(f"{i}. {a['text']}" for i, a in enumerate(anchor['examples'], 1)) + '\n'
    messages[1]['content'] = prefix + messages[1]['content']
    return messages


def anchor_template():
    examples = '\n'.join(f'{i}. {{示例{i}原文；连发消息保留换行}}' for i in range(1, 6))
    return template().replace('任务目标：{', LABEL + '：\n' + examples + '\n任务目标：{', 1)
