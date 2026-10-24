import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim.spark import Spark


async def main():
    results = []
    out = Path('outputs')
    client = Spark(out, 8)
    try:
        for concurrency, count in [(1, 1), (2, 4), (4, 8), (8, 16)]:
            client.sem = asyncio.Semaphore(concurrency)
            start = time.monotonic()
            before = len(client.records)
            await asyncio.gather(*(client.generate([{'role': 'user', 'content': '请只回复“你好”。'}], f'check-{concurrency}-{i}') for i in range(count)))
            duration = time.monotonic()-start
            records = client.records[before:]
            results.append(dict(concurrency=concurrency, requests=count, seconds=duration,
                                requests_per_minute=count/duration*60,
                                errors=sum(r['status']=='error' for r in records),
                                statuses=[r.get('http_status') for r in records]))
            print(json.dumps(results[-1]), flush=True)
    finally:
        await client.close()
        (out/'spark_check_results.json').write_text(json.dumps(results, indent=2))
        text = '# Spark 连通性和并发实测\n\n2026-10-25；模型 spark-x2.6；thinking={"type":"disabled"}；temperature 不传，官方默认1（固定值，建议省略）；stream=false。\n\n官方接口与参数文档：https://maas.xfyun.cn/doc/guide/3、API%20接口/3.1%20星火模型%20API/3.1.1%20Chat.html\n\n|并发|成功逻辑请求数|耗时秒|观测请求/分钟|失败尝试|HTTP状态|\n|---|---|---|---|---|---|\n'
        for r in results:
            text += f"|{r['concurrency']}|{r['requests']}|{r['seconds']:.2f}|{r['requests_per_minute']:.2f}|{r['errors']}|{r['statuses']}|\n"
        text += '\n这是短回复、短批次的观测吞吐，不是账户RPM/TPM额度或持续压测。未测得官方限速阈值；后续采用并发4，失败指数退避最多4次，耗尽即停。不查询使用条款。免费额度按用户提供的10亿token记录，剩余额度、有效期未核验。逐请求token见 request_usage.jsonl（check 前缀）。\n'
        (out/'spark_check.md').write_text(text)

if __name__ == '__main__':
    asyncio.run(main())
