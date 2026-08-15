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


def dominant_pair(rows: list) -> list:
    """The two arms that co-occur (both present+valid in the SAME row)
    most often -- NOT just the two alphabetically-first arm names across
    the whole file. A file that accumulates rows from more than one
    --arms configuration (e.g. an initial no_memory/graph_memory sweep,
    later resumed after htn_no_memory was added to ARM_SPEC) has no
    single arm pair present on every row; picking the pair that actually
    co-occurs most is what makes the console table and the pairwise
    stats describe the data that's actually THERE, per row, rather than
    a global union that most individual rows don't satisfy."""
    counts = Counter()
    for r in rows:
        present = [a for a in arms_in_row(r) if r[a].get("valid", True)]
        for i, x in enumerate(present):
            for y in present[i + 1:]:
                counts[tuple(sorted((x, y)))] += 1
    if not counts:
        return []
    return list(max(counts, key=counts.get))


def report(path, quiet=False, rows=None):
    """
    Arm-generic: previously hardcoded to no_memory/graph_memory, which
    meant a file recorded with ANY other arm pair (e.g. the current
    no_memory/htn_memory sweep) always printed "usable=0 / no usable
    paired rows yet" -- not because the data was bad, but because the
    two hardcoded key lookups (r.get("no_memory"), r.get("graph_memory"))
    never both hit. Confirmed live: 15 real, validly-paired rows in the
    active run reported as zero. `arms_in_row` (below) detects whichever
    arms are actually present instead of assuming which two.

    "usable" is scoped to the DISPLAYED PAIR (`dominant_pair`), never to
    the whole-file union of every arm ever seen: requiring every arm in
    that union to be present+valid on every row silently drops rows from
    any file that mixes more than one --arms configuration, which
    reproduces the exact "usable=0 when it shouldn't be" bug this
    rewrite exists to fix, just in a new shape.

    `rows`: pass an already-loaded/parsed row list (e.g. from
    `build_data`'s `_cache`) to skip re-reading and re-JSON-decoding a file
    this process already parsed once this invocation.
    """
    if rows is None:
        rows = load(path)
    name = os.path.basename(path)
    if not rows:
        out(f"{C_DIM}{name}: empty{C_END}")
        return

    arms = sorted({a for r in rows for a in arms_in_row(r)})
    model = next((r.get("model") for r in rows if r.get("model")), "?")
    col = next((r.get("embedding_column") for r in rows if r.get("embedding_column")), "?")
    cols = dominant_pair(rows) or arms[:1]

    usable = [r for r in rows
              if cols and all(c in r and r[c].get("valid", True) for c in cols)]

    out(f"\n{C_HDR}{name}{C_END}  {C_DIM}model={model} retriever={col} "
          f"rows={len(rows)} usable={len(usable)} arms={','.join(arms) or '(none found)'}"
          f"{C_END}")

    if not arms:
        out(f"  {C_DIM}no arm data found in this file{C_END}")
        return

    if not quiet:
        out(f"  {'repo':<26}" + "".join(f"{c:<14}" for c in cols)
              + "".join(f"{('tok(' + c + ')'):>11}" for c in cols) + "  copy")
        for r in rows:
            row_arms = arms_in_row(r)
            if not row_arms:
                why = r.get("excluded") or str(r.get("error", "?"))[:44]
                out(f"  {C_DIM}{r.get('repo','?')[:24]:<26}{why}{C_END}")
                continue
            present = {c: r.get(c) for c in cols}
            if not any(present.values()):
                # This row genuinely has arm data -- just not under the
                # file's dominant pair (a different --arms invocation).
                # Say so plainly rather than mislabeling it as an error.
                out(f"  {C_DIM}{r.get('repo','?')[:24]:<26}(different arms: "
                      f"{','.join(row_arms)}){C_END}")
                continue
            verdicts = "".join(f"{verdict(present.get(c)):<14}" for c in cols)
            toks = "".join(f"{(present.get(c) or {}).get('total_tokens', 0):>11,}"
                           for c in cols)
            cp = r.get("copyability", {}).get("max_copyable_fraction")
            any_resolved = any((present.get(c) or {}).get("resolved") for c in cols)
            colour = C_OK if any_resolved else ""
            out(f"  {colour}{r.get('repo','?')[:24]:<26}{verdicts}{toks}  {cp}{C_END}")
        if len(arms) > 2:
            out(f"  {C_DIM}({len(arms)} arms present -- table shows {cols[0]}/{cols[-1]} "
                  f"(the dominant pair) only; use --html for the rest{C_END}")

    if not usable:
        out(f"  {C_DIM}no usable paired rows yet{C_END}")
        return

    resolved = {c: sum(1 for r in usable if r[c].get("resolved")) for c in cols}
    out(f"  {C_HDR}resolved{C_END}   " + "   ".join(
        f"{c} {resolved[c]}/{len(usable)} ({resolved[c]/len(usable):.0%})" for c in cols))

    hit = False
    if len(cols) == 2:
        x, y = cols
        ao = sum(1 for r in usable if r[x]["resolved"] and not r[y]["resolved"])
        bo = sum(1 for r in usable if r[y]["resolved"] and not r[x]["resolved"])
        p, note = mcnemar(ao, bo)
        pstr = "n/a" if p is None else f"{p:.5f}"
        hit = p is not None and p < 0.05
        out(f"  {C_HDR}mcnemar{C_END}    {x} vs {y}  "
              f"p={C_OK if hit else ''}{pstr}{C_END if hit else ''}   {note}")
        if len(arms) > 2:
            out(f"  {C_DIM}(showing {x} vs {y} only -- {len(arms)} arms present, "
                  f"--html shows every pair){C_END}")

        need = min_discordant_for_significance()
        if not hit:
            out(f"  {C_DIM}needs >={need} discordant pairs one-way for p<0.05; "
                  f"have {ao + bo}{C_END}")

    for c in cols:
        t = sum(r[c].get("total_tokens", 0) for r in usable)
        npatch = sum(1 for r in usable if r[c].get("status") == "no_patch")
        created = sum(r[c].get("n_files_created", 0) for r in usable)
        deleted = sum(r[c].get("n_files_deleted", 0) for r in usable)
        tol = sum(r[c].get("tolerant_edits", 0) for r in usable)
        out(f"  {c:<14} tokens={t:,}   no_patch={npatch}   "
              f"files created/deleted={created}/{deleted}   tolerant_edits={tol}")

    cops = [r["copyability"]["max_copyable_fraction"] for r in usable
            if r.get("copyability", {}).get("max_copyable_fraction") is not None]
    if cops:
        out(f"  copyable   mean {sum(cops)/len(cops):.3f}  max {max(cops):.3f}"
              f"   {C_DIM}(high => wins may be near-duplicate lookup){C_END}")


