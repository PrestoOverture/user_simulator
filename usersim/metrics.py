import json
import re
from difflib import SequenceMatcher
from pathlib import Path

KEYWORDS = json.loads((Path(__file__).resolve().parents[1] / 'config/keywords.json').read_text())


def compact(text):
    return re.sub(r'\s+', '', text)


def measure(text, previous, first, messages, source="simulator"):
    s = compact(text)
    count = sum(c.isascii() and c.isalnum() or '\u4e00' <= c <= '\u9fff' for c in s)
    result = dict(char_count=count, short=int(count <= 5), message_count=messages,
                  repeated=int(any(SequenceMatcher(None, s, compact(p)).ratio() > .6 for p in previous)),
                  first_char_count=count if first else '',
                  first_identifier=int('[订单编号]' in s or '[数字]' in s or (source == 'simulator' and bool(re.search(r'\d{6,}', text)))) if first else '')
    for name in ('politeness', 'escalation', 'refusal', 'urgency', 'correction'):
        candidate = s
        if name == 'politeness':
            for excluded in KEYWORDS.get('politeness_exclude', []):
                candidate = candidate.replace(excluded, '')
        result[name] = int(any(k in candidate for k in KEYWORDS[name]))
    result['correction'] |= int(bool(re.search(KEYWORDS['correction_regex'], s)))
    return result
