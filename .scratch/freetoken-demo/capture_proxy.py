"""
FreeToken capture proxy — the demo's trace-ingestion shim.

WHY THIS EXISTS
The capability demo runs a coding agent against a FREE local model served
by FreeToken (OpenAI-compatible, http://localhost:8000/v1). To prove
"earned memory" we need the agent's raw traffic captured into the VPES
ingestion pipeline WITHOUT modifying the agent. Cleanest hook point: a
transparent logging reverse proxy. The agent points base_url here; every
request/response pair is appended as ONE event line via the production
collector (backend/app/services/trace_collector.py) — inheriting its
redaction, dedup key, bounded-file, and lock-timeout behavior for free —
and trace_worker.py drains it into agent_traces/trace_events exactly like
Claude Code hook traffic. No new ingestion code paths.

WIRING (demo day)
  1. FreeToken serve qwen3.6-35b-a3b        (its own port, e.g. 8000)
  2. python capture_proxy.py                (this file; listens :8351)
  3. Agent config: OPENAI_BASE_URL=http://localhost:8351/v1
     (works unmodified with opencode / Codex CLI / any OpenAI client)
  4. Backend up: migrate chain 01->23; trace_worker running.
  5. Run task set -> Arm A traces accumulate. Distill -> procedures ->
     Arm B replays WITH mcp_server retrieve_precedent/check_procedure.

V0 GATE COMPLIANCE
Events are stamped producer provenance=public_generated (local open-weight
model output), scope_type=session, scope_entity_id=<run_id>. Rows entering
agent_traces carry the migration-21 scope columns; nothing implicit-global.

HONEST SCOPE
Scaffold quality: stdlib-only, non-streaming passthrough (agentic tool-call
turns tolerate this), no TLS, loopback bind by default. Not yet wired to a
live FreeToken process -- same honesty bar as scripts/example_hook_wrapper.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FREETOKEN_UPSTREAM = os.environ.get("FREETOKEN_UPSTREAM", "http://localhost:8000")
PROXY_BIND = os.environ.get("PROXY_BIND", "127.0.0.1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8351"))
COLLECTOR_PATH = os.environ.get(
    "COLLECTOR_PATH",
    os.path.expanduser("~/.stealthlab/demo_traces.jsonl"),
)

# Import the real collector (redaction + dedup + bounded file). If run
# outside the backend venv, fall back to raw JSONL appends and say so.
try:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
    from app.services.trace_collector import append_event  # type: ignore
    COLLECTOR = "production"
except Exception:  # pragma: no cover - scaffold fallback
    def append_event(event, file_path, session_id, event_type, **_):
        # minimal stand-in, same argument order as the production signature
        os.makedirs(os.path.dirname(str(file_path)), exist_ok=True)
        with open(file_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
    COLLECTOR = "fallback"


def _event(run_id: str, idx: int, req: dict, status: int,
           latency_ms: int, resp_summary: dict) -> dict:
    body = json.dumps(req.get("messages", []), sort_keys=True, default=str)
    return {
        # trace identity: one agent RUN == one trace; each LLM call is a span
        "trace_id": f"ft_{run_id}",
        "parent_trace_id": None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor_id": f"freetoken:{req.get('model', 'unknown')}",
        "action_type": "invoke_agent",
        "outcome": "success" if status == 200 else "failure",
        "cost": 0.0,                       # local arm is free — that IS the point
        "latency_ms": latency_ms,
        # Band-1 contract fields (migration 21 scope columns downstream):
        "provenance": "public_generated",
        "scope_type": "session",
        "scope_entity_id": run_id,
        # demo-specific payload (worker adapter maps these onto
        # trace_events: prompt-shape hash, not full text, unless
        # STEALTH_DEMO_FULL_TEXT=1 — keep redaction meaningful).
        "payload": {
            "kind": "llm_call",
            "step_index": idx,
            "messages_sha256": hashlib.sha256(body.encode()).hexdigest()[:16],
            "n_messages": len(req.get("messages", [])),
            "tools_offered": len(req.get("tools") or []),
            "response_status": status,
            "usage": resp_summary.get("usage", {}),
        },
    }


class Handler(BaseHTTPRequestHandler):
    run_counter = 0

    def do_POST(self):  # noqa: N802 - stdlib naming
        t0 = time.monotonic()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            req = {}

        # X-Run-ID lets the harness pin all turns of one task to one trace.
        run_id = self.headers.get("X-Run-ID") or f"run{Handler.run_counter}"
        Handler.run_counter += 1

        upstream_req = urllib.request.Request(
            FREETOKEN_UPSTREAM + self.path,
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(upstream_req, timeout=600) as up:
                body, status = up.read(), up.status
        except urllib.error.HTTPError as e:
            body, status = e.read(), e.code
        except urllib.error.URLError as e:
            body = json.dumps({"proxy_error": str(e)}).encode()
            status = 502

        latency_ms = int((time.monotonic() - t0) * 1000)
        try:
            resp_summary = json.loads(body) if status == 200 else {}
        except json.JSONDecodeError:
            resp_summary = {}
        try:
            append_event(
                _event(run_id, Handler.run_counter, req, status, latency_ms,
                       resp_summary),
                COLLECTOR_PATH, run_id, "llm_call")
        except Exception as e:  # never break the agent turn on telemetry
            print(f"[capture] collector write failed: {e}", flush=True)

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # pass through /v1/models etc.
        with urllib.request.urlopen(FREETOKEN_UPSTREAM + self.path, timeout=30) as up:
            body = up.read()
        self.send_response(up.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"capture proxy {PROXY_BIND}:{PROXY_PORT} -> {FREETOKEN_UPSTREAM}")
    print(f"collector: {COLLECTOR} @ {COLLECTOR_PATH}")
    ThreadingHTTPServer((PROXY_BIND, PROXY_PORT), Handler).serve_forever()
