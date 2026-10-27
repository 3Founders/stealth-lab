"""Keep a series run going across Claude usage limits: when the Modal app has stopped and some issue in some arm is not
done, wait until the limit's reset time (read off the rate_limited rows) and run the same command again; the harness
skips what is already done. Stops when every issue is done in every arm, or after --max-launches.

    python autoresume.py --run cache-full-1 --repos jsonargparse,sqlglot --arms A,R,RC,C -- <the modal run arguments>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VOLUME = "stealthlab-series"
RESET = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(UTC\)", re.I)


def log(msg: str) -> None:
    print(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC  {msg}", flush=True)


def app_running() -> bool:
    out = subprocess.run(["modal", "app", "list", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    try:
        apps = json.loads(out[out.index("["):])
    except ValueError:
        return True                                  # cannot tell: assume it still runs, look again later
    return any("series" in str(a.get("description") or "") and not a.get("stopped_at") for a in apps)


def fetch(run: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="autoresume-"))
    subprocess.run(["modal", "volume", "get", VOLUME, run, str(d)], capture_output=True)
    return d


def status(root: Path, run: str, repos: list[str], arms: list[str]) -> tuple[list[str], list[str]]:
    """(the (repo, arm, issue) still not done, the rate-limit messages among their latest rows)."""
    missing, limits = [], []
    for repo in repos:
        gold = next(root.rglob(f"{repo}/gold_check.json"), None)
        try:
            usable = json.loads(gold.read_text(encoding="utf-8"))["usable"] if gold else None
        except ValueError:                           # rewritten while a run starts: use the issues the arms have seen
            usable = None
        if not usable:
            seen = set()
            for f in root.rglob(f"{repo}/*/results/{run}.jsonl"):
                seen |= {json.loads(x)["task_id"] for x in f.read_text(encoding="utf-8").splitlines() if x.strip()}
            usable = sorted(seen) or None
        for arm in arms:
            f = next(root.rglob(f"{repo}/{arm}/results/{run}.jsonl"), None)
            latest: dict = {}
            if f:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        r = json.loads(line)
                        latest[r["task_id"]] = r
            for issue in (usable or ["?"]):
                r = latest.get(issue)
                if not r or r.get("status") != "done":
                    missing.append(f"{repo}/{arm}/{issue}")
                    if r and r.get("status") == "rate_limited":
                        limits.append((str(r.get("error") or ""), str(r.get("started_at") or "")))
    return missing, limits


def next_reset(rows: list[tuple[str, str]]) -> dt.datetime | None:
    """The latest reset named by the rate-limit rows, each taken as the first such time after the row was written (a
    reset that has already passed means: go now)."""
    best = None
    for msg, when in rows:
        g = RESET.search(msg)
        if not g:
            continue
        try:
            base = dt.datetime.fromisoformat(when.replace("Z", "+00:00")) if when else dt.datetime.now(dt.timezone.utc)
        except ValueError:
            base = dt.datetime.now(dt.timezone.utc)
        h = int(g[1]) % 12 + (12 if g[3].lower() == "pm" else 0)
        t = base.astimezone(dt.timezone.utc).replace(hour=h, minute=int(g[2] or 0), second=0, microsecond=0)
        if t < base:
            t += dt.timedelta(days=1)
        best = t if best is None or t > best else best
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--repos", required=True)
    ap.add_argument("--arms", required=True)
    ap.add_argument("--max-launches", type=int, default=8)
    ap.add_argument("modal_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    repos, arms = a.repos.split(","), a.arms.split(",")
    cmd = ["modal", "run", "--detach", *[x for x in a.modal_args if x != "--"]]
    launches, other_stops = 0, 0
    while True:
        if app_running():
            time.sleep(300)
            continue
        root = fetch(a.run)
        missing, limits = status(root, a.run, repos, arms)
        shutil.rmtree(root, ignore_errors=True)
        if not missing:
            log("every issue is done in every arm -- finished")
            return
        if launches >= a.max_launches:
            log(f"stopping after {launches} launches; still not done: {len(missing)}")
            return
        reset = next_reset(limits)
        if reset and reset > dt.datetime.now(dt.timezone.utc):
            wake = reset + dt.timedelta(minutes=5)
            log(f"usage limit: {len(missing)} not done; waiting until {wake:%H:%M} UTC")
            time.sleep(max(0, (wake - dt.datetime.now(dt.timezone.utc)).total_seconds()))
        elif not limits:
            other_stops += 1
            if other_stops > 1:
                log(f"stopped twice without a usage limit; {len(missing)} not done -- leaving it for a person")
                return
        launches += 1
        log(f"launch {launches}: {len(missing)} not done")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(240)                                # let the app appear before checking again


if __name__ == "__main__":
    sys.exit(main())