def arms_in_row(row: dict) -> list:
    """Any top-level key whose value looks like an agent-arm result --
    generic rather than a hardcoded arm-name list, so a future ARM_SPEC
    entry shows up here without editing this file.

    'resolved' ALONE is not enough: rec["gold"] (the gold-patch validation
    result written in run_one, BEFORE any arm runs) is `{"resolved":...,
    "status":..., "n_tests_parsed":...}` -- same shape check, wrong
    semantics entirely, and it produced a real, live bug here: a bogus
    "gold vs htn_memory" McNemar row with p=0.001 on the very first data
    this was tested against. Real arm dicts also always carry
    'total_tokens' (set at run_graph_experiment.py's rec[arm] construction,
    unconditionally, for every arm) -- gold's dict never has that key at
    all, so requiring it excludes gold without needing to name it."""
    return [k for k, v in row.items()
            if isinstance(v, dict) and "resolved" in v and "total_tokens" in v]


def node_rollup(nodes: list) -> dict:
    """Cheap status counts for one plan's nodes. Tolerant of BOTH node
    shapes on disk: the original 11-key snapshot (no per-node cost data)
    and this session's added instrumentation (steps_used/tokens/etc) --
    every read here is .get(..., default), so a mixed set of old and new
    result files never raises."""
    total = len(nodes)
    by_status = Counter(n.get("status", "?") for n in nodes)
    starved = sum(1 for n in nodes
                  if n.get("attempts", 0) == 0 and n.get("status") == "pending")
    blocked_unrun = sum(1 for n in nodes
                        if n.get("attempts", 0) == 0 and n.get("status") == "blocked")
    return {"total": total, "by_status": dict(by_status),
            "budget_starved": starved, "blocked_unrun": blocked_unrun}


