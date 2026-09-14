import os
import time
import json
from dotenv import load_dotenv

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")
import httpx

key = os.environ["GEMINI_API_KEY"]
start = time.time()
resp = httpx.post(
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    json={
        "model": "gemini-3.8-flash",
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
