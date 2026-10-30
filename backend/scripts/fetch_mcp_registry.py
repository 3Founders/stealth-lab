"""Snapshot of the official MCP Registry (registry.modelcontextprotocol.io) as compact server cards.

    python scripts/fetch_mcp_registry.py [--out app/tool_routing/data/mcp_registry.json] [--max-pages 200]

Pages through GET /v0/servers (100 a page, cursor), keeps each server's latest active version, and writes the fields the
tool router reads: name, title, description, version, how it is reached (remote transports, packages), repository and
website, dates. The registry's text is untrusted third-party data: it is stored as data and never followed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://registry.modelcontextprotocol.io/v0/servers"
UA = "kell-tool-router/0.1 (+https://www.kell.org.in)"


def page(cursor: str | None) -> dict:
    q = {"limit": "100", **({"cursor": cursor} if cursor else {})}
    req = urllib.request.Request(f"{BASE}?{urllib.parse.urlencode(q)}", headers={"user-agent": UA, "accept": "application/json"})
    # retries ride out an outage (a DNS failure lasted minutes, 2026-10-30): 2 s doubling to 2 min, ~10 min in all
    for attempt in range(9):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as exc:  # noqa: BLE001 -- retried; the checkpoint keeps what was fetched if it still fails
            if attempt == 8:
                raise
            wait = min(120, 2 * 2 ** attempt)
            print(f"retry in {wait} s after {exc}", file=sys.stderr, flush=True)
            time.sleep(wait)
    return {}


def card(entry: dict) -> dict | None:
    s = entry.get("server") or {}
    meta = (entry.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
    if meta.get("status", "active") != "active" or not s.get("name"):
        return None
    repo = s.get("repository") or {}
    return {
        "name": s["name"],
        "title": s.get("title") or "",
        "description": " ".join(str(s.get("description") or "").split())[:600],
        "version": s.get("version") or "",
        "remotes": sorted({r.get("type", "") for r in s.get("remotes") or [] if r.get("type")}),
        # where a remote server answers: the liveness probe (scripts/probe_mcp_servers.py) connects here
        "remote_urls": [{"type": r.get("type", ""), "url": r.get("url", "")} for r in (s.get("remotes") or [])
                        if isinstance(r, dict) and str(r.get("url", "")).startswith("https://")][:2],
        "packages": [{"registry": p.get("registryType") or p.get("registry_name") or "", "id": p.get("identifier") or p.get("name") or ""}
                     for p in (s.get("packages") or [])][:4],
        "repository": repo.get("url") or "",
        "website": s.get("websiteUrl") or "",
        "published": meta.get("publishedAt") or "",
        "updated": meta.get("updatedAt") or "",
        "latest": bool(meta.get("isLatest", True)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="app/tool_routing/data/mcp_registry.json")
    ap.add_argument("--max-pages", type=int, default=5000)
    ap.add_argument("--fresh", action="store_true", help="ignore a checkpoint and start over")
    a = ap.parse_args()
    # resumable: the servers so far and the next cursor are checkpointed every 25 pages; a rerun continues from them
    part = Path(a.out).with_suffix(".partial.json")
    by_name: dict[str, dict] = {}
    cursor, pages, seen_cursors = None, 0, set()
    if part.exists() and not a.fresh:
        st = json.loads(part.read_text(encoding="utf-8"))
        by_name, cursor, pages = st["servers"], st["cursor"], st["pages"]
        print(f"resuming at page {pages} ({len(by_name)} servers), cursor {cursor}", flush=True)
    while pages < a.max_pages:
        d = page(cursor)
        pages += 1
        if pages % 25 == 0:
            print(f"page {pages}: {len(by_name)} servers so far (cursor {cursor})", flush=True)
        for e in d.get("servers") or []:
            c = card(e)
            if not c:
                continue
            old = by_name.get(c["name"])
            # the latest version wins; without the flag, the most recently updated one
            if old is None or (c["latest"] and not old["latest"]) or (c["latest"] == old["latest"] and c["updated"] > old["updated"]):
                by_name[c["name"]] = c
        cursor = (d.get("metadata") or {}).get("nextCursor")
        # a cursor seen before means the paging went round: stop with what we have
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        if pages % 25 == 0:
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_text(json.dumps({"servers": by_name, "cursor": cursor, "pages": pages}, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.3)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cards = sorted(by_name.values(), key=lambda c: c["name"])
    out.write_text(json.dumps({"source": BASE, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "pages": pages, "servers": cards}, ensure_ascii=False, indent=0), encoding="utf-8")
    part.unlink(missing_ok=True)
    print(f"{len(cards)} servers from {pages} pages -> {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
