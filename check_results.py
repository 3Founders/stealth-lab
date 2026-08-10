#!/usr/bin/env python3
"""
One command to see where every SWE-bench Pro run stands.

    python check_results.py            # all runs, summary + per-instance
    python check_results.py -q         # summary lines only
    python check_results.py final      # only files matching "final"

Reads the result JSONLs directly, so it is always current and never needs the
run to be finished. Deliberately dependency-free (stdlib only) -- it must work
while the experiment is holding the database and the Voyage quota.

The two numbers that matter are DISCORDANT PAIRS and the McNemar p, not the
resolution totals: an instance both arms solve, or both fail, carries no
information about whether memory helped. With zero discordant pairs there is
no test to run, which is a different statement from "no difference found".
"""
from __future__ import annotations

import glob
import json
import math
import os
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "experiments", "swebench_pro")
LOGDIR = os.path.expanduser(
    "~/AppData/Local/Temp/claude/c--Users-chait-Prog-3Found-Stealth-StealthLab/"
    "5fbae7cf-2574-4394-9fc8-749fecbdad2b/scratchpad")

# Colour only where it will actually render. The legacy Windows console prints
# the escape sequences literally, which is worse than no colour at all.
_COLOUR = (os.environ.get("WT_SESSION") or os.environ.get("TERM")
           or os.name != "nt") and sys.stdout.isatty()
if "--color" in sys.argv:
    _COLOUR = True
if "--no-color" in sys.argv:
    _COLOUR = False
C_OK, C_BAD, C_DIM, C_HDR, C_END = (
    ("\033[92m", "\033[91m", "\033[90m", "\033[1m", "\033[0m") if _COLOUR
    else ("", "", "", "", ""))


def out(text: str = "") -> None:
    """Print without ever dying on the console's encoding.

    Repo names and issue titles in this corpus contain smart quotes and
    em-dashes; cp1252 raises UnicodeEncodeError on them and a status tool
    that crashes while reporting status is worse than useless."""
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, errors="replace").decode(enc, errors="replace"))


def mcnemar(a_only: int, b_only: int):
    """Exact binomial test on DISCORDANT pairs only. Returns (p, note)."""
    n = a_only + b_only
    if n == 0:
        return None, "no discordant pairs — the test has no input"
    k = min(a_only, b_only)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return p, f"{n} discordant (no_mem-only {a_only}, mem-only {b_only})"


def min_discordant_for_significance(alpha=0.05) -> int:
    k = 1
    while 2 / 2 ** k >= alpha:
        k += 1
    return k


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def verdict(arm):
    if arm is None:
        return "-"
    if arm.get("resolved"):
        return "RESOLVED"
    if not arm.get("valid", True):
        return "INVALID"
    return str(arm.get("status", "?"))[:11]


