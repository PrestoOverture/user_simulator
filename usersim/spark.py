"""Configurable OpenAI-compatible replay client; credentials stay in environment."""
import asyncio
import json
import os
import time
from pathlib import Path
import httpx

URL = 'https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions'


def api_key(provider="spark"):
    name = "SPARK_API_KEY" if provider == "spark" else "SANDBOX_API_KEY"
    key = os.environ.get(name)
    if not key:
        raise RuntimeError(f'{name} missing')
    return key


class Spark:
    def __init__(self, out, concurrency=4, provider="spark", model=None, transport=None):
        if provider not in ("spark", "sandbox"):
            raise ValueError("Unknown provider")
        if provider == "spark" and model not in (None, "spark-x2.6"):
            raise ValueError("Spark credentials only support spark-x2.6")
        if provider == "sandbox" and not model:
            raise ValueError("sandbox requires model")
        self.provider = provider
        self.model = model or "spark-x2.6"
        self.url = URL if provider == "spark" else os.environ["SANDBOX_BASE_URL"].rstrip("/") + "/v1/chat/completions"
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.sem = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(timeout=120, headers={'Authorization': 'Bearer ' + api_key(provider)}, transport=transport)
        self.secret = api_key(provider)
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
                record = dict(request_id=request_id, attempt=attempt+1, input_tokens=0, output_tokens=0, reasoning_tokens=0, provider=self.provider, model=self.model)
                try:
                    payload = dict(model=self.model, messages=messages, stream=False)
                    if self.provider == 'spark':
                        payload['thinking'] = {'type': 'disabled'}
                    elif self.provider == 'sandbox' and self.model.startswith('deepseek'):
                        payload['thinking'] = {'type': 'disabled'}
                    if self.provider == 'sandbox':
                        record['thinking'] = payload.get('thinking', 'omitted')
                    response = await self.client.post(self.url, json=payload)
                    record['http_status'] = response.status_code
                    if response.is_error:
                        try:
                            error = response.json().get('error', {})
                        except ValueError:
                            error = {}
                        if isinstance(error, dict):
                            record['error_code'] = error.get('code')
                            record['error_message'] = str(error.get('message', '')).replace(self.secret, '[REDACTED]')
                        if self.provider == 'spark' and response.status_code == 400 and str(record.get('error_code')) == '10013':
                            record['status'] = 'moderated'
                            record['usage_known'] = False
                            return None
                    response.raise_for_status()
                    data = response.json()
                    usage = data['usage']
                    record.update(input_tokens=usage['prompt_tokens'], output_tokens=usage['completion_tokens'],
                                  reasoning_tokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens', usage.get('reasoning_tokens', 0)), usage=usage)
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
