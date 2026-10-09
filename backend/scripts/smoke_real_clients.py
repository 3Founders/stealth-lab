"""Real coding clients (OpenCode, Codex CLI, Cline) against our OpenAI-compatible endpoints, with a fake chat-completions provider behind them.
Hand-run probe, not part of pytest. No keys, no network beyond loopback; the clients must be installed on PATH.
usage: python backend/scripts/smoke_real_clients.py <opencode|codex|cline> [cline-provider-id]
       SCENARIO=tool  makes the fake provider ask the client to run a shell command, then answer from its result."""
import os, json, socket, subprocess, sys, tempfile, threading, time
WT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, WT)
os.environ.pop("DATABASE_URL", None); os.environ["ENVIRONMENT"] = "TEST"
import httpx, uvicorn
from fastapi import FastAPI
from app.api import chat_completions as api
from app.api.deps import enforce_limits
from app.providers import adapters, registry
from app.providers.types import Connection, UnitSpec

client = sys.argv[1]
seen = []

def chunk(delta=None, finish=None, usage=None):
    obj = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if (delta is not None or finish) else []}
    if usage: obj["usage"] = usage
    return "data: " + json.dumps(obj) + "\n\n"

def handler(request):
    body = json.loads(request.content)
    tools = [(t.get("function") or {}).get("name") for t in body.get("tools") or []]
    seen.append({"stream": body.get("stream"), "model": body.get("model"), "n_msgs": len(body["messages"]), "roles": [m["role"] for m in body["messages"]], "tools": tools[:8], "n_tools": len(tools)})
    scenario = os.environ.get("SCENARIO")
    if scenario == "tool":
        prefer = {"opencode": "bash", "codex": "exec_command", "cline": "run_commands"}[client]
        tool = next((t for t in body.get("tools") or [] if (t.get("function") or {}).get("name") == prefer), None)
        tool_msgs = [m for m in body["messages"] if m["role"] == "tool"]
        if tool and not tool_msgs:
            props = (tool["function"].get("parameters") or {}).get("properties") or {}
            req = (tool["function"].get("parameters") or {}).get("required") or list(props)
            def fill(name, spec):
                kind = spec.get("type")
                if kind == "array": return ["echo TOOL-OK"]
                if kind == "string": return "echo TOOL-OK" if ("command" in name or "cmd" in name) else "run echo"
                if kind in ("integer", "number"): return 5000
                if kind == "boolean": return False
                return {}
            args = {n: fill(n, props.get(n, {})) for n in req}
            seen[-1]["tool_call"] = {"name": prefer, "args": args}
            tc = {"index": 0, "id": "call_smoke_1", "type": "function", "function": {"name": prefer, "arguments": json.dumps(args)}}
            if body.get("stream"):
                return httpx.Response(200, content=chunk({"role": "assistant", "tool_calls": [tc]}) + chunk({}, "tool_calls") + chunk(None, None, {"prompt_tokens": 20, "completion_tokens": 8}) + "data: [DONE]\n\n")
            return httpx.Response(200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [{**tc, "index": None}]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 20, "completion_tokens": 8}})
        if tool_msgs:
            seen[-1]["tool_result"] = str(tool_msgs[-1].get("content"))[:200]
            text = "TOOL-LOOP-OK"
        else:
            text = "SMOKE-OK from the routed model"
    else:
        text = "SMOKE-OK from the routed model"
    usage = {"prompt_tokens": 9, "completion_tokens": 6}
    if body.get("stream"):
        return httpx.Response(200, content=chunk({"role": "assistant", "content": text}) + chunk({}, "stop") + chunk(None, None, usage) + "data: [DONE]\n\n")
    return httpx.Response(200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": usage})

os.environ["SMOKE_KEY"] = "sk-smoke"
orig = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))] if host == "smoke.example.com" else orig(host, *a, **k)
conn = Connection(connection_id="smoke", kind="openai_compatible", base_url="https://smoke.example.com/v1", provider="vendor", credential_ref="env:SMOKE_KEY",
                  allowed_data_classes=("USER_PRIVATE",), units=(UnitSpec(model="glm-smoke", provider_model="vendor/glm-smoke", input_per_mtok=1.0, output_per_mtok=2.0),))