def node_view(n: dict) -> dict:
    """One node, trimmed to what the viewer renders. Caps free-text fields
    (goal/note/last_evidence can run to hundreds of chars in real plans)
    and passes instrumentation fields through with 0/[]/None defaults so
    legacy (pre-instrumentation) node snapshots render fine, just with
    those columns empty instead of raising."""
    return {
        "id": n.get("id"), "goal": str(n.get("goal", ""))[:220],
        "status": n.get("status", "?"), "attempts": n.get("attempts", 0),
        "deps": n.get("deps") or [], "requires": n.get("requires") or [],
        "note": str(n.get("note", ""))[:220],
        "path_hint": str(n.get("path_hint", ""))[:160],
        "steps_used": n.get("steps_used"), "budget_granted": n.get("budget_granted"),
        "rounds": n.get("rounds"), "llm_calls": n.get("llm_calls"),
        "total_tokens": (n["prompt_tokens"] + n["completion_tokens"]
                        if "prompt_tokens" in n and "completion_tokens" in n else None),
        "wall_seconds": n.get("wall_seconds"),
        "tool_calls": n.get("tool_calls"),
        "files_edited": n.get("files_edited"),
    }


def build_data(files: list, _cache: Optional[dict] = None) -> dict:
    """Everything the HTML viewer needs, as one JSON-able dict. Mirrors
    report()'s own logic (same load(), same mcnemar(), same dominant-pair
    notion of "usable") rather than importing run_graph_experiment.py's
    summarise()/node_metrics() -- this file has to keep working while a
    live run holds the DB pool and the embedding API quota, and importing
    that module pulls in both.

    `_cache`, if given a dict, is filled with {path: rows} as each file is
    loaded -- lets a caller that's ALSO going to call report()/main_console
    on the same files (main()'s `--html` without `-q`) reuse the parse
    instead of reading and re-JSON-decoding every file from disk twice.

    Per-arm stats are each arm's OWN valid rows -- not gated on every arm
    ever seen in the file being present too, which would silently drop
    real data from a file that mixes more than one --arms configuration
    (see `dominant_pair`'s docstring). Pairwise stats are similarly scoped
    per PAIR, not to a single whole-file "usable" set.
    """
    out_files = []
    for path in files:
        rows = load(path)
        if _cache is not None:
            _cache[path] = rows
        name = os.path.basename(path)
        if not rows:
            out_files.append({"name": name, "empty": True})
            continue
        model = next((r.get("model") for r in rows if r.get("model")), None)
        col = next((r.get("embedding_column") for r in rows if r.get("embedding_column")), None)
        arms = sorted({a for r in rows for a in arms_in_row(r)})
        cols = dominant_pair(rows)
        usable = [r for r in rows
                  if cols and all(c in r and r[c].get("valid", True) for c in cols)]

        per_arm = {}
        for a in arms:
            got = [r for r in rows if a in r and r[a].get("valid", True)]
            res = sum(1 for r in got if r[a].get("resolved"))
            per_arm[a] = {
                "n": len(got), "resolved": res,
                "rate": round(res / len(got), 3) if got else None,
                "tokens": sum(r[a].get("total_tokens", 0) for r in got),
                "no_patch": sum(1 for r in got if r[a].get("status") == "no_patch"),
            }

        pairwise = {}
        for i, x in enumerate(arms):
            for y in arms[i + 1:]:
                pair_rows = [r for r in rows if x in r and y in r
                            and r[x].get("valid", True) and r[y].get("valid", True)]
                ao = sum(1 for r in pair_rows
                        if r[x].get("resolved") and not r[y].get("resolved"))
                bo = sum(1 for r in pair_rows
                        if r[y].get("resolved") and not r[x].get("resolved"))
                p, note = mcnemar(ao, bo)
                pairwise[f"{x} vs {y}"] = {"a_only": ao, "b_only": bo, "p": p, "note": note,
                                           "n": len(pair_rows)}

        instances = []
        for r in rows:
            inst = {
                "instance_id": r.get("instance_id"), "repo": r.get("repo"),
                "title": r.get("title"), "gold_files": r.get("gold_files") or [],
                "excluded": r.get("excluded"), "error": r.get("error"),
                "wall_seconds": r.get("wall_seconds"), "arms": {},
            }
            for a in arms_in_row(r):
                v = r[a]
                htn = v.get("htn")
                nodes = [node_view(n) for n in (htn.get("nodes") or [])] if htn else None
                inst["arms"][a] = {
                    "resolved": v.get("resolved"), "status": v.get("status"),
                    "valid": v.get("valid", True),
                    "total_tokens": v.get("total_tokens"),
                    "n_tool_calls": v.get("n_tool_calls"),
                    "wall_seconds": v.get("wall_seconds"),
                    "stop_reason": v.get("stop_reason"),
                    "files_edited_correct": v.get("files_edited_correct"),
                    "node_rollup": node_rollup(htn.get("nodes") or []) if htn else None,
                    "nodes": nodes,
                }
            instances.append(inst)

        out_files.append({
            "name": name, "empty": False, "model": model, "embedding_column": col,
            "n_rows": len(rows), "n_usable": len(usable), "arms": arms,
            "per_arm": per_arm, "pairwise": pairwise, "instances": instances,
        })
    return {"files": out_files}