def report(path, quiet=False):
    rows = load(path)
    name = os.path.basename(path)
    if not rows:
        out(f"{C_DIM}{name}: empty{C_END}")
        return

    usable = [r for r in rows
              if r.get("no_memory") and r.get("graph_memory")
              and r["no_memory"].get("valid", True)
              and r["graph_memory"].get("valid", True)]
    model = next((r.get("model") for r in rows if r.get("model")), "?")
    col = next((r.get("embedding_column") for r in rows if r.get("embedding_column")), "?")

    out(f"\n{C_HDR}{name}{C_END}  {C_DIM}model={model} retriever={col} "
          f"rows={len(rows)} usable={len(usable)}{C_END}")

    if not quiet:
        out(f"  {'repo':<26}{'no_memory':<12}{'graph_memory':<12}"
              f"{'nm_tok':>9}{'gm_tok':>9}  {'new/del/tol':<12}copy")
        for r in rows:
            nm, gm = r.get("no_memory"), r.get("graph_memory")
            if not nm:
                why = r.get("excluded") or str(r.get("error", "?"))[:44]
                out(f"  {C_DIM}{r.get('repo','?')[:24]:<26}{why}{C_END}")
                continue
            v_nm, v_gm = verdict(nm), verdict(gm)
            cd = (f"{gm.get('n_files_created',0)}/{gm.get('n_files_deleted',0)}"
                  f"/{gm.get('tolerant_edits',0)}")
            cp = r.get("copyability", {}).get("max_copyable_fraction")
            colour = C_OK if (nm.get("resolved") or gm.get("resolved")) else ""
            out(f"  {colour}{r['repo'][:24]:<26}{v_nm:<12}{v_gm:<12}"
                  f"{nm['total_tokens']:>9,}{gm['total_tokens']:>9,}  {cd:<12}{cp}{C_END}")

    if not usable:
        out(f"  {C_DIM}no usable paired rows yet{C_END}")
        return

    a = sum(1 for r in usable if r["no_memory"]["resolved"])
    b = sum(1 for r in usable if r["graph_memory"]["resolved"])
    ao = sum(1 for r in usable
             if r["no_memory"]["resolved"] and not r["graph_memory"]["resolved"])
    bo = sum(1 for r in usable
             if r["graph_memory"]["resolved"] and not r["no_memory"]["resolved"])
    p, note = mcnemar(ao, bo)
    ta = sum(r["no_memory"]["total_tokens"] for r in usable) or 1
    tb = sum(r["graph_memory"]["total_tokens"] for r in usable)
    npatch = Counter()
    for r in usable:
        for arm in ("no_memory", "graph_memory"):
            if r[arm].get("status") == "no_patch":
                npatch[arm] += 1
    cops = [r["copyability"]["max_copyable_fraction"] for r in usable
            if r.get("copyability", {}).get("max_copyable_fraction") is not None]
    created = sum(r["graph_memory"].get("n_files_created", 0) for r in usable)
    tol = sum(r[arm].get("tolerant_edits", 0) for r in usable
              for arm in ("no_memory", "graph_memory"))

    out(f"  {C_HDR}resolved{C_END}   no_memory {a}/{len(usable)} ({a/len(usable):.0%})"
          f"   graph_memory {b}/{len(usable)} ({b/len(usable):.0%})")
    pstr = "n/a" if p is None else f"{p:.5f}"
    hit = (p is not None and p < 0.05)
    out(f"  {C_HDR}mcnemar{C_END}    p={C_OK if hit else ''}{pstr}{C_END if hit else ''}"
          f"   {note}")
    out(f"  tokens     {ta:,} vs {tb:,} ({100*(tb-ta)/ta:+.1f}%)"
          f"   no_patch nm={npatch['no_memory']} gm={npatch['graph_memory']}")
    if cops:
        out(f"  copyable   mean {sum(cops)/len(cops):.3f}  max {max(cops):.3f}"
              f"   {C_DIM}(high => wins may be near-duplicate lookup){C_END}")
    out(f"  capability files_created={created} tolerant_edits={tol}")

    need = min_discordant_for_significance()
    if not hit:
        out(f"  {C_DIM}needs >={need} discordant pairs one-way for p<0.05; "
              f"have {ao+bo}{C_END}")


def in_flight():
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object {$_.CommandLine -like '*run_graph_experiment*'} | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=20)
        n = int((proc.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        n = -1
    docker = subprocess.run(["docker", "ps"], capture_output=True).returncode == 0

    logs = sorted(glob.glob(os.path.join(LOGDIR, "*.log")), key=os.path.getmtime)
    cur = ""
    if logs:
        try:
            with open(logs[-1], encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.replace("\x00", "").rstrip()
                    if line.startswith("[") and "/" in line[:6]:
                        cur = line
        except OSError:
            pass
    out(f"\n{C_HDR}environment{C_END}  experiment processes: "
          f"{'unknown' if n < 0 else n}   docker: {'up' if docker else C_BAD+'DOWN'+C_END}")
    if cur:
        out(f"  in flight: {cur[:110]}")


def main() -> int:
    pattern = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else ""
    quiet = "-q" in sys.argv

    files = sorted(glob.glob(os.path.join(RESULTS, "*.jsonl")), key=os.path.getmtime)
    files = [f for f in files if pattern in os.path.basename(f)]
    if not files:
        out(f"no result files matching {pattern!r} in {RESULTS}")
        return 1
    for f in files:
        report(f, quiet=quiet)
    in_flight()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
