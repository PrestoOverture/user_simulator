"""Train-only situation retrieval and independently sampled turn structure."""
from collections import Counter, defaultdict
import hashlib
import math
import random
import re

from .csds import user_turns
from .replay import prompt, unsegment

SEED = 20261027
CONDITIONS = ('situation', 'structure', 'combined')


def previous_agent(row, turn):
    return next((unsegment(m['utterance']) for m in reversed(row['Dialogue'][:turn['start']])
                 if m['speaker'] != 'Q'), '')


def features(text):
    tokens = re.findall(r'[\u4e00-\u9fff]|[a-z0-9]+', text.lower())
    return Counter(tokens + [''.join(tokens[i:i+2]) for i in range(len(tokens)-1)])


class SituationPool:
    def __init__(self, rows, excluded):
        self.items = []
        for row in sorted(rows, key=lambda r: r['DialogueID']):
            if row['split'] != 'train' or row['QRole'] != '用户' or row['DialogueID'] in excluded:
                continue
            for t, turn in enumerate(user_turns(row), 1):
                agent = previous_agent(row, turn)
                text = unsegment('\n'.join(turn['messages']))
                self.items.append(dict(dialogue_id=row['DialogueID'], turn=t, agent=agent, text=text,
                                       chars=len(re.sub(r'\s', '', text)), messages=len(turn['messages'])))
        if len(self.items) < 5:
            raise ValueError('Insufficient independent training pool')
        df = Counter()
        bags = [features(r['agent']) for r in self.items]
        for bag in bags:
            df.update(bag.keys())
        self.idf = {k: math.log((1+len(bags))/(1+n))+1 for k,n in df.items()}
        self.index = defaultdict(list)
        for i, bag in enumerate(bags):
            vector = {k: (1+math.log(n))*self.idf[k] for k,n in bag.items()}
            norm = math.sqrt(sum(v*v for v in vector.values())) or 1
            for k,v in vector.items():
                self.index[k].append((i,v/norm))
        self.sha256 = hashlib.sha256(str(self.items).encode()).hexdigest()

    def plan(self, row, t, turn):
        # Only the strict prefix and stable ID enter retrieval/control sampling.
        agent = previous_agent(row, turn)
        bag = features(agent)
        vector = {k:(1+math.log(n))*self.idf[k] for k,n in bag.items() if k in self.idf}
        norm = math.sqrt(sum(v*v for v in vector.values())) or 1
        scores = defaultdict(float)
        for k,v in vector.items():
            for i,w in self.index[k]:
                scores[i] += v/norm*w
        best = sorted(scores, key=lambda i:(-scores[i],i))[:5]
        fallback = len(best) < 5
        if fallback:
            rng = random.Random(f'{SEED}:retrieval:{row["DialogueID"]}:{t}')
            available = [i for i in range(len(self.items)) if i not in best]
            best += rng.sample(available,5-len(best))
        rng = random.Random(f'{SEED}:structure:{row["DialogueID"]}:{t}')
        structure = self.items[rng.randrange(len(self.items))]
        return dict(request_id=f'{row["DialogueID"]}:{t}', query=agent,
                    examples=[dict(self.items[i], score=scores.get(i,0)) for i in best],
                    fallback=fallback,
                    structure={k:structure[k] for k in ('dialogue_id','turn','chars','messages')})


def corrected_prompt(row, turn, plan, condition):
    if condition not in CONDITIONS:
        raise ValueError('Unknown correction')
    messages = prompt(row, turn)
    extra = []
    if condition in ('situation','combined'):
        extra.append('以下是其他真实售后对话中，客服回应与顾客后续回复的示例。参考相似情境下的回应方式，不要照搬订单、商品或事实：')
        extra.extend(f'示例{i} 客服：{a["agent"] or "（尚未发言）"}\n顾客：{a["text"]}'
                     for i,a in enumerate(plan['examples'],1))
    if condition in ('structure','combined'):
        s = plan['structure']
        extra.append(f'本轮说话结构：总长度目标约{s["chars"]}个非空白字符；连发{s["messages"]}条消息，各条用换行分隔。保持符合当前情境，不添加无依据的事实。')
    messages[1]['content'] = '\n'.join(extra)+'\n'+messages[1]['content']
    return messages
