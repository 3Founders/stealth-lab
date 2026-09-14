import os
import time
import json
from dotenv import load_dotenv

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")
import httpx

key = os.environ["GENERAL_COMPUTE_API_KEY"]
base = os.environ["GENERAL_COMPUTE_BASE_URL"]
start = time.time()
resp = httpx.post(
    f"{base}/chat/completions",
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    json={
        "model": "deepseek-v3.1",
        "messages": [
            {"role": "user", "content": 'Reply with exactly the JSON object {"ok": true} and nothing else.'}
        ],
        "max_tokens": 200,
    },
    timeout=30,
)
print("status:", resp.status_code)
print("latency_s:", round(time.time() - start, 3))
print(json.dumps(resp.json(), indent=2)[:1200])