registry._STORES = {"t": registry.StaticConnectionStore([conn])}
transport = httpx.MockTransport(handler)
adapters.http_client = lambda **kw: httpx.AsyncClient(transport=transport, **kw)

app = FastAPI(); app.include_router(api.router); app.state.pool = None
app.dependency_overrides[enforce_limits] = lambda: "smoke"

@app.middleware("http")
async def log(request, call_next):
    body = await request.body()
    resp = await call_next(request)
    keys = sorted(json.loads(body).keys()) if body and body[:1] == b"{" else None
    print("REQ", request.method, request.url.path, "->", resp.status_code, "keys", keys, flush=True)
    if resp.status_code >= 400:
        data = b"".join([c async for c in resp.body_iterator])
        print("ERRBODY", data[:300], flush=True)
        from starlette.responses import Response
        return Response(data, status_code=resp.status_code, headers=dict(resp.headers))
    return resp

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(50):
    try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
    except OSError: time.sleep(0.1)

work = tempfile.mkdtemp(prefix="smoke-work-")
env = dict(os.environ)
base = f"http://127.0.0.1:{port}/v1"
if client == "opencode":
    cfg = {"$schema": "https://opencode.ai/config.json", "provider": {"stealth-open": {"npm": "@ai-sdk/openai-compatible", "name": "StealthLab", "options": {"baseURL": base, "apiKey": "{env:OPEN_MODEL_API_KEY}"}, "models": {"glm-smoke": {"name": "glm-smoke"}}}}}
    env.update({"OPENCODE_CONFIG_CONTENT": json.dumps(cfg), "OPEN_MODEL_API_KEY": "local-test-token"})
    cmd = ["opencode", "run", "--format", "json", "--dir", work, "--model", "stealth-open/glm-smoke", "say hello"]
elif client == "codex":
    home = tempfile.mkdtemp(prefix="smoke-codex-home-")
    open(os.path.join(home, "config.toml"), "w").write(f'model = "glm-smoke"\nmodel_provider = "stealth"\n\n[model_providers.stealth]\nname = "stealth"\nbase_url = "{base}"\nenv_key = "SMOKE_TOKEN"\nwire_api = "responses"\n')
    env.update({"CODEX_HOME": home, "SMOKE_TOKEN": "local-test-token"})
    cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--cd", work, "say hello"]
elif client == "cline":
    data = tempfile.mkdtemp(prefix="smoke-cline-data-"); cfgdir = tempfile.mkdtemp(prefix="smoke-cline-cfg-")
    provider = sys.argv[2] if len(sys.argv) > 2 else "openai"
    a = subprocess.run(["cline", "auth", "-p", provider, "-k", "local-test-token", "-m", "glm-smoke", "-b", base, "--data-dir", data, "--config", cfgdir, "-c", work], env=env, cwd=work, capture_output=True, text=True, timeout=60, shell=(os.name == "nt"), stdin=subprocess.DEVNULL)
    print("AUTH EXIT", a.returncode, (a.stdout + a.stderr)[-300:], flush=True)
    cmd = ["cline", "--json", "-P", provider, "-m", "glm-smoke", "-k", "local-test-token", "--data-dir", data, "--config", cfgdir, "-c", work, "say hello"]
else:
    raise SystemExit("unknown client")
print("CMD", " ".join(cmd), flush=True)
try:
    r = subprocess.run(cmd, env=env, cwd=work, capture_output=True, text=True, timeout=75, shell=(os.name == "nt"), stdin=subprocess.DEVNULL)
    print("EXIT", r.returncode)
    print("STDOUT", r.stdout[-900:])
    print("STDERR", r.stderr[-500:])
except subprocess.TimeoutExpired as e:
    print("TIMEOUT", (e.stdout or b"")[-400:], (e.stderr or b"")[-400:])
print("PROVIDER SAW", json.dumps(seen))
server.should_exit = True
