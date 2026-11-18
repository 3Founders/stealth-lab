#!/usr/bin/env python3
"""Ask kell's agent a question from the terminal and see exactly what it did: the tools it used, any sign-in or consent link it
needs from you, the memory it used, and its answer. For trying out tool calling without opening the chat page.

    python scripts/ask_kell.py "what is the weather in Mumbai right now?"        # one question
    python scripts/ask_kell.py                                                   # a conversation: type questions, empty line to quit
    python scripts/ask_kell.py "what is anuj working on?" --room <room-id> --agent anuj/kell   # another person's kell, in a room you share
    python scripts/ask_kell.py "..." --raw                                       # also print every streamed frame

Where it asks. --url defaults to a local server (``uvicorn app.mcp_server.server:app --workers 1`` is http://127.0.0.1:8765), which
needs no token. For the hosted server (https://mcp.kell.org.in) it needs the website session token: set KELL_TOKEN, or pass
--token-file (a file holding it), or --token. The token is the ``access_token`` the signed-in website holds (browser developer tools,
Application, Local Storage, the ``sb-...-auth-token`` entry). It is sent only as an Authorization header to --url and never printed.

Honest limits. Without --room the question goes to the temporary-chat endpoint (nothing is stored; no room memory, no canvas). With
--room it is posted as a real message from the agent into that room. A tool that needs a sign-in answers with a link you open yourself;
this script never opens one. What the agent says is model output, not a verified fact.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx


def token_from(args: argparse.Namespace) -> str:
    if args.token:
        return args.token.strip()
    if args.token_file:
        return Path(args.token_file).read_text(encoding="utf-8").strip()
    return os.environ.get("KELL_TOKEN", "").strip()


def frames(response: httpx.Response):
    """Server-sent events as dicts: each ``data: {...}`` line."""
    for line in response.iter_lines():
        if line.startswith("data:"):
            try:
                yield json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue


def ask(args: argparse.Namespace, text: str, history: list[dict]) -> dict:
    token = token_from(args)
    headers = {"accept": "text/event-stream", "content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    base = args.url.rstrip("/")
    body: dict = {"text": text}
    if args.agent:
        body["agent"] = args.agent.lstrip("@")
    if args.room:
        url = f"{base}/v1/rooms/{args.room}/agent"
    else:
        url = f"{base}/v1/agent/ephemeral"
        body["history"] = history
    started = time.monotonic()
    final: dict = {}
    streamed = False
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
        with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code != 200:
                response.read()
                print(f"\n[{response.status_code}] {response.text[:600]}", file=sys.stderr)
                return {"error": True}
            for frame in frames(response):
                if args.raw:
                    print(f"\n[frame] {json.dumps(frame)[:400]}", file=sys.stderr)
                if "delta" in frame:
                    streamed = True
                    print(frame["delta"], end="", flush=True)
                elif "sign_in_tool" in frame:
                    print(f"\n[needs your sign-in] {json.dumps(frame['sign_in_tool'])[:300]}", file=sys.stderr)
                elif "error" in frame:
                    print(f"\n[error] {frame['error']}", file=sys.stderr)
                    return {"error": True}
                elif frame.get("done"):
                    final = frame
    if final and not streamed:
        print(final.get("text", ""), end="")
    print()
    print("-" * 60)
    tools = final.get("tools") or []
    print(f"tools used : {', '.join(map(str, tools)) if tools else '(none: it answered from the model alone)'}")
    for link in final.get("consent") or []:
        print(f"consent    : open this to let the agent use the tool for 15 minutes: {link}")
    for link in final.get("sign_in") or []:
        print(f"sign-in    : open this to connect the tool: {link}")
    memory = final.get("memory") or {}
    if memory.get("used"):
        print(f"memory used: {', '.join(memory['used'])}")
    if memory.get("remembered"):
        print(f"remembered : {'; '.join(memory['remembered'])}")
    print(f"took       : {time.monotonic() - started:.1f}s")
    return final


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="what to ask; leave out for a conversation")
    ap.add_argument("--url", default=os.environ.get("KELL_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--agent", help='which agent: kell (default), or someone else\'s like "anuj/kell" (needs --room)')
    ap.add_argument("--room", help="a room id you are in (from room_list); omit for a temporary chat")
    ap.add_argument("--token")
    ap.add_argument("--token-file")
    ap.add_argument("--raw", action="store_true", help="print every streamed frame to stderr")
    args = ap.parse_args()
    if args.agent and "/" in args.agent and not args.room:
        ap.error("another person's kell needs --room (a room you both are in)")
    history: list[dict] = []
    if args.query:
        return 1 if ask(args, args.query, history).get("error") else 0
    print("Ask kell (empty line to quit). Tools it uses are listed after each answer.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not q:
            return 0
        out = ask(args, q, history)
        if not out.get("error"):
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": out.get("text", "")}]


if __name__ == "__main__":
    raise SystemExit(main())
