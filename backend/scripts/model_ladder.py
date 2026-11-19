#!/usr/bin/env python
"""See the model ladder for a prompt, from a script.

What it is for: the router in `app/routing/` decides which model runs a task, and that decision is only visible
through the MCP surface (`find_ways` attaches a `model_plan`; `recommend_models` returns the ladder on its own).
This script is the smallest way to ask it, one prompt per line, and read the answer as text.

    python backend/scripts/model_ladder.py "fix the failing test in auth"
    python backend/scripts/model_ladder.py --file prompts.txt
    printf 'summarise this changelog\nmake a slide deck about market research\n' | python backend/scripts/model_ladder.py
    python backend/scripts/model_ladder.py --recommend "size the Indian EV market"

It signs in the way the desktop app does: `~/.stealthlab/config.json` holds the MCP endpoint and the coding
sign-in token. Nothing is printed from that file, and no token is ever echoed. `--url` / `--token` override it,
and `STEALTHLAB_MCP_URL` / `STEALTHLAB_TOKEN` work too.

What each line means:
  * `model_plan.status = available` -> `ladder` is the order to try: run the first, check it, and on a failed check
    call `report_result(instance_key, accepted=false)` to be told the next one.
  * `no_candidates` -> the router refuses to guess a model list. Pass `--candidates`, or register a
    CandidateProvider on the server (`app/routing/plan.py`).
  * `unavailable` -> the router itself failed; `reason` says why.

Honest limit: this asks the router only. It does not run the task, and it does not touch the desktop's own ladder
(`desktop/lib/pooled.ts` MODEL_LADDER + `kellplugin.ts` modelPlan), which is a separate, static choice.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CANDIDATES = [
    "deepseek-v4.1-flash", "glm-5.3-flash", "mimo-v2.6-flash", "deepseek-v4-pro", "kimi-k3",
]


def config() -> tuple[str, str]:
    """(url, token): the desktop's own sign-in file, unless the environment or the flags say otherwise."""
    url = os.environ.get("STEALTHLAB_MCP_URL") or os.environ.get("STEALTHLAB_URL") or ""
    token = os.environ.get("STEALTHLAB_TOKEN") or ""
    if url and token:
        return url, token
    path = Path(os.environ.get("STEALTHLAB_HOME") or Path.home() / ".stealthlab") / "config.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return url, token
    return url or str(saved.get("url") or ""), token or str(saved.get("token") or "")


class Mcp:
    """A fresh MCP session per call: initialize, then tools/call. Same shape as the desktop's lib/mcp.ts."""

    def __init__(self, url: str, token: str, timeout: float = 120.0) -> None:
        self.url, self.token, self.timeout, self.sid = url, token, timeout, ""

    def _rpc(self, body: dict) -> tuple[int, str]:
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.url, data=data, method="POST")
        req.add_header("content-type", "application/json")
        req.add_header("accept", "application/json, text/event-stream")
        if self.token:
            req.add_header("authorization", f"Bearer {self.token}")
        if self.sid:
            req.add_header("mcp-session-id", self.sid)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                self.sid = r.headers.get("mcp-session-id") or self.sid
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    @staticmethod
    def _payload(text: str) -> dict:
        """The reply is JSON or SSE; take the last JSON object either way."""
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {}

    def open(self) -> None:
        status, text = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                             "clientInfo": {"name": "kell-model-ladder", "version": "0"}}})
        if status >= 400:
            raise SystemExit(f"the MCP server refused the session ({status}): {text[:200]}")
        self._rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name: str, args: dict) -> dict:
        status, text = self._rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": name, "arguments": args}})
        reply = self._payload(text)
        if status >= 400 or reply.get("error"):
            raise SystemExit(f"{name} failed ({status}): {json.dumps(reply.get('error') or text[:300])[:300]}")
        content = reply.get("result", {}).get("content") or []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                try:
                    return json.loads(part["text"])
                except json.JSONDecodeError:
                    return {"text": part["text"]}
        return reply.get("result") or {}


