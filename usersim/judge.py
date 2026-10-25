"""Sandbox Gemini judge with strict ADR-016 JSON and one parse retry."""
import json
import os
import threading
from pathlib import Path
import httpx

MODEL = 'gemini-3.8-flash'
PROVIDER = 'sandbox'
TOPICS = ('close', 'continue', 'unclear')


def parse(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=unique)
    if (not isinstance(value, dict) or set(value) != {'topic', 'emotion'}
            or value['topic'] not in TOPICS or type(value['emotion']) is not bool):
        raise ValueError('Invalid judge schema')
    return value


def messages(prompt, item):
    view = {k: item[k] for k in ('goal', 'history', 'previous_agent', 'text')}
    return [dict(role='system', content=prompt),
            dict(role='user', content=json.dumps(view, ensure_ascii=False))]


class Judge:
    def __init__(self, cache, transport=None):
        self.lock = threading.Lock()
        self.cache = Path(cache)
        self.secret = os.environ['SANDBOX_API_KEY']
        self.url = os.environ['SANDBOX_BASE_URL'].rstrip('/') + '/v1/chat/completions'
        self.client = httpx.Client(timeout=300, transport=transport,
                                  headers={'Authorization': 'Bearer ' + self.secret})

    def close(self):
        self.client.close()

    def record(self, row):
        text = json.dumps(row, ensure_ascii=False).replace(self.secret, '[REDACTED]')
        with self.lock:
            with self.cache.open('a') as f:
                f.write(text+'\n')
                f.flush()
                os.fsync(f.fileno())

    def evaluate(self, prompt, item, previous=()):
        attempts = [r for r in previous if r['id'] == item['id']]
        for old in attempts:
            if old['status'] == 'ok':
                return old['prediction']
        if len(attempts) == 2:
            return None
        for attempt in range(len(attempts)+1, 3):
            self.record(dict(id=item['id'], attempt=attempt, event='dispatch', model=MODEL, provider=PROVIDER))
            row = dict(id=item['id'], attempt=attempt, event='response', model=MODEL,
                       provider=PROVIDER, temperature='official default (omitted)', usage=None)
            try:
                response = self.client.post(self.url, json=dict(model=MODEL, messages=messages(prompt, item), stream=False))
                row['http_status'] = response.status_code
                response.raise_for_status()
                body = response.json()
                row['usage'] = body.get('usage')
                if not isinstance(row['usage'], dict) or not all(type(row['usage'].get(k)) is int for k in ('prompt_tokens','completion_tokens','total_tokens')):
                    raise RuntimeError('Missing actual token usage; stop')
                row['content'] = body['choices'][0]['message']['content']
                try:
                    row['prediction'] = parse(row['content'])
                except (ValueError, TypeError):
                    row['status'] = 'parse_error'
                else:
                    row['status'] = 'ok'
            except Exception as exc:
                row.update(status='request_error', error_type=type(exc).__name__)
                raise RuntimeError('Judge request failed; inspect cache, no automatic request retry') from None
            finally:
                self.record(row)
            if row['status'] == 'ok':
                return row['prediction']
        return None
