import json
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, r"C:\Users\user\stealth-lab\backend\scripts")
import load_test_local as lt  # noqa: E402

dsn = "postgresql://kel_owner@127.0.0.1:55432/kel_v2_isolation"
env = lt._env(dsn)
port = lt._free_port()
proc = subprocess.Popen([sys.executable, "-c", lt.SERVER, str(port)], cwd=lt.BACKEND, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
log = []
threading.Thread(target=lambda: [log.append(x.rstrip()) for x in proc.stdout], daemon=True).start()
base = f"http://127.0.0.1:{port}"
try:
    for _ in range(240):
        try:
            with urllib.request.urlopen(base + "/readyz", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.5)
    import httpx
    for body in ({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "find_ways", "arguments": {"query": "fix the billing webhook", "use_llm": False}}},):
        r = httpx.post(base + "/mcp", json=body, timeout=60, headers={
            "accept": "application/json, text/event-stream", "content-type": "application/json",
            "mcp-protocol-version": "2025-06-18", "authorization": "Bearer " + __import__("dotenv").dotenv_values(r"C:\Users\user\stealth-lab\backend\.env").get("STEALTHLAB_MCP_TOKEN", "")})
        print(r.status_code, dict(r.headers).get("content-type"))
        print(r.text[:1500])
finally:
    proc.kill()
    print("\n".join(log[-15:]))
