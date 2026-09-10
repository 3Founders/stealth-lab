"""
`stealth` -- a thin READ-ONLY CLI over a workspace's `.stealth/`
projection. It never writes; the local Stealth daemon / MCP server is the
only writer (G13 single-writer discipline).

    python scripts/stealth.py show   <workspace> [claims|procedures|implementations|run|exploration|context]
    python scripts/stealth.py grep   <workspace> <pattern>
    python scripts/stealth.py events <workspace> [--since N]
    python scripts/stealth.py meta   <workspace>

`show` with no page prints `index/root.idx` (the router). `grep` searches
every `index/*.idx` and prints matching rows (id + line range), so the
caller can then `sed -n 'start,end p' .stealth/<file>`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

STEALTH_DIRNAME = ".stealth"
_PAGES = ("claims", "procedures", "implementations", "run", "exploration", "context")


def _sdir(workspace: str) -> str:
    d = os.path.join(workspace, STEALTH_DIRNAME)
    if not os.path.isdir(d):
        sys.exit(f"stealth: no {STEALTH_DIRNAME}/ under {workspace!r}")
    return d


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        sys.exit(f"stealth: not found: {path}")


def cmd_show(args: argparse.Namespace) -> None:
    sdir = _sdir(args.workspace)
    if not args.page:
        sys.stdout.write(_read(os.path.join(sdir, "index", "root.idx")))
        return
    if args.page not in _PAGES:
        sys.exit(f"stealth: unknown page {args.page!r} (one of: {', '.join(_PAGES)})")
    ext = "md" if args.page != "context" else "md"
    sys.stdout.write(_read(os.path.join(sdir, f"{args.page}.{ext}")))


def cmd_grep(args: argparse.Namespace) -> None:
    sdir = _sdir(args.workspace)
    index_dir = os.path.join(sdir, "index")
    rx = re.compile(args.pattern, re.IGNORECASE)
    hits = 0
    for name in sorted(os.listdir(index_dir)):
        if not name.endswith(".idx"):
            continue
        for line in _read(os.path.join(index_dir, name)).splitlines():
            if line.startswith("#") or not line.strip():
                continue
            if rx.search(line):
                print(f"{name}: {line}")
                hits += 1
    if not hits:
        sys.exit(1)


def cmd_events(args: argparse.Namespace) -> None:
    path = os.path.join(_sdir(args.workspace), "events.jsonl")
    for line in _read(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if evt.get("seq", 0) > args.since:
            print(json.dumps(evt, default=str))


def cmd_meta(args: argparse.Namespace) -> None:
    sys.stdout.write(_read(os.path.join(_sdir(args.workspace), "meta.json")))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="stealth", description="read-only view of a .stealth/ projection")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show"); s.add_argument("workspace"); s.add_argument("page", nargs="?")
    s.set_defaults(func=cmd_show)
    g = sub.add_parser("grep"); g.add_argument("workspace"); g.add_argument("pattern")
    g.set_defaults(func=cmd_grep)
    e = sub.add_parser("events"); e.add_argument("workspace"); e.add_argument("--since", type=int, default=0)
    e.set_defaults(func=cmd_events)
    m = sub.add_parser("meta"); m.add_argument("workspace")
    m.set_defaults(func=cmd_meta)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
