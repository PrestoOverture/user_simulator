"""Conditional human topic draws and label-only oracle control."""
from collections import Counter
import random
from usersim.behaviors import negative_agent
from usersim.correction_v2 import previous_agent
from usersim.replay import prompt

SEED = 20261027
CONDITIONS = ('decision', 'oracle')
TARGETS = {
    'close': '本轮你决定就此结束这个问题：接受或确认客服的答复（可以致谢），不再在这个问题上追问或提出新要求。',
    'continue': '本轮你决定在这个问题上继续：追问、表达不接受，或提出新的要求。',
    'unclear': '本轮只提供客服需要的信息或直接回答客服的问题，不表态是否接受。',
}


def group(row, turn):
    return 'negative' if negative_agent(previous_agent(row, turn)) else 'nonnegative'


class DecisionPool:
    def __init__(self, items, excluded):
        self.items = sorted((dict(r) for r in items if r['dialogue_id'] not in excluded),
                            key=lambda r: (r['dialogue_id'], r['turn']))
        self.counts = {g: dict(Counter(r['topic'] for r in self.items if r['group'] == g))
                       for g in ('negative', 'nonnegative')}
        if not all(sum(v.values()) for v in self.counts.values()):
            raise ValueError('Empty conditional human pool; stop')
        if any(r['topic'] not in TARGETS for r in self.items):
            raise ValueError('Invalid human label')

    def draw(self, row, turn, t):
        g = group(row, turn)
        counts = self.counts[g]
        rng = random.Random(f'{SEED}:decision:{row["DialogueID"]}:{t}')
        return rng.choices(list(TARGETS), weights=[counts.get(k, 0) for k in TARGETS])[0]


def corrected_prompt(row, turn, target):
    messages = prompt(row, turn)
    messages[1]['content'] = TARGETS[target] + '\n' + messages[1]['content']
    return messages


def budget_guard(total_usage_cents, requests):
    if total_usage_cents >= 4900:
        raise RuntimeError('Gateway total_usage >= 4900 cents; stop')
    if requests >= 10000:
        raise RuntimeError('Gemini request cap 10000 reached; stop')


def parse_judgment(text):
    """Only trim JSON field names; retain strict values and duplicate rejection."""
    import json
    from usersim.judge import parse
    def keys(pairs):
        result = {}
        for key,value in pairs:
            key = key.strip()
            if key in result:
                raise ValueError('Duplicate normalized JSON key')
            result[key] = value
        return result
    return parse(json.dumps(json.loads(text,object_pairs_hook=keys,strict=False),ensure_ascii=False))


def sample_dialogues(ids, n=100, seed=SEED):
    if len(ids)!=len(set(ids)):
        raise ValueError('Duplicate population IDs')
    return sorted(random.Random(seed).sample(sorted(ids),n))
