"""A small proxy between Claude Code and the Anthropic API, for the prompt-cache benchmark (docs/cache_bench.md).

Claude Code is pointed at it with ANTHROPIC_BASE_URL. Every request is passed on to the API unchanged and answered as it
streams back; the proxy only writes one JSON line per request to its log: which model was asked for and which was used,
the input the provider served from its cache (`cache_read_input_tokens`), wrote to it (`cache_creation_input_tokens`)
and read fresh (`input_tokens`), the output tokens, the time to the first token and the whole call.

With --route it is also the router arm: a Messages call for the session's model (--session-model) can be sent to
--switch-model instead, so the model changes in the middle of the session, as a per-request router does (see Router for
the modes: naive, cache-aware, every:K). With --route off nothing is changed (the arms that keep one model use the proxy
only to measure).

    python cache_proxy.py --port 8787 --log requests.jsonl [--route naive --session-model claude-opus --switch-model claude-haiku-5-5]
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

UPSTREAM = "https://api.anthropic.com"
# a switch model written "zai:<model>" goes to z.ai's Anthropic-format endpoint (the cheaper open models live on other
# providers, as a router's do), with the z.ai key from the environment in place of the session's Claude login
ZAI_UPSTREAM = "https://api.z.ai/api/anthropic"


def other_provider(model: str, headers: dict) -> tuple[str, str, dict]:
    """Where a switched request goes: (upstream, the model name it knows there, the headers to send)."""
    if not model.startswith("zai:"):
        return UPSTREAM, model, headers
    key = os.environ.get("Z_AI_KEY", "")
    out = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "x-api-key", "anthropic-beta")}
    out["x-api-key"] = key
    return ZAI_UPSTREAM, model.split(":", 1)[1], out
# hop-by-hop headers and the ones aiohttp sets itself
DROP_REQ = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
DROP_RESP = {"content-length", "content-encoding", "transfer-encoding", "connection"}


ROUTINE_MAX_CHARS = 6000        # tool output up to this size counts as a routine step
WARM_PREFIX_TOKENS = 20000      # cache-aware: with a warm cache, switch only when the history to reread is smaller
CACHE_TTL_S = 300               # Anthropic's default cache lifetime


def step_kind(payload: dict) -> tuple[str, dict]:
    """What the request's latest step is, from what any router sees: the last message. 'routine' = only tool results
    coming back, small and without an error (a test run, a file read); 'hard' = anything else (a new instruction from
    the developer, an error, a large result)."""
    msgs = payload.get("messages") or []
    last = msgs[-1] if msgs else {}
    blocks = last.get("content") if isinstance(last.get("content"), list) else [{"type": "text", "text": str(last.get("content", ""))}]
    results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
    size = sum(len(json.dumps(b.get("content", ""))) for b in results)
    feats = {"last_role": last.get("role"), "tool_results": len(results), "result_chars": size,
             "result_error": any(b.get("is_error") for b in results)}
    routine = (last.get("role") == "user" and results and len(results) == len(blocks)
               and size <= ROUTINE_MAX_CHARS and not feats["result_error"])
    return ("routine" if routine else "hard"), feats


class Router:
    """Which model a Messages call for the session's model goes to. Modes:
      off         -- never switch (the arms that keep one model use the proxy only to measure);
      every:K     -- every K-th call (a fixed-rate sweep);
      naive       -- a per-request router: a routine step goes to the cheaper model, a hard one stays;
      cache-aware -- the same, but while the session's cache is warm it switches only when the history the cheaper model
                     would have to reread is small (the 'higher bar when a cache exists' some routers describe)."""

    def __init__(self, mode: str, session_model: str, switch_model: str, state: Path | None = None):
        self.mode, self.session_model, self.switch_model, self.state = mode, session_model, switch_model, state
        self.calls = 0
        self.last_session_ts = 0.0       # when the session's model last answered
        self.last_prefix_tokens = 0      # how much input its last call had (fresh + cache read + cache write)
        if state and state.exists():
            try:
                saved = json.loads(state.read_text(encoding="utf-8"))
                self.calls = int(saved.get("calls", 0))
                self.last_session_ts = float(saved.get("last_session_ts", 0))
                self.last_prefix_tokens = int(saved.get("last_prefix_tokens", 0))
            except (OSError, ValueError):
                pass

    def pick(self, asked: str, payload: dict) -> tuple[str, dict]:
        if self.mode == "off" or not asked.startswith(self.session_model):
            return asked, {}
        self.calls += 1
        if self.mode.startswith("every:"):
            k = int(self.mode.split(":", 1)[1])
            return (self.switch_model if self.calls % k == 0 else asked), {"policy": self.mode}
        kind, feats = step_kind(payload)
        feats["step"] = kind
        if kind != "routine":
            return asked, feats
        if self.mode == "cache-aware":
            warm = time.time() - self.last_session_ts < CACHE_TTL_S and self.last_prefix_tokens > 0
            feats.update(cache_warm=warm, prefix_tokens=self.last_prefix_tokens)
            if warm and self.last_prefix_tokens >= WARM_PREFIX_TOKENS:
                return asked, feats
        return self.switch_model, feats

    def answered(self, model: str, rec: dict) -> None:
        if model.startswith(self.session_model) and rec.get("status") == 200:
            self.last_session_ts = time.time()
            self.last_prefix_tokens = sum(int(rec.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if self.state:
            try:
                self.state.write_text(json.dumps({"calls": self.calls, "last_session_ts": self.last_session_ts,
                                                  "last_prefix_tokens": self.last_prefix_tokens}), encoding="utf-8")
            except OSError:
                pass


def usage_from_sse(chunk_text: str, rec: dict) -> None:
    """Fold the usage numbers of the SSE events in this text into rec (message_start has the input side, message_delta the
    output count)."""
    for line in chunk_text.split("\n"):
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "message_start":
            u = (ev.get("message") or {}).get("usage") or {}
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                rec[k] = int(u.get(k) or 0)
            rec["served_model"] = (ev.get("message") or {}).get("model")
        elif kind == "message_delta":
            u = ev.get("usage") or {}
            rec["output_tokens"] = int(u.get("output_tokens") or rec.get("output_tokens") or 0)
            # some providers (z.ai) send the input side here, at the end, instead of in message_start
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                if u.get(k):
                    rec[k] = int(u[k])
        elif kind == "content_block_delta" and "ttft_s" not in rec:
            rec["ttft_s"] = round(time.monotonic() - rec["_t0"], 3)
        elif kind == "error":
            rec["error"] = (ev.get("error") or {}).get("message", "error")[:300]


def make_app(router: Router, log_path: Path, upstream: str = UPSTREAM) -> web.Application:
    log = open(log_path, "a", encoding="utf-8")
    seq = {"n": 0}

    async def handle(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQ}
        # plain bytes back, so the usage can be read off the stream (aiohttp would otherwise ask for gzip by itself)
        headers["Accept-Encoding"] = "identity"
        rec: dict = {"_t0": time.monotonic(), "ts": time.time(), "path": request.path}
        is_messages = request.method == "POST" and request.path.rstrip("/").endswith("/v1/messages")
        target = upstream
        if is_messages and body:
            try:
                payload = json.loads(body)
                asked = str(payload.get("model", ""))
                used, why = router.pick(asked, payload)
                rec.update(asked_model=asked, model=used, switched=used != asked, **({"route": why} if why else {}))
                if used != asked:
                    target, used, headers = other_provider(used, headers)
                    payload["model"] = used
                    rec["model"] = used
                    body = json.dumps(payload).encode("utf-8")
            except ValueError:
                pass
        async with app["client"].request(request.method, target + request.path_qs, headers=headers, data=body) as up:
            resp = web.StreamResponse(status=up.status, headers={k: v for k, v in up.headers.items() if k.lower() not in DROP_RESP})
            await resp.prepare(request)
            streaming = "text/event-stream" in up.headers.get("content-type", "")
            tail, whole = "", bytearray()
            async for chunk in up.content.iter_any():
                await resp.write(chunk)
                if not is_messages:
                    continue
                if streaming:
                    text = tail + chunk.decode("utf-8", "replace")
                    cut = text.rfind("\n")
                    usage_from_sse(text[: cut + 1], rec)
                    tail = text[cut + 1:]
                else:
                    whole.extend(chunk)
            await resp.write_eof()
            if is_messages:
                if streaming and tail:
                    usage_from_sse(tail + "\n", rec)
                elif not streaming and whole:
                    try:
                        j = json.loads(bytes(whole))
                        u = j.get("usage") or {}
                        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
                            rec[k] = int(u.get(k) or 0)
                        rec["served_model"] = j.get("model")
                        if j.get("type") == "error":
                            rec["error"] = (j.get("error") or {}).get("message", "error")[:300]
                    except ValueError:
                        pass
                rec["status"] = up.status
                rec["total_s"] = round(time.monotonic() - rec.pop("_t0"), 3)
                seq["n"] += 1
                rec["seq"] = seq["n"]
                router.answered(rec.get("model", ""), rec)
                log.write(json.dumps(rec) + "\n")
                log.flush()
            return resp

    async def on_start(app: web.Application) -> None:
        app["client"] = ClientSession(timeout=ClientTimeout(total=None, sock_read=900), auto_decompress=False)

    async def on_stop(app: web.Application) -> None:
        await app["client"].close()
        log.close()

    app = web.Application(client_max_size=256 * 1024 * 1024)
    app.router.add_route("*", "/{tail:.*}", handle)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--log", required=True)
    ap.add_argument("--route", default="off", help="off | naive | cache-aware | every:K")
    ap.add_argument("--session-model", default="claude-opus")
    ap.add_argument("--switch-model", default="claude-haiku-5-5")
    ap.add_argument("--upstream", default=UPSTREAM)
    ap.add_argument("--state", default="", help="a file that keeps the router's memory of the session between runs")
    a = ap.parse_args()
    web.run_app(make_app(Router(a.route, a.session_model, a.switch_model, Path(a.state) if a.state else None), Path(a.log), a.upstream),
                host="127.0.0.1", port=a.port, print=None)


if __name__ == "__main__":
    main()
