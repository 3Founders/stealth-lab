#!/usr/bin/env python3
"""
What actually HAPPENED in each run of a results file.

    python inspect_runs.py experiments/swebench_pro/stage5_check_gptoss.jsonl
    python inspect_runs.py experiments/swebench_pro/*.jsonl -q
    python inspect_runs.py results.jsonl --arm htn_memory --failed-only

check_results.py answers "is there a significant difference between two
arms" -- it is hardwired to no_memory vs graph_memory and reports a McNemar
p. This answers the question that comes BEFORE that one: for a single run,
what did the agent actually do, and where did it go wrong? At the resolution
rates this corpus produces (~12%), the p-value is usually uncomputable and
the per-run trace is the only thing carrying information.

ARM-AGNOSTIC BY CONSTRUCTION. Arms are discovered from the data (any
top-level key whose value is a dict carrying both `status` and
`total_tokens`), not from a hardcoded pair, so this works on the 2-arm pilot
(`no_memory`/`memory`), the 3-arm graph experiment
(`no_memory`/`graph_memory`/`htn_memory`) and any arm added later without
being edited.

The four things it surfaces that the raw JSON buries:

  LOCALIZATION   files_edited vs gold_files, and specifically WHICH gold
                 files were never touched. A patch that edits 1 of 4 required
                 files grades identically to one that edits nothing, but they
                 are completely different failures.
  THE HTN DAG    per-subgoal status and the failure note, so a run that
                 collapsed at subgoal 2 is distinguishable from one whose
                 planner never named the right file at all.
  TOOL SHAPE     the call histogram, and an explicit warning when edit_file
                 was never called -- "no_patch" has several unrelated causes
                 and only the sequence separates them.
  WHY IT ENDED   stop_reason and error/exclusion, kept distinct from the
                 graded verdict: `api_error` and `f2p_failed` are not the
                 same kind of fact and must never be pooled.

Stdlib only, like check_results.py -- it has to run while the experiment is
holding the database, Docker and the embedding quota.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter

# Colour only where it will actually render; the legacy Windows console
# prints the escape sequences literally, which is worse than no colour.
_COLOUR = (os.environ.get("WT_SESSION") or os.environ.get("TERM")
           or os.name != "nt") and sys.stdout.isatty()
if "--color" in sys.argv:
    _COLOUR = True
if "--no-color" in sys.argv:
    _COLOUR = False
C_OK, C_BAD, C_WARN, C_DIM, C_HDR, C_END = (
    ("\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[1m", "\033[0m")
    if _COLOUR else ("", "", "", "", "", ""))

# A run's own `htn` key is the DAG the agent executed. The row's TOP-LEVEL
# `htn` key is something else entirely -- the htn_route() retrieval
# diagnostic -- and conflating them would report routing beam scores as
# subgoal outcomes.
ARM_MARKERS = ("status", "total_tokens")


def out(text: str = "") -> None:
    """Print without ever dying on the console's encoding.

    Repo names and issue titles in this corpus contain smart quotes and
    em-dashes; cp1252 raises UnicodeEncodeError on them, and a status tool
    that crashes while reporting status is worse than useless."""
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, errors="replace").decode(enc, errors="replace"))


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass          # a half-written last line during a live run
    return rows


def arms_in(row: dict) -> list[str]:
    return [k for k, v in row.items()
            if isinstance(v, dict) and all(m in v for m in ARM_MARKERS)]


def verdict(arm: dict) -> tuple[str, str]:
    """(text, colour). `valid` is checked before `status` because a
    provider-killed episode has a status too, and reporting it as a real
    outcome would count an infrastructure failure as a wrong answer."""
    if not arm.get("valid", True):
        return "INVALID", C_WARN
    if arm.get("resolved"):
        return "RESOLVED", C_OK
    return str(arm.get("status", "?")), ""


def short(text, n: int, full: bool = False) -> str:
    text = str(text or "").replace("\n", " ").strip()
    if full or len(text) <= n:
        return text
    return text[:n - 1] + ".."


def tool_histogram(calls: list[str]) -> str:
    if not calls:
        return "(none)"
    counts = Counter(calls)
    return " ".join(f"{name}x{n}" for name, n in counts.most_common())


def describe_arm(name: str, arm: dict, gold: list[str], full: bool) -> None:
    text, colour = verdict(arm)
    edited = list(arm.get("files_edited") or [])
    hit = sorted(set(edited) & set(gold))
    loc = f"{len(hit)}/{len(gold)} gold" if gold else f"{len(edited)} files"

    out(f"    {colour}{name:<14}{text:<13}{C_END}{loc:<14}"
        f"{arm.get('total_tokens', 0):>9,} tok "
        f"{arm.get('n_tool_calls', 0):>3} tools "
        f"{arm.get('wall_seconds', 0):>6.0f}s  "
        f"{C_DIM}[{arm.get('stop_reason', '?')}]{C_END}")

    if arm.get("invalid_reason"):
        out(f"      {C_WARN}invalid: {arm['invalid_reason']}{C_END}")
    if arm.get("agent_error"):
        out(f"      {C_BAD}agent error: {short(arm['agent_error'], 100, full)}{C_END}")

    # The single most diagnostic line in the whole tool: a patch that edits
    # some-but-not-all required files is the largest failure category, and
    # it is invisible in the graded verdict.
    missed = [f for f in gold if f not in set(edited)]
    if gold and missed:
        out(f"      {C_WARN}never touched:{C_END} "
            + ", ".join(short(f, 58, full) for f in missed[:4])
            + (f" (+{len(missed) - 4} more)" if len(missed) > 4 else ""))
    extra = [f for f in edited if f not in set(gold)]
    if extra:
        out(f"      {C_DIM}also edited: "
            + ", ".join(short(f, 58, full) for f in extra[:3])
            + (f" (+{len(extra) - 3} more)" if len(extra) > 3 else "") + C_END)

    graded = arm.get("graded") or {}
    if graded:
        out(f"      tests: f2p {graded.get('f2p_passed', 0)} passed / "
            f"{graded.get('f2p_missing', 0)} missing, "
            f"p2p broke {graded.get('p2p_broke', 0)}, "
            f"apply={graded.get('apply_status', '?')}")

    _describe_dag(arm.get("htn"), full)

    calls = arm.get("tool_calls") or []
    if calls:
        out(f"      {C_DIM}tools: {tool_histogram(calls)}{C_END}")
        # `no_patch` has several unrelated causes -- never editing at all,
        # versus editing repeatedly and having every attempt rejected on an
        # old_str byte-mismatch. They need opposite fixes, and only the
        # sequence tells them apart.
        if "edit_file" not in calls and "create_file" not in calls:
            out(f"      {C_WARN}! never called edit_file/create_file{C_END}")
        elif not (arm.get("patch_bytes") or 0):
            out(f"      {C_WARN}! {calls.count('edit_file')} edit attempt(s) "
                f"but empty patch - edits were rejected{C_END}")
    if arm.get("tolerant_edits"):
        out(f"      {C_DIM}{arm['tolerant_edits']} edit(s) landed only via the "
            f"whitespace-tolerant fallback{C_END}")


def _describe_dag(htn, full: bool) -> None:
    """The arm's OWN htn dict -- the DAG it executed, not the row-level
    htn_route() retrieval diagnostic."""
    if not isinstance(htn, dict):
        return
    nodes = htn.get("nodes") or []
    if not nodes:
        return
    out(f"      plan: {len(nodes)} subgoals - "
        f"{htn.get('subgoals_done', 0)} done / "
        f"{htn.get('subgoals_failed', 0)} failed / "
        f"{htn.get('subgoals_blocked', 0)} blocked / "
        f"{htn.get('subgoals_expanded', 0)} expanded, "
        f"{htn.get('replans', 0)} replans")
    if htn.get("decompose_failed"):
        out(f"      {C_WARN}planner produced no usable DAG (fell back to one "
            f"catch-all subgoal){C_END}")
    if htn.get("seeded_from_library"):
        out(f"      {C_DIM}plan reused from the method library{C_END}")
    for n in nodes:
        st = str(n.get("status", "?"))
        mark = {"done": C_OK, "failed": C_BAD, "blocked": C_WARN}.get(st, C_DIM)
        indent = "  " * int(n.get("depth", 0) or 0)
        out(f"        {mark}[{n.get('id')}] {st:<8}{C_END}{indent}"
            f"{short(n.get('goal'), 88, full)}")
        # The note is where a failure explains itself -- and where a
        # truncated hand-off between subgoals becomes visible.
        if st in ("failed", "blocked") and n.get("note"):
            out(f"            {C_DIM}`- {short(n['note'], 96, full)}{C_END}")


def describe_row(row: dict, idx: int, want_arm: str, failed_only: bool,
                 full: bool) -> bool:
    """Returns whether anything was printed (so filters can skip quietly)."""
    arms = [a for a in arms_in(row) if not want_arm or a == want_arm]
    if failed_only and arms and all(row[a].get("resolved") for a in arms):
        return False

    gold = list(row.get("gold_files") or [])
    head = (f"\n{C_HDR}[{idx}] {row.get('repo', '?')}{C_END}  "
            f"{C_DIM}{short(row.get('instance_id'), 52, full)}{C_END}")
    out(head)
    if row.get("title"):
        out(f"    {short(row['title'], 96, full)}")

    # Rows that never reached an agent at all. These are NOT failures of the
    # agent and must not be read as such.
    if row.get("excluded"):
        out(f"    {C_WARN}EXCLUDED: {row['excluded']}{C_END}")
        return True
    if row.get("error") and not arms:
        out(f"    {C_BAD}ERROR: {short(row['error'], 110, full)}{C_END}")
        if row.get("traceback") and full:
            out(f"{C_DIM}{row['traceback']}{C_END}")
        return True

    if gold:
        out(f"    {C_DIM}gold ({len(gold)}): "
            + ", ".join(short(f, 52, full) for f in gold[:4])
            + (f" (+{len(gold) - 4})" if len(gold) > 4 else "") + C_END)

    g = row.get("gold") or {}
    if g and not g.get("resolved", True):
        out(f"    {C_WARN}gold patch itself did not resolve "
            f"({g.get('status')}){C_END}")

    rr = row.get("retrieval") or {}
    if rr.get("file_recall") is not None:
        out(f"    {C_DIM}retrieval: file_recall={rr['file_recall']:.2f} "
            f"dir_recall={rr.get('dir_recall', 0):.2f} "
            f"hits={rr.get('n_hits', 0)}"
            + (f"  memory_block={row['memory_block_chars']}ch"
               if row.get("memory_block_chars") else "") + C_END)

    if not arms:
        out(f"    {C_DIM}(no arm results in this row){C_END}")
        return True
    for a in arms:
        describe_arm(a, row[a], gold, full)
    return True


def summarise(rows: list[dict], want_arm: str) -> None:
    per = {}
    for r in rows:
        gold = set(r.get("gold_files") or [])
        for a in arms_in(r):
            if want_arm and a != want_arm:
                continue
            d = per.setdefault(a, {"n": 0, "res": 0, "np": 0, "tok": 0,
                                   "loc": [], "invalid": 0})
            arm = r[a]
            d["n"] += 1
            d["res"] += bool(arm.get("resolved"))
            d["np"] += arm.get("status") == "no_patch"
            d["tok"] += arm.get("total_tokens", 0) or 0
            d["invalid"] += not arm.get("valid", True)
            if gold:
                edited = set(arm.get("files_edited") or [])
                d["loc"].append(len(edited & gold) / len(gold))
    if not per:
        return
    out(f"\n  {C_HDR}{'arm':<14}{'n':>4}{'resolved':>14}{'no_patch':>10}"
        f"{'invalid':>9}{'mean loc':>10}{'tokens':>13}{C_END}")
    for a, d in sorted(per.items()):
        loc = sum(d["loc"]) / len(d["loc"]) if d["loc"] else 0.0
        rate = f"{d['res']}/{d['n']} ({100 * d['res'] / d['n']:.0f}%)" if d["n"] else "-"
        out(f"  {a:<14}{d['n']:>4}{rate:>14}{d['np']:>10}{d['invalid']:>9}"
            f"{loc:>10.3f}{d['tok']:>13,}")


def report(path: str, quiet: bool, want_arm: str, failed_only: bool,
           full: bool) -> list[dict]:
    rows = load(path)
    name = os.path.basename(path)
    if not rows:
        out(f"\n{C_DIM}{name}: empty{C_END}")
        return []

    model = next((r.get("model") for r in rows if r.get("model")), "?")
    col = next((r.get("embedding_column") for r in rows
                if r.get("embedding_column")), "?")
    found = sorted({a for r in rows for a in arms_in(r)})
    out(f"\n{'=' * 78}")
    out(f"{C_HDR}{name}{C_END}  {C_DIM}rows={len(rows)} model={model} "
        f"retriever={col} arms={','.join(found) or 'none'}{C_END}")

    if not quiet:
        shown = 0
        for i, r in enumerate(rows, 1):
            if describe_row(r, i, want_arm, failed_only, full):
                shown += 1
        if failed_only and shown == 0:
            out(f"  {C_DIM}(every run resolved){C_END}")
    summarise(rows, want_arm)
    return rows


def main() -> int:
    argv = sys.argv[1:]
    quiet = "-q" in argv or "--quiet" in argv
    failed_only = "--failed-only" in argv
    full = "--full" in argv
    want_arm = ""
    if "--arm" in argv:
        i = argv.index("--arm")
        if i + 1 < len(argv):
            want_arm = argv[i + 1]

    skip = {"--arm", want_arm} if want_arm else set()
    patterns = [a for a in argv if not a.startswith("-") and a not in skip]
    if not patterns:
        out(__doc__.strip().split("\n\n")[1])
        return 1

    # Expand globs ourselves: cmd.exe and PowerShell do not do it for us, so
    # `*.jsonl` would otherwise arrive as a literal filename.
    files: list[str] = []
    for p in patterns:
        matched = sorted(glob.glob(p))
        if matched:
            files.extend(m for m in matched if m not in files)
        elif os.path.exists(p):
            files.append(p)
        else:
            out(f"{C_BAD}no such file: {p}{C_END}")
    if not files:
        return 1

    everything: list[dict] = []
    for f in files:
        everything.extend(report(f, quiet, want_arm, failed_only, full))

    if len(files) > 1:
        out(f"\n{'=' * 78}")
        out(f"{C_HDR}ALL FILES{C_END}  {C_DIM}{len(files)} files, "
            f"{len(everything)} rows{C_END}")
        summarise(everything, want_arm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