def render_html(data: dict) -> str:
    import datetime
    payload = json.dumps(data, default=str)
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return _HTML_TEMPLATE.replace("__PAYLOAD__", payload).replace("__GENERATED__", generated)


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SWE-bench Pro results</title>
<style>
  :root{
    --bg:#ffffff; --fg:#0a0a0a; --muted:#71717a; --border:#e4e4e7; --card:#ffffff;
    --card2:#fafafa; --accent:#18181b; --ring:#e4e4e7;
    --ok:#16a34a; --ok-bg:#f0fdf4; --bad:#dc2626; --bad-bg:#fef2f2;
    --warn:#d97706; --warn-bg:#fffbeb; --info:#2563eb; --info-bg:#eff6ff;
    --dim-bg:#f4f4f5;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#0a0a0a; --fg:#fafafa; --muted:#a1a1aa; --border:#27272a; --card:#111113;
      --card2:#18181b; --accent:#fafafa; --ring:#27272a;
      --ok:#4ade80; --ok-bg:#052e16; --bad:#f87171; --bad-bg:#450a0a;
      --warn:#fbbf24; --warn-bg:#451a03; --info:#60a5fa; --info-bg:#0c1e3d;
      --dim-bg:#18181b;
    }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--sans);
       font-size:14px;line-height:1.5;padding:24px;max-width:1280px;margin-inline:auto}
  h1{font-size:20px;font-weight:600;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13px;margin-bottom:20px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:10px;
        padding:16px 18px;margin-bottom:16px}
  .card h2{font-size:15px;font-weight:600;margin:0 0 2px;display:flex;align-items:center;gap:8px}
  .card .meta{color:var(--muted);font-size:12px;margin-bottom:12px}
  .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .stat{background:var(--card2);border:1px solid var(--border);border-radius:8px;
        padding:8px 12px;min-width:110px}
  .stat .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
  .stat .v{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
  .badge{display:inline-flex;align-items:center;border-radius:999px;padding:2px 9px;
         font-size:11px;font-weight:600;white-space:nowrap;border:1px solid transparent}
  .b-ok{color:var(--ok);background:var(--ok-bg)}
  .b-bad{color:var(--bad);background:var(--bad-bg)}
  .b-warn{color:var(--warn);background:var(--warn-bg)}
  .b-info{color:var(--info);background:var(--info-bg)}
  .b-dim{color:var(--muted);background:var(--dim-bg)}
  table{border-collapse:collapse;width:100%;font-size:12.5px}
  th{text-align:left;color:var(--muted);font-weight:500;padding:6px 8px;
     border-bottom:1px solid var(--border);white-space:nowrap}
  td{padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
  tr:last-child td{border-bottom:none}
  .mono{font-family:var(--mono)}
  .inst{border:1px solid var(--border);border-radius:8px;margin-bottom:8px;overflow:hidden}
  .inst > summary{cursor:pointer;padding:10px 14px;display:flex;gap:10px;align-items:center;
                  list-style:none;background:var(--card2)}
  .inst > summary::-webkit-details-marker{display:none}
  .inst > summary::before{content:"\25B8";display:inline-block;color:var(--muted);
                          transition:transform .1s;font-size:10px}
  .inst[open] > summary::before{transform:rotate(90deg)}
  .inst .body{padding:12px 14px}
  .repo{font-weight:600}
  .title{color:var(--muted);font-size:12.5px}
  .arm-block{border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:10px}
  .arm-head{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}
  .arm-name{font-family:var(--mono);font-size:12.5px;font-weight:600}
  .pill-list{display:flex;gap:4px;flex-wrap:wrap}
  .empty{color:var(--muted);font-style:italic}
  .controls{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
  input[type=text]{background:var(--card);border:1px solid var(--border);color:var(--fg);
                    border-radius:8px;padding:7px 11px;font-size:13px;font-family:var(--sans)}
  input[type=text]:focus{outline:2px solid var(--ring);outline-offset:-1px}
  .toggle{background:var(--card);border:1px solid var(--border);color:var(--fg);
          border-radius:8px;padding:7px 11px;font-size:13px;cursor:pointer}
  .toggle[aria-pressed=true]{background:var(--accent);color:var(--bg);border-color:var(--accent)}
  .files-scroll{overflow-x:auto}
  .nodegoal{max-width:360px;white-space:normal}
</style>
</head>
<body>
<h1>SWE-bench Pro results</h1>
<div class="sub">generated __GENERATED__ &middot; reads the same result JSONLs as <span class="mono">check_results.py</span></div>
<div class="controls">
  <input type="text" id="filter" placeholder="filter by repo / instance / file&hellip;" style="flex:1;min-width:220px">
  <button class="toggle" id="onlyIssues" aria-pressed="false">only starved/blocked nodes</button>
</div>
<div id="root"></div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const DATA = JSON.parse(document.getElementById('payload').textContent);

function el(tag, attrs, children){
  const e = document.createElement(tag);
  for (const k in (attrs||{})) {
    if (k === 'class') e.className = attrs[k];
    else if (k === 'html') e.innerHTML = attrs[k];
    else e.setAttribute(k, attrs[k]);
  }
  (children||[]).forEach(c => { if (c != null) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return e;
}

function statusBadge(status){
  const map = {done:'b-ok', resolved:'b-ok', failed:'b-bad', blocked:'b-warn',
               pending:'b-dim', expanded:'b-info'};
  return el('span', {class:'badge ' + (map[status] || 'b-dim')}, [status || '?']);
}

function verdictBadge(arm){
  if (!arm) return el('span', {class:'badge b-dim'}, ['-']);
  if (arm.resolved) return el('span', {class:'badge b-ok'}, ['RESOLVED']);
  if (arm.valid === false) return el('span', {class:'badge b-bad'}, ['INVALID']);
  return el('span', {class:'badge b-warn'}, [String(arm.status || '?')]);
}

function stat(k, v){
  return el('div', {class:'stat'}, [el('div', {class:'k'}, [k]), el('div', {class:'v'}, [String(v)])]);
}

function fmtN(x){ return (x === null || x === undefined) ? '—' : x; }

function nodeTable(nodes){
  const hasCost = nodes.some(n => n.steps_used !== null && n.steps_used !== undefined);
  const head = ['id','status','attempts','deps','requires'];
  if (hasCost) head.push('steps', 'budget', 'tokens', 'wall(s)');
  head.push('goal');
  const thead = el('tr', {}, head.map(h => el('th', {}, [h])));
  const rows = nodes.map(n => {
    const cells = [
      el('td', {class:'mono'}, [String(n.id)]),
      el('td', {}, [statusBadge(n.status)]),
      el('td', {class:'mono'}, [String(n.attempts)]),
      el('td', {class:'mono'}, [JSON.stringify(n.deps)]),
      el('td', {class:'mono'}, [JSON.stringify(n.requires)]),
    ];
    if (hasCost) {
      cells.push(
        el('td', {class:'mono'}, [fmtN(n.steps_used)]),
        el('td', {class:'mono'}, [fmtN(n.budget_granted)]),
        el('td', {class:'mono'}, [fmtN(n.total_tokens)]),
        el('td', {class:'mono'}, [n.wall_seconds != null ? n.wall_seconds.toFixed(1) : '—']),
      );
    }
    cells.push(el('td', {class:'nodegoal'}, [n.goal]));
    return el('tr', {}, cells);
  });
  return el('table', {}, [thead, ...rows]);
}

function rollupPills(r){
  if (!r) return el('span', {class:'empty'}, ['no plan data']);
  const parts = [];
  for (const k in r.by_status) parts.push(el('span', {class:'badge b-dim'}, [k + ': ' + r.by_status[k]]));
  if (r.budget_starved) parts.push(el('span', {class:'badge b-bad'}, ['budget-starved: ' + r.budget_starved]));
  if (r.blocked_unrun) parts.push(el('span', {class:'badge b-warn'}, ['blocked: ' + r.blocked_unrun]));
  return el('div', {class:'pill-list'}, parts);
}

function armBlock(name, arm){
  const head = el('div', {class:'arm-head'}, [
    el('span', {class:'arm-name'}, [name]),
    verdictBadge(arm),
    el('span', {class:'badge b-dim'}, ['tok ' + fmtN(arm.total_tokens)]),
    el('span', {class:'badge b-dim'}, ['tools ' + fmtN(arm.n_tool_calls)]),
    arm.wall_seconds != null ? el('span', {class:'badge b-dim'}, [arm.wall_seconds.toFixed(0) + 's']) : null,
    arm.stop_reason ? el('span', {class:'badge b-dim'}, [arm.stop_reason]) : null,
  ]);
  const body = [head];
  if (arm.node_rollup) {
    body.push(rollupPills(arm.node_rollup));
    if (arm.nodes && arm.nodes.length) {
      const wrap = el('div', {class:'files-scroll', style:'margin-top:8px'}, [nodeTable(arm.nodes)]);
      body.push(wrap);
    }
  }
  return el('div', {class:'arm-block'}, body);
}

function instanceCard(inst, onlyIssues){
  if (inst.excluded || inst.error) {
    return el('details', {class:'inst'}, [
      el('summary', {}, [
        el('span', {class:'repo'}, [inst.repo || '?']),
        el('span', {class:'badge b-bad'}, [inst.excluded ? 'EXCLUDED' : 'ERROR']),
      ]),
      el('div', {class:'body'}, [el('div', {class:'empty'}, [inst.excluded || inst.error])]),
    ]);
  }
  const armNames = Object.keys(inst.arms || {});
  if (onlyIssues) {
    const hasIssue = armNames.some(a => {
      const r = inst.arms[a].node_rollup;
      return r && (r.budget_starved > 0 || r.blocked_unrun > 0);
    });
    if (!hasIssue) return null;
  }
  const summaryBadges = armNames.map(a => verdictBadge(inst.arms[a]));
  const details = el('details', {class:'inst'}, [
    el('summary', {}, [
      el('span', {class:'repo'}, [inst.repo || '?']),
      el('span', {class:'title'}, [(inst.title || '').slice(0, 90)]),
      el('span', {class:'row'}, summaryBadges),
    ]),
    el('div', {class:'body'}, armNames.map(a => armBlock(a, inst.arms[a]))),
  ]);
  return details;
}

function fileCard(f, filterText, onlyIssues){
  if (f.empty) return el('div', {class:'card'}, [el('h2', {}, [f.name]), el('div', {class:'meta'}, ['empty'])]);
  const stats = el('div', {class:'row', style:'margin-bottom:12px'}, [
    stat('rows', f.n_rows), stat('usable', f.n_usable),
    ...f.arms.map(a => stat(a, (f.per_arm[a].resolved) + '/' + f.per_arm[a].n)),
  ]);
  const pairRows = Object.entries(f.pairwise || {}).map(([k, v]) =>
    el('div', {class:'badge b-dim', style:'margin-right:6px'},
      [k + ': p=' + (v.p == null ? 'n/a' : v.p.toFixed(4)) + ' (' + v.a_only + '/' + v.b_only + ')']));

  let insts = f.instances || [];
  const ft = (filterText || '').toLowerCase();
  // A filename match (the "file" half of the placeholder's promise) shows
  // the WHOLE file with every instance -- typing a filename to narrow down
  // which run you're looking at shouldn't also require every instance in
  // it to happen to match the same text.
  const fileMatches = ft && f.name.toLowerCase().includes(ft);
  if (ft && !fileMatches) {
    insts = insts.filter(i => (i.repo || '').toLowerCase().includes(ft)
      || (i.instance_id || '').toLowerCase().includes(ft)
      || (i.title || '').toLowerCase().includes(ft));
  }
  const cards = insts.map(i => instanceCard(i, onlyIssues)).filter(Boolean);
  // Nothing in this file matches at all (neither the filename nor any
  // instance) -- hide the card entirely rather than showing an empty
  // shell for every non-matching file while searching.
  if (ft && !fileMatches && !cards.length) return null;

  return el('div', {class:'card'}, [
    el('h2', {}, [f.name]),
    el('div', {class:'meta'}, ['model=' + (f.model || '?') + '  retriever=' + (f.embedding_column || '?')]),
    stats,
    pairRows.length ? el('div', {style:'margin-bottom:12px'}, pairRows) : null,
    ...cards,
    (f.instances && f.instances.length && !cards.length) ? el('div', {class:'empty'}, ['no instances match']) : null,
  ]);
}

function renderAll(){
  const root = document.getElementById('root');
  root.innerHTML = '';
  const filterText = document.getElementById('filter').value;
  const onlyIssues = document.getElementById('onlyIssues').getAttribute('aria-pressed') === 'true';
  DATA.files.forEach(f => {
    const card = fileCard(f, filterText, onlyIssues);
    if (card) root.appendChild(card);
  });
}

document.getElementById('filter').addEventListener('input', renderAll);
document.getElementById('onlyIssues').addEventListener('click', function(){
  const cur = this.getAttribute('aria-pressed') === 'true';
  this.setAttribute('aria-pressed', String(!cur));
  renderAll();
});
renderAll();
</script>
</body>
</html>
"""


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
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    pattern = args[0] if args else ""
    quiet = "-q" in sys.argv

    # Always the default path, deliberately not a positional-adjacent
    # argument: `--html` immediately followed by a filter pattern (a
    # completely natural invocation order, e.g. `--html final`) is
    # indistinguishable from `--html <output-path>` if the next token's
    # shape is the only signal -- that ambiguity would silently swallow
    # the pattern as an output filename instead of using it as a filter.
    html_out = os.path.join(RESULTS, "results_viewer.html") if "--html" in sys.argv else None

    files = sorted(glob.glob(os.path.join(RESULTS, "*.jsonl")), key=os.path.getmtime)
    files = [f for f in files if pattern in os.path.basename(f)]
    if not files:
        out(f"no result files matching {pattern!r} in {RESULTS}")
        return 1

    if html_out:
        # build_data() already loads and JSON-decodes every file; hand that
        # same parse to main_console()/report() via `cache` instead of
        # having report()'s own load() re-read and re-decode each file a
        # second time -- real, avoidable I/O now that rows carry per-node
        # instrumentation and are considerably larger than before.
        cache: dict = {}
        data = build_data(files, _cache=cache)
        html = render_html(data)
        with open(html_out, "w", encoding="utf-8") as f:
            f.write(html)
        n_inst = sum(len(fl.get("instances", [])) for fl in data["files"])
        out(f"wrote {html_out}  ({len(files)} file(s), {n_inst} instance(s))")
        if not quiet:
            return main_console(files, quiet, cache=cache)
        # in_flight() must still run here -- it's cheap (no file parsing)
        # and every OTHER invocation shape prints it; `--html -q` (a very
        # natural "just render the dashboard quietly" command) was
        # silently the one combination that skipped it entirely.
        in_flight()
        return 0

    return main_console(files, quiet)


def main_console(files, quiet, cache=None) -> int:
    for f in files:
        report(f, quiet=quiet, rows=(cache or {}).get(f))
    in_flight()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
