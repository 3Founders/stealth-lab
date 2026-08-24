"""Exa search client for the RESEARCH lane — stdlib only, zero deps.

Reads the key from EXA_API_KEY (env var) or backend/.env. The key is NEVER
hardcoded here and never committed; backend/.env is git-ignored.

Usage:
  python research_exa.py "tau-bench leaderboard 2026 agent memory" -n 8
  python research_exa.py "procedural memory LLM agents survey" --domain arxiv.org

Output: JSON lines {title, url, published, snippet} to stdout — pipe into a
research note or read directly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


def load_key() -> str:
    key = os.environ.get("EXA_API_KEY", "").strip()
    if key:
        return key
    env_file = Path(__file__).resolve().parent / "backend" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("EXA_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("EXA_API_KEY not found (set env var or add to backend/.env)")


def search(query: str, n: int = 8, domain: str | None = None,
            category: str | None = None) -> list[dict]:
    body: dict = {"query": query, "numResults": n,
                  "contents": {"text": {"maxCharacters": 400}}}
    if domain:
        body["includeDomains"] = [domain]
    if category:
        body["category"] = category  # e.g. "news", "pdf", "github"
    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=json.dumps(body).encode(),
        headers={"x-api-key": load_key(), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    out = []
    for res in data.get("results", []):
        out.append({
            "title": res.get("title"),
            "url": res.get("url"),
            "published": res.get("publishedDate"),
            "snippet": (res.get("text") or "")[:300],
        })
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--domain", default=None,
                    help="restrict to domain, e.g. arxiv.org, openalex.org")
    ap.add_argument("--category", default=None)
    args = ap.parse_args()
    for row in search(args.query, args.n, args.domain, args.category):
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
