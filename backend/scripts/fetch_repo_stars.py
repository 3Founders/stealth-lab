"""GitHub stars (and archived / last push) for the registry's repositories: the tool router's popularity signal.

    python scripts/fetch_repo_stars.py [--batch 50]

Reads the GitHub repositories the MCP Registry snapshot links (app/tool_routing/data/mcp_registry.json) and asks
GitHub's GraphQL API for each one's stargazer count, whether it is archived and when it was last pushed, 50 repositories
a query. Read-only public metadata. The token is PERSONAL_GITHUB_TOKEN (or GITHUB_TOKEN) from the environment or
backend/.env, never printed. Written to app/tool_routing/data/registry_stars.json ({"owner/repo": {...}}), with a
checkpoint every 20 queries; a rerun skips what is already there (a missing repository is kept as {"missing": true}).

Why: relevance alone ranked pay-per-call wrappers over Microsoft's Playwright server and Context7 (2026-10-30): the
registry has no notion of what developers actually use, and stars are the cheapest public one.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "app" / "tool_routing" / "data"
SNAPSHOT = DATA / "mcp_registry.json"
OUT = DATA / "registry_stars.json"
REPO = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def token() -> str:
    for var in ("PERSONAL_GITHUB_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    env = Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        for var in ("PERSONAL_GITHUB_TOKEN", "GITHUB_TOKEN"):
            if line.startswith(f"{var}=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("PERSONAL_GITHUB_TOKEN is not set")


def repo_key(url: str) -> str | None:
    m = REPO.search(url or "")
    if not m:
        return None
    name = m.group(2)
    name = name[:-4] if name.endswith(".git") else name
    return f"{m.group(1)}/{name}".lower()


def query(keys: list[str]) -> str:
    parts = []
    for i, k in enumerate(keys):
        owner, name = k.split("/", 1)
        parts.append(f'r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
                     "{ stargazerCount isArchived pushedAt }")
    return "query {\n" + "\n".join(parts) + "\n}"


def ask(keys: list[str], tok: str) -> dict:
    body = json.dumps({"query": query(keys)}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body,
                                 headers={"authorization": f"bearer {tok}", "content-type": "application/json",
                                          "user-agent": "kell-tool-router/0.1"})
    for attempt in range(7):  # back-off up to two minutes: rides out a rate limit or an outage
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise SystemExit("GitHub rejected the token (401)")
            print(f"  retry {attempt + 1}: HTTP {exc.code}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"  retry {attempt + 1}: {str(exc)[:160]}", file=sys.stderr)
        time.sleep(min(120, 4 * 2 ** attempt))
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=50)
    a = ap.parse_args()
    tok = token()
    servers = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["servers"]
    keys = sorted({k for s in servers if (k := repo_key(s.get("repository") or ""))})
    done: dict = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    todo = [k for k in keys if k not in done]
    print(f"{len(keys)} repositories, {len(todo)} to fetch ({len(done)} already)", flush=True)
    t0 = time.monotonic()
    batches = [todo[i:i + a.batch] for i in range(0, len(todo), a.batch)]
    for n, part in enumerate(batches, 1):
        data = (ask(part, tok) or {}).get("data") or {}
        for i, k in enumerate(part):
            r = data.get(f"r{i}")
            if r:
                done[k] = {"stars": int(r.get("stargazerCount") or 0), "archived": bool(r.get("isArchived")),
                           "pushed": str(r.get("pushedAt") or "")[:10]}
            elif data:                       # the query ran and this one came back null: renamed, private or gone
                done[k] = {"missing": True}
        if n % 20 == 0 or n == len(batches):
            OUT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
            print(f"{min(n * a.batch, len(todo))}/{len(todo)} ({time.monotonic() - t0:.0f} s)", flush=True)
    OUT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    print(f"{len(done)} repositories -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