def ladder_lines(prompt: str, found: dict) -> list[str]:
    """The reply as a person reads it: the Goal, the Ways, and the model ladder."""
    out = [f"== {prompt}"]
    goal = found.get("goal") if isinstance(found.get("goal"), dict) else None
    ways = found.get("procedures") if isinstance(found.get("procedures"), list) else []
    if goal:
        out.append(f"   goal      {goal.get('name') or goal.get('canonical_name') or '?'}")
    if ways:
        out.append(f"   ways      {', '.join(str(w.get('name') or '?') for w in ways[:3])}")
    plan = found.get("model_plan") if isinstance(found.get("model_plan"), dict) else None
    if not plan:
        out.append("   model     no model_plan in the reply (the router did not run for this prompt)")
        return out
    status = plan.get("status") or "?"
    out.append(f"   plan      {status}")
    if plan.get("reason"):
        out.append(f"   reason    {plan['reason']}")
    ladder = plan.get("ladder") or []
    if ladder:
        names = []
        for unit in ladder:
            if isinstance(unit, dict):
                names.append(str(unit.get("unit") or unit.get("model") or "?"))
            else:
                names.append(str(unit))
        out.append("   ladder    " + "  ->  ".join(names))
    if plan.get("instance_key"):
        out.append(f"   instance  {plan['instance_key']}")
    if plan.get("meets_reliability_target") is not None:
        # When the target is out of reach the ladder is chosen by expected utility; say so, so "reliable False" does
        # not read as "this is a guess" when it is the best trade the prices allow.
        fallback = (plan.get("reliability") or {}).get("fallback")
        note = " (chosen by expected utility: the target is out of reach)" if fallback == "expected_utility" else ""
        out.append(f"   reliable  {plan['meets_reliability_target']}{note}")
    if plan.get("next"):
        out.append(f"   next      {plan['next']}")
    return out


def prompts_from(args: argparse.Namespace) -> list[str]:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.prompts:
        text = "\n".join(args.prompts)
    else:
        text = sys.stdin.read() if not sys.stdin.isatty() else ""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def main() -> int:
    # A Windows console is cp1252; the ladder text is not, so a print must never crash the run.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Ask kell's router which model runs a prompt, and print the ladder.")
    ap.add_argument("prompts", nargs="*", help="one or more prompts (or use --file / stdin, one per line)")
    ap.add_argument("--file", help="a file of prompts, one per line (# lines are ignored)")
    ap.add_argument("--candidates", help="comma-separated units you can run, e.g. 'deepseek-v4.1-flash,glm-5.3-flash'")
    ap.add_argument("--check", default="tests", help="how a result is checked: tests (default), judge, self_report")
    ap.add_argument("--recommend", action="store_true",
                    help="ask recommend_models directly instead of find_ways (no Goal needed, ladder without Ways)")
    ap.add_argument("--llm", action="store_true", help="let find_ways use its LLM stage (slower, closer to the app)")
    ap.add_argument("--url", default="", help="MCP endpoint (default: ~/.stealthlab/config.json)")
    ap.add_argument("--token", default="", help="bearer (default: ~/.stealthlab/config.json)")
    args = ap.parse_args()

    prompts = prompts_from(args)
    if not prompts:
        ap.print_help()
        return 2
    url, token = config()
    url, token = args.url or url, args.token or token
    if not url or not token:
        print("no kell sign-in found: run `kell login`, or pass --url and --token", file=sys.stderr)
        return 2
    units = [c.strip() for c in (args.candidates or ",".join(DEFAULT_CANDIDATES)).split(",") if c.strip()]
    # A candidate is a unit: "model|scaffold". The scaffold is the harness that runs it; "bare" is the model on its
    # own (no agent loop), which is what a router test needs unless the caller names one.
    candidates = [u if "|" in u else f"{u}|bare" for u in units]

    mcp = Mcp(url, token)
    mcp.open()
    for prompt in prompts:
        if args.recommend:
            found = mcp.call("recommend_models", {"candidates": candidates, "check_kind": args.check})
            found = {"model_plan": found}
        else:
            found = mcp.call("find_ways", {"query": prompt, "candidates": candidates,
                                           "check_kind": args.check, "use_llm": bool(args.llm),
                                           "detail": "summary"})
        print("\n".join(ladder_lines(prompt, found)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
