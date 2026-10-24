"""Spark only, no secret-bearing response bodies in logs."""
import asyncio
import json
import os
import time
from pathlib import Path
import httpx

URL = 'https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions'


def api_key():
    key = os.environ.get('SPARK_API_KEY')
    if not key:
        raise RuntimeError('SPARK_API_KEY missing')
    return key


class Spark:
    def __init__(self, out, concurrency=4):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.sem = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(timeout=120, headers={'Authorization': 'Bearer ' + api_key()})
        self.secret = api_key()
        self.records = []
        self.failed = False

    async def close(self):
        await self.client.aclose()

    async def generate(self, messages, request_id):
        async with self.sem:
            if self.failed:
                raise RuntimeError('Stopped after exhausted Spark request')
            for attempt in range(4):
                start = time.monotonic()
                record = dict(request_id=request_id, attempt=attempt+1, input_tokens=0, output_tokens=0)
                try:
                    response = await self.client.post(URL, json=dict(model='spark-x2.6', messages=messages,
                                                                  thinking={'type': 'disabled'}, stream=False))
                    record['http_status'] = response.status_code
                    if response.is_error:
                        try:
                            error = response.json().get('error', {})
                        except ValueError:
                            error = {}
                        if isinstance(error, dict):
                            record['error_code'] = error.get('code')
                            record['error_message'] = str(error.get('message', '')).replace(self.secret, '[REDACTED]')
                        if response.status_code == 400 and str(record.get('error_code')) == '10013':
                            record['status'] = 'moderated'
                            record['usage_known'] = False
                            return None
                    response.raise_for_status()
                    data = response.json()
                    usage = data['usage']
                    record.update(input_tokens=usage['prompt_tokens'], output_tokens=usage['completion_tokens'])
                    content = data['choices'][0]['message']['content'] or ''
                    if not isinstance(content, str):
                        raise ValueError('Invalid content')
                    record['status'] = 'ok'
                    record['usage_known'] = True
                    return content
                except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
                    record.update(status='error', error_type=type(exc).__name__, usage_known=False)
                    if attempt == 3:
                        self.failed = True
                        raise RuntimeError(f'Spark failed after 4 attempts: {request_id}; see request_usage.jsonl') from None
                except asyncio.CancelledError:
                    record.update(status='cancelled', usage_known=False)
                    raise
                finally:
                    record['elapsed_seconds'] = time.monotonic()-start
                    self.records.append(record)
                    with (self.out / 'request_usage.jsonl').open('a') as f:
                        f.write(json.dumps(record, ensure_ascii=False)+'\n')
                await asyncio.sleep(2 ** attempt)
