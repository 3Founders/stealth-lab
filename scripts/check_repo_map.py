"""Staleness check for docs/repo_map/*.claims.md and *.procedures.md.

Claims carry `source=<path>:<line>#sha=<sha7>`; a claim goes stale when `git hash-object <path>` no longer
starts with that sha, or the path is gone. Procedures carry `-> <path>` locators; a missing path is reported.
This only says a SOURCE CHANGED, not that the claim is now false -- re-read the line and bump the claim.
Exit 1 if anything is stale or missing, so it can gate CI. Usage: python scripts/check_repo_map.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "docs" / "repo_map"
CLAIM_SRC = re.compile(r"source=(?P<path>[^:|#]+):(?P<line>\d+)#sha=(?P<sha>[0-9a-f]{7})")
LOCATOR = re.compile(r"->\s+(?P<path>[\w./\[\]-]+?)(?::\d+)?(?:\s|$)")


def blob_sha(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], cwd=ROOT, text=True).strip()[:7]


def main() -> int:
    bad = 0
    for f in sorted(MAP.glob("*.claims.md")):
        for n, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1):
            m = CLAIM_SRC.search(line)
            if not line.startswith("CLAIM|") or not m:
                continue
            p = ROOT / m["path"]
            if not p.exists():
                print(f"MISSING {f.name}:{n} {m['path']}"); bad += 1
            elif blob_sha(p) != m["sha"]:
                print(f"STALE   {f.name}:{n} {m['path']} (re-read line {m['line']})"); bad += 1
    for f in sorted(MAP.glob("*.procedures.md")):
        for n, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1):
            for m in LOCATOR.finditer(line):
                path = m["path"]
                if "<" in path or "/" not in path:
                    continue  # template placeholder or not a path
                if not (ROOT / path).exists():
                    print(f"MISSING {f.name}:{n} {path}"); bad += 1
    print("repo_map: ok" if not bad else f"repo_map: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
