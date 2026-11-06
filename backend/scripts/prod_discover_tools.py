#!/usr/bin/env python3
"""Ask the PRODUCTION tool router, exactly as an agent does: call discover_tools on the hosted MCP server and print what comes back.

    python scripts/prod_discover_tools.py "check what the page looks like after login"
    python scripts/prod_discover_tools.py "send a slack message" --k 10 --goal messaging
    python scripts/prod_discover_tools.py                      # type needs one after another
    python scripts/prod_discover_tools.py "..." --raw          # the server's untouched JSON

Nothing is re-implemented here: the request goes over MCP (streamable HTTP) to the deployed server, so the answer is the live router's
own (its database catalogue, embeddings, reranker, liveness checks, your account's tool preferences). Use --url to point it at a local
server (http://127.0.0.1:8765/mcp) to compare a change before it is deployed.

Sign-in. The hosted server wants your kell token. This reads, in order: --token, KELL_TOKEN, then ``token`` in ~/.stealthlab/config.json
(what ``kell login`` saves). If you have none, run ``kell login`` once. A local server on this machine needs no token. The token is sent
only as an Authorization header to --url and is never printed. The call is read-only: discover_tools lists tools and runs none.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):          # a description can hold characters a Windows console cannot print
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

DEFAULT_URL = "https://mcp.kell.org.in/mcp"


def find_token(args: argparse.Namespace) -> str:
    if args.token:
        return args.token.strip()
    if os.environ.get("KELL_TOKEN"):
        return os.environ["KELL_TOKEN"].strip()
    cfg = Path(os.environ.get("STEALTHLAB_HOME") or Path.home() / ".stealthlab") / "config.json"
    try:
        return str(json.loads(cfg.read_text(encoding="utf-8")).get("token") or "").strip()
    except (OSError, ValueError):
        return ""


async def call(args: argparse.Namespace, need: str) -> str:
    from mcp import ClientSession

    token = find_token(args)
    local = args.url.startswith(("http://127.0.0.1", "http://localhost"))
    if not token and not local:
        raise SystemExit("no kell token: run `kell login` once (or set KELL_TOKEN / pass --token). A local server needs none.")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    arguments: dict = {"need": need, "k": args.k}
    if args.goal:
        arguments["goal"] = args.goal
    try:                                                     # newer mcp (2.x): the transport takes an HTTP client that carries the header
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        async with create_mcp_http_client(headers=headers) as http:
            async with streamable_http_client(args.url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.call_tool("discover_tools", arguments)
    except ImportError:                                      # older mcp (1.x)
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(args.url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("discover_tools", arguments)
    return "".join(getattr(c, "text", "") for c in result.content)


def ask(args: argparse.Namespace, need: str) -> str:
    try:
        return asyncio.run(call(args, need))
    except SystemExit:
        raise
    except BaseException as exc:                              # noqa: BLE001 -- a transport error arrives wrapped in an ExceptionGroup
        inner = exc
        while getattr(inner, "exceptions", None):
            inner = inner.exceptions[0]
        raise SystemExit(f"the server at {args.url} refused or could not be reached: {type(inner).__name__}: {str(inner)[:200]}"
                         " -- if this is a sign-in error, the token is missing or expired: run `kell login` again.") from None


def show(text: str, raw: bool) -> None:
    if raw:
        print(text)
        return
    try:
        data = json.loads(text)
    except ValueError:
        print(text)                                    # a refusal or plain sentence from the server
        return
    ext = data.get("external") or {}
    if not ext:
        print("(the router returned no external tools; kell's own tools:)")
        for t in data.get("tools", [])[:12]:
            print(f"   {t.get('name')}: {str(t.get('description') or t.get('summary') or '')[:90]}")
        return
    print(f"goal={ext.get('goal')}  found by {ext.get('found_by')}" + (f"  NOTE {ext['note']}" if ext.get("note") else ""))
    for i, t in enumerate(ext.get("tools", []), 1):
        flag = "via kell" if t.get("via_kell") else t.get("source", "")
        print(f"{i:>2}. {t.get('name', '')[:50]:<50} [{flag}] {t.get('status') or ''}")
        print(f"      {str(t.get('summary') or '')[:110]}")
        if t.get("call"):
            print(f"      {t['call'][:120]}")
        elif t.get("install"):
            print(f"      install: {t['install'][:110]}")
    if ext.get("next"):
        print("next:", ext["next"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("need", nargs="?", help="what you want done, in plain words; leave out to type several")
    ap.add_argument("--url", default=os.environ.get("KELL_MCP_URL", DEFAULT_URL))
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--goal", help="force the kind of tool (web, browser, database, messaging, ...)")
    ap.add_argument("--token")
    ap.add_argument("--raw", action="store_true", help="print the server's JSON untouched")
    args = ap.parse_args()
    if args.need:
        show(ask(args, args.need), args.raw)
        return 0
    print(f"asking {args.url} (empty line to quit)")
    while True:
        try:
            q = input("\nneed> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not q:
            return 0
        show(ask(args, q), args.raw)


if __name__ == "__main__":
    raise SystemExit(main())
