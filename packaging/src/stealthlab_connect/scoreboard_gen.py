"""P5 groundwork (board Lane SHIP): PUBLIC SCOREBOARD GENERATOR.

Reads a real-arms sweep's two JSONL artifacts --
experiments/harness/real_arms_results.jsonl (one row per task, episodes
nested under each arm key) and the spend ledger beside it (one row PER
ATTEMPT; run_real_arms.py names it <results-stem>_spend.jsonl, board RUN #1
logged it as real_spend.jsonl -- both names resolve) -- and emits a STATIC
scoreboard page, markdown + HTML, suitable for publishing as-is.

House rules enforced structurally:
  - DISCORDANT PAIRS BESIDE EVERY P-VALUE: comparison lines are rendered by
    experiments/harness/mcnemar_power.format_pair(), whose signature makes a
    bare p-value unrepresentable. This module adds no other way to print one.
  - SPEND LINE: attempts / billed / failed / tokens / cost, failures visible
    AS DATA (the saturated-pool story), aggregated by the runner's own
    openrouter_arms.SpendLog.summarize() -- never re-implemented here.
  - GENERATED TIMESTAMP on every page, plus source-file provenance (which
    files, how many rows) so a screenshot can always be traced to inputs.
  - NO BACKEND EDITS, NO RE-IMPLEMENTATION: classification, arm aggregation,
    McNemar math and spend summation are imported from the harness tree as
    shipped (same discipline as status_server.py importing backend modules).
    Only page assembly lives here.

Honest-absence rules: a missing spend ledger renders an explicit absence
note (never a fabricated zero-spend claim that implies a free run); result
rows that cannot contribute a paired comparison are counted and disclosed
in a caveat, never silently dropped; and any pairwise comparison below the
RUN #1 significance floor (k >= MIN_PUBLIC_DISCORDANT_N discordant pairs)
carries a small-n caveat blocking headline phrasing.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ENV_HARNESS_ROOT = "STEALTHLAB_HARNESS_ROOT"
_HARNESS_MARKERS = ("mcnemar_power.py", "scoreboard.py")
_MARKERS_DISPLAY = " or ".join(_HARNESS_MARKERS)
DEFAULT_RESULTS_NAME = "real_arms_results.jsonl"
ALT_SPEND_NAME = "real_spend.jsonl"
DEFAULT_TITLE = "SS40 Public Scoreboard"

# RUN #1 log (2026-08-26): stale-refusal 6/6 vs 0/6 at p~0.031 was called
# "at the k>=6 significance floor - more n required before any public
# phrasing". That floor is codified here: comparisons under it render with
# an explicit caveat on the public page.
MIN_PUBLIC_DISCORDANT_N = 6

# RUN #1 independent verification (research lane, 2026-08-26,
# .scratch/research/run1-verification.md, caveat 2): a small-n caveat is
# not the only way a comparison can overstate the evidence. C's journal
# proved every stale-refusal credit it earned came from the SUBSTRATE
# GATE deciding (or overriding a proposed reuse) before/instead of the
# model choosing to refuse - openrouter_arms.py's own record_refusal
# reason strings say so verbatim. These two literal strings are the
# harness's contract for that distinction; if openrouter_arms.py renames
# them, test_public_board_offline.py is the tripwire (same discipline as
# the other imported-contract notes on this module).
GATE_AUTO_REFUSAL_REASON = "assumptions no longer hold (gate)"
GATE_BLOCKED_REUSE_REASON = "model proposed reuse; gate blocked"

MD_FILENAME = "public_scoreboard.md"
HTML_FILENAME = "public_scoreboard.html"


class HarnessRootNotFound(RuntimeError):
    pass


def _is_harness_root(candidate: Path) -> bool:
    return all((candidate / marker).is_file() for marker in _HARNESS_MARKERS)


def find_harness_root(explicit: str | os.PathLike | None = None) -> Path:
    """Locate experiments/harness (marker files, _bootstrap.py style).
    Order: explicit argument > $STEALTHLAB_HARNESS_ROOT > walk from this
    file upward, then from the cwd upward."""
    if explicit:
        candidate = Path(explicit).resolve()
        if _is_harness_root(candidate):
            return candidate
        raise HarnessRootNotFound(
            f"the path passed explicitly ({explicit}) is not the "
            f"experiments/harness tree (missing {_HARNESS_MARKERS[0]} or "
            f"{_HARNESS_MARKERS[1]}).")
    env_value = os.environ.get(ENV_HARNESS_ROOT)
    if env_value:
        candidate = Path(env_value).resolve()
        if _is_harness_root(candidate):
            return candidate
        raise HarnessRootNotFound(
            f"${ENV_HARNESS_ROOT}={env_value} is not the "
            f"experiments/harness tree.")
    searched: list[str] = []
    bases: list[Path] = []
    here = Path(__file__).resolve()
    bases.extend(here.parents)
    cwd = Path.cwd().resolve()
    bases.extend((cwd, *cwd.parents))
    seen: set[Path] = set()
    for base in bases:
        if base in seen:
            continue
        seen.add(base)
        searched.append(str(base / "experiments" / "harness"))
        if _is_harness_root(base / "experiments" / "harness"):
            return base / "experiments" / "harness"
        if base.name == "harness":
            searched.append(str(base))
            if _is_harness_root(base):
                return base
    raise HarnessRootNotFound(
        f"could not locate experiments/harness (looked for "
        f"{_MARKERS_DISPLAY} under each candidate). Set "
        f"${ENV_HARNESS_ROOT} or pass --harness-root. Candidates tried: "
        f"{searched}")


_MARKERS_DISPLAY = " or ".join(_HARNESS_MARKERS)


def ensure_harness_importable(root: Path) -> Path:
    root = root.resolve()
    if not _is_harness_root(root):
        raise HarnessRootNotFound(
            f"{root} is not the experiments/harness tree (missing "
            f"{_MARKERS_DISPLAY}).")
    root_str = str(root)
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)
    return root


def _load_harness(harness_root: Path | str | None):
    root = ensure_harness_importable(
        Path(harness_root) if harness_root is not None
        else find_harness_root())
    import mcnemar_power
    import openrouter_arms
    import scoring
    import scoreboard as harness_scoreboard
    return {
        "root": root,
        "mcnemar": mcnemar_power,
        "spend_log": openrouter_arms.SpendLog,
        "scoring": scoring,
        "scoreboard": harness_scoreboard,
    }


def load_jsonl(path: Path | str | None) -> list[dict]:
    """Tolerant JSONL read (torn final line from an interrupted append is
    skipped, same discipline as harness scoreboard.load_rows)."""
    if path is None:
        return []
    out: list[dict] = []
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def stale_refusal_attribution(usable_rows: list[dict], arm: str,
                              procedures_by_id: dict[str, dict]) -> dict:
    """Split an arm's stale_refusals_correct credit by MECHANISM, counted
    PER TASK (one vote per row) to match scoring.classify's own
    stale_refusal_correct semantics (`bool(correct_stale_refusals)` - a
    task with two stale refusals still counts once, not twice; counting
    raw journal EVENTS instead would not foot to the Arms table's
    stale_refusal column whenever a task refuses more than one stale id):
      - gate_mechanical: every stale-refusal reason on the task came from
        the substrate's check_applicability call deciding it (auto-refusal
        before the model saw the card, or a blocked reuse the model itself
        proposed) - GATE_AUTO_REFUSAL_REASON / GATE_BLOCKED_REUSE_REASON.
      - model_initiated: at least one stale-refusal reason on the task was
        anything else, i.e. the agent chose to refuse on its own.
      - unattributed: the episode counts as a correct stale refusal but no
        "<arm>_journal" entry names the refused stale procedure id at all
        (no journal for this arm, or a torn/absent entry) - reported
        honestly rather than guessed into either bucket.
    gate_mechanical + model_initiated + unattributed always equals
    arms_stats[arm]['stale_refusals_correct'] PROVIDED usable_rows is
    exactly the usable (all-arms-valid) task set arms_stats is computed
    over - a task excluded there can still carry a journal entry and must
    not be passed in here.
    """
    gate_mechanical = 0
    model_initiated = 0
    unattributed = 0
    journal_key = f"{arm}_journal"
    for row in usable_rows:
        episode = row.get(arm) or {}
        stale_refused = [
            pid for pid in (episode.get("refused_procedure_ids") or [])
            if procedures_by_id.get(pid, {}).get("stale")]
        if not stale_refused:
            continue
        reasons = [
            entry.get("reason") or ""
            for entry in (row.get(journal_key) or [])
            if isinstance(entry, dict)
            and entry.get("tool") == "record_refusal"
            and entry.get("procedure_id") in stale_refused]
        if not reasons:
            unattributed += 1
        elif all(r in (GATE_AUTO_REFUSAL_REASON, GATE_BLOCKED_REUSE_REASON)
                for r in reasons):
            gate_mechanical += 1
        else:
            model_initiated += 1
    return {"gate_mechanical": gate_mechanical, "model_initiated": model_initiated,
            "unattributed": unattributed}


def default_spend_path(results_path: Path | str) -> Path:
    """Spend-ledger resolution beside the results file, in the order the
    names actually occur in the wild:
      1. <results stem>_spend.jsonl -- run_real_arms.py's code default;
      2. real_arms_spend.jsonl      -- the harness .gitignore's ledger name;
      3. real_spend.jsonl           -- the board RUN #1 logged name.
    When none exists the first candidate is returned so the absence message
    names where the ledger WOULD be."""
    results_path = Path(results_path)
    candidates = [
        results_path.with_name(results_path.stem + "_spend.jsonl"),
        results_path.with_name("real_arms_spend.jsonl"),
        results_path.with_name(ALT_SPEND_NAME),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def build_model(results_rows: list[dict],
                spend_rows: list[dict] | None,
                *,
                generated_at: str,
                title: str = DEFAULT_TITLE,
                results_source: str | None = None,
                spend_source: str | None = None,
                fixtures_dir: Path | str,
                harness_root: Path | str | None = None,
                alpha: float = 0.05,
                target_power: float = 0.80,
                arms: tuple[str, ...] = ("A", "B", "C")) -> dict:
    """Pure transformation: rows in -> page-model dict out. All statistics
    come from the harness modules; this function only sequences them and
    attaches provenance/caveats."""
    h = _load_harness(harness_root)
    scoring = h["scoring"]
    hs = h["scoreboard"]
    mcp = h["mcnemar"]

    procedures_by_id = scoring.load_procedures(fixtures_dir)
    classified = hs.classify_rows(results_rows, procedures_by_id)
    usable = hs.usable_tasks(classified, arms)
    arms_stats = hs.per_arm(usable, arms)

    comparisons = []
    pair_lines = []
    small_n_pairs = []
    for i, x in enumerate(arms):
        for y in arms[i + 1:]:
            fo, so = mcp.discordant_counts(usable, x, y)
            p, note = mcp.mcnemar_exact(fo, so)
            line = mcp.format_pair(x, y, fo, so, alpha, target_power)
            comparisons.append({
                "first": x, "second": y,
                "discordant_first_only": fo,
                "discordant_second_only": so,
                "n_discordant": fo + so,
                "p": p, "note": note, "line": line,
            })
            pair_lines.append(line)
            n = fo + so
            if n < MIN_PUBLIC_DISCORDANT_N:
                small_n_pairs.append(f"{x} vs {y} (n={n})")
    footer_text = mcp.format_footer(
        [(c["first"], c["second"], c["discordant_first_only"],
          c["discordant_second_only"]) for c in comparisons],
        alpha, target_power)
    canonical_text = hs.render(arms_stats, ["PAIRWISE (exact McNemar on "
                                            "pass/fail discordant pairs)",
                                           footer_text],
                               n_tasks_total=len(classified),
                               n_usable=len(usable), banner=title)

    n_errors = sum(1 for r in results_rows if r.get("error"))
    n_excluded = len(classified) - len(usable)

    spend_present = bool(spend_rows)
    spend_block = {"present": spend_present}
    if spend_present:
        ledger = h["spend_log"](None)
        ledger.rows = list(spend_rows)
        summary = ledger.summarize()
        billed = [r for r in spend_rows if r.get("tokens_in") or
                  r.get("tokens_out")]
        by_arm = {}
        for r in billed:
            arm = str(r.get("arm") or "?")
            by_arm[arm] = round(by_arm.get(arm, 0.0) + r.get("cost_usd", 0.0),
                                4)
        spend_block.update({
            "summary": summary,
            "line": ledger.render(),
            "count_429": sum(1 for r in spend_rows if r.get("status") == 429),
            "cost_by_arm_usd": by_arm,
        })

    models_seen = sorted({
        str(ep.get("served_by_model"))
        for r in results_rows for ep in (r.get(a) for a in arms)
        if isinstance(ep, dict) and ep.get("valid")
        and ep.get("served_by_model")
    })

    # Match by object identity, not task_id: a resumed sweep can leave TWO
    # raw rows sharing one task_id (an earlier invalid attempt alongside
    # its valid retry) - classify_rows is 1:1 with results_rows in order,
    # so this pairs each usable CLASSIFIED entry back to its own specific
    # raw row instead of re-matching by a task_id that both rows share.
    usable_ids = {id(c) for c in usable}
    usable_rows = [r for r, c in zip(results_rows, classified)
                  if id(c) in usable_ids]
    stale_attribution = {
        a: stale_refusal_attribution(usable_rows, a, procedures_by_id)
        for a in arms}
    gate_only_arms = [
        a for a in arms
        if arms_stats[a]["stale_refusals_correct"] > 0
        and stale_attribution[a]["gate_mechanical"] > 0
        and stale_attribution[a]["model_initiated"] == 0
    ]
    stale_attribution_present = any(
        v["gate_mechanical"] + v["model_initiated"] > 0
        for v in stale_attribution.values())

    caveats: list[str] = []
    if small_n_pairs:
        caveats.append(
            f"small-n caveat: {', '.join(small_n_pairs)} ha"
            f"{'ve' if len(small_n_pairs) > 1 else 's'} fewer than "
            f"{MIN_PUBLIC_DISCORDANT_N} discordant pairs (the RUN #1 "
            f"k>={MIN_PUBLIC_DISCORDANT_N} significance floor) - directional "
            f"only, not headline evidence.")
    if n_excluded:
        caveats.append(
            f"{n_excluded} of {len(classified)} task rows excluded from the "
            f"paired statistics (missing or invalid episode in some arm); a "
            f"task contributes only when EVERY arm produced a valid episode."
            + (f" Of these, {n_errors} row(s) recorded a runner-level error."
               if n_errors else ""))
    if gate_only_arms:
        caveats.append(
            "interpretive-validity caveat (independent of sample size): "
            f"arm(s) {', '.join(gate_only_arms)} earned 100% of their "
            "counted stale_refusal credit from the substrate gate deciding "
            "before/instead of the model (see 'Stale-refusal attribution' "
            "below), with zero model-initiated refusals logged - the "
            "stale_refusal column is descriptive SYSTEM-level behavior "
            "here, not evidence of per-model staleness detection.")

    return {
        "title": title,
        "generated_at": generated_at,
        "alpha": alpha,
        "target_power": target_power,
        "min_public_discordant_n": MIN_PUBLIC_DISCORDANT_N,
        "sources": {"results": results_source, "spend": spend_source},
        "counts": {
            "rows_total": len(results_rows),
            "tasks_classified": len(classified),
            "tasks_with_error": n_errors,
            "usable": len(usable),
            "excluded": n_excluded,
        },
        "arm_labels": {a: hs.ARM_LABELS[a] for a in arms},
        "arms_stats": arms_stats,
        "comparisons": comparisons,
        "footer_text": footer_text,
        "canonical_text": canonical_text,
        "spend": spend_block,
        "models_seen": models_seen,
        "fixture_pack": f"{Path(fixtures_dir).name} "
                        f"({len(procedures_by_id)} procedures)",
        "caveats": caveats,
        "stale_attribution": stale_attribution,
        "stale_attribution_present": stale_attribution_present,
    }


def _arm_cell_pass(m: dict) -> str:
    return ("-" if m["pass_rate"] is None
            else f"{m['passes']}/{m['n']} ({m['pass_rate']:.2f})")


def _arm_cell_false_reuse(m: dict) -> str:
    return ("-" if m["false_reuse_rate"] is None
            else f"{m['false_reuse_count']}/{m['n']} "
                 f"({m['false_reuse_rate']:.2f})")


def _arm_cell_stale(m: dict) -> str:
    if m["stale_opportunities"] == 0:
        return "no offers"
    return (f"{m['stale_refusals_correct']}/{m['stale_opportunities']}"
            f"(missed {m['stale_refusals_missed']})")


def _arm_cell_unseen(m: dict) -> str:
    return f"{m['unseen_passes']}/{m['unseen_n']}" if m["unseen_n"] else "-"


def render_markdown(model: dict) -> str:
    out: list[str] = []
    out.append(f"# {model['title']}")
    out.append("")
    out.append(f"Generated: {model['generated_at']}")
    src_r = model["sources"]["results"] or "unknown source"
    src_s = (model["sources"]["spend"] or "no spend ledger found")
    out.append(
        f"Sources: `{src_r}` ({model['counts']['rows_total']} task rows) "
        f"· `{src_s}` "
        f"({model['spend']['summary']['attempts'] if model['spend'].get('present') else 0} attempts)")
    out.append(
        f"Fixture pack: {model['fixture_pack']} · usable paired tasks: "
        f"{model['counts']['usable']}/{model['counts']['tasks_classified']}")
    out.append("")
    if model["caveats"]:
        out.append("> **Read first:**")
        for c in model["caveats"]:
            out.append(f"> - {c}")
        out.append("")
    out.append("## Arms")
    out.append("")
    out.append("| arm | n | pass | cost tot/mean $ | false_reuse | "
               "stale_refusal | unseen |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for arm, m in model["arms_stats"].items():
        out.append(
            f"| {arm} ({model['arm_labels'][arm]}) | {m['n']} | "
            f"{_arm_cell_pass(m)} | "
            f"{m['total_cost_usd']:.4f}/{m['mean_cost_usd']:.4f} | "
            f"{_arm_cell_false_reuse(m)} | {_arm_cell_stale(m)} | "
            f"{_arm_cell_unseen(m)} |")
    out.append("")
    out.append("Every rate ships with its numerator/denominator - no bare "
               "rates.")
    if model["models_seen"]:
        out.append("")
        out.append(f"Served by: {', '.join(model['models_seen'])}")
    out.append("")
    out.append("## Stale-refusal attribution (gate-mechanical vs "
               "model-initiated)")
    out.append("")
    if model["stale_attribution_present"]:
        out.append("| arm | gate-mechanical | model-initiated | "
                   "unattributed |")
        out.append("|---|---:|---:|---:|")
        for arm in model["arms_stats"]:
            att = model["stale_attribution"][arm]
            out.append(f"| {arm} | {att['gate_mechanical']} | "
                       f"{att['model_initiated']} | "
                       f"{att['unattributed']} |")
        out.append("")
        out.append("gate-mechanical = substrate check_applicability decided "
                   "the refusal (model never saw the card, or a proposed "
                   "reuse was blocked); model-initiated = the agent refused "
                   "on its own after seeing the card; unattributed = a "
                   "correct stale refusal with no journal entry naming the "
                   "mechanism. A stale_refusal count with zero "
                   "model-initiated entries reflects the surface's gating "
                   "design, not detection by the model serving that arm.")
    else:
        out.append("_No refusal-mechanism journal data available for this "
                   "sweep - attribution not computable; stale_refusal counts "
                   "above are unattributed._")
    out.append("")
    out.append("## Pairwise comparisons (exact McNemar on pass/fail)")
    out.append("")
    for c in model["comparisons"]:
        out.append(f"- **{c['first']} vs {c['second']}**: {c['line']}")
    out.append("")
    out.append(model["footer_text"])
    out.append("")
    out.append("## Spend")
    out.append("")
    if model["spend"].get("present"):
        s = model["spend"]["summary"]
        out.append(model["spend"]["line"])
        out.append(f"- shared-pool saturation (HTTP 429) attempts: "
                   f"{model['spend']['count_429']}")
        by_arm = ", ".join(
            f"{arm} ${cost:.4f}"
            for arm, cost in sorted(model["spend"]["cost_by_arm_usd"].items()))
        out.append(f"- billed cost by arm: {by_arm or '-'}")
        out.append(f"- models used: {', '.join(s['by_model']) or '-'}")
    else:
        out.append("_No spend ledger was found beside the results file - "
                   "the spend section is honestly absent rather than "
                   "reported as a zero-cost run._")
    out.append("")
    out.append("## Canonical terminal rendering")
    out.append("")
    out.append("```")
    out.append(model["canonical_text"])
    out.append("```")
    out.append("")
    return "\n".join(out)


def render_html(model: dict) -> str:
    e = _html.escape
    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append(f'<meta name="generator" content="stealthlab-connect '
                 f'scoreboard_gen">')
    parts.append(f'<meta name="generated-at" content='
                 f'"{e(model["generated_at"])}">')
    parts.append(f"<title>{e(model['title'])}</title>")
    parts.append("""<style>
body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:60rem;
padding:0 1rem;color:#1a1a2e;background:#fafafa}
table{border-collapse:collapse;margin:0.5rem 0}
th,td{border:1px solid #bbb;padding:0.25rem 0.6rem;text-align:right}
th:first-child,td:first-child{text-align:left}
pre{background:#efefef;padding:0.75rem;overflow-x:auto;border:1px solid #ddd}
.caveat{background:#fff3cd;border:1px solid #e0c568;padding:0.5rem 1rem}
.muted{color:#666}
</style></head><body>""")
    parts.append(f"<h1>{e(model['title'])}</h1>")
    parts.append(f"<p class='muted'>Generated: "
                 f"{e(model['generated_at'])}</p>")
    src_r = model["sources"]["results"] or "unknown source"
    src_s = model["sources"]["spend"] or "no spend ledger found"
    attempts = (model["spend"]["summary"]["attempts"]
                if model["spend"].get("present") else 0)
    parts.append(
        f"<p>Sources: <code>{e(src_r)}</code> "
        f"({model['counts']['rows_total']} task rows) &middot; "
        f"<code>{e(src_s)}</code> ({attempts} attempts)</p>")
    parts.append(f"<p>Fixture pack: {e(model['fixture_pack'])} &middot; "
                 f"usable paired tasks: {model['counts']['usable']}/"
                 f"{model['counts']['tasks_classified']}</p>")
    if model["caveats"]:
        parts.append("<div class='caveat'><strong>Read first:</strong><ul>")
        for c in model["caveats"]:
            parts.append(f"<li>{e(c)}</li>")
        parts.append("</ul></div>")
    parts.append("<h2>Arms</h2><table><tr><th>arm</th><th>n</th>"
                 "<th>pass</th><th>cost tot/mean $</th><th>false_reuse</th>"
                 "<th>stale_refusal</th><th>unseen</th></tr>")
    for arm, m in model["arms_stats"].items():
        parts.append(
            f"<tr><td>{e(arm)} ({e(model['arm_labels'][arm])})</td>"
            f"<td>{m['n']}</td><td>{e(_arm_cell_pass(m))}</td>"
            f"<td>{m['total_cost_usd']:.4f}/{m['mean_cost_usd']:.4f}</td>"
            f"<td>{e(_arm_cell_false_reuse(m))}</td>"
            f"<td>{e(_arm_cell_stale(m))}</td>"
            f"<td>{e(_arm_cell_unseen(m))}</td></tr>")
    parts.append("</table>")
    parts.append("<p class='muted'>Every rate ships with its numerator/"
                 "denominator - no bare rates.</p>")
    if model["models_seen"]:
        parts.append(f"<p class='muted'>Served by: "
                     f"{e(', '.join(model['models_seen']))}</p>")
    parts.append("<h2>Stale-refusal attribution (gate-mechanical vs "
                 "model-initiated)</h2>")
    if model["stale_attribution_present"]:
        parts.append("<table><tr><th>arm</th><th>gate-mechanical</th>"
                     "<th>model-initiated</th><th>unattributed</th></tr>")
        for arm in model["arms_stats"]:
            att = model["stale_attribution"][arm]
            parts.append(f"<tr><td>{e(arm)}</td>"
                         f"<td>{att['gate_mechanical']}</td>"
                         f"<td>{att['model_initiated']}</td>"
                         f"<td>{att['unattributed']}</td></tr>")
        parts.append("</table>")
        parts.append("<p class='muted'>gate-mechanical = substrate "
                     "check_applicability decided the refusal (model never "
                     "saw the card, or a proposed reuse was blocked); "
                     "model-initiated = the agent refused on its own after "
                     "seeing the card; unattributed = a correct stale "
                     "refusal with no journal entry naming the mechanism. "
                     "A stale_refusal count with zero model-initiated "
                     "entries reflects the surface's gating "
                     "design, not detection by the model serving that "
                     "arm.</p>")
    else:
        parts.append("<p><em>No refusal-mechanism journal data available "
                     "for this sweep - attribution not computable; "
                     "stale_refusal counts above are unattributed.</em></p>")
    parts.append("<h2>Pairwise comparisons (exact McNemar on pass/fail)"
                 "</h2><ul>")
    for c in model["comparisons"]:
        parts.append(f"<li><strong>{e(c['first'])} vs {e(c['second'])}"
                     f"</strong>: {e(c['line'])}</li>")
    parts.append("</ul>")
    parts.append("<h2>POWER-ANALYSIS FOOTER</h2>")
    parts.append(f"<pre>{e(model['footer_text'])}</pre>")
    parts.append("<h2>Spend</h2>")
    if model["spend"].get("present"):
        s = model["spend"]["summary"]
        parts.append(f"<p>{e(model['spend']['line'])}</p><ul>")
        parts.append(f"<li>shared-pool saturation (HTTP 429) attempts: "
                     f"{model['spend']['count_429']}</li>")
        by_arm = ", ".join(
            f"{e(arm)} ${cost:.4f}" for arm, cost
            in sorted(model["spend"]["cost_by_arm_usd"].items()))
        parts.append(f"<li>billed cost by arm: {by_arm or '-'}</li>")
        parts.append(f"<li>models used: "
                     f"{e(', '.join(s['by_model']) or '-')}</li></ul>")
    else:
        parts.append("<p><em>No spend ledger was found beside the results "
                     "file - the spend section is honestly absent rather "
                     "than reported as a zero-cost run.</em></p>")
    parts.append("<h2>Canonical terminal rendering</h2>")
    parts.append(f"<pre>{e(model['canonical_text'])}</pre>")
    parts.append("</body></html>")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stealthlab-public-board",
        description=__doc__.splitlines()[0])
    parser.add_argument("--results", default=None,
                        help=f"real-arms results JSONL (default: "
                             f"<harness>/{DEFAULT_RESULTS_NAME})")
    parser.add_argument("--spend", default=None,
                        help="spend ledger JSONL (default: "
                             "<results stem>_spend.jsonl beside --results, "
                             f"falling back to {ALT_SPEND_NAME})")
    parser.add_argument("--harness-root", default=None,
                        help=f"experiments/harness tree (default: "
                             f"${ENV_HARNESS_ROOT} or auto-discovery)")
    parser.add_argument("--fixtures-dir", default=None,
                        help="procedure ground-truth pack (default: "
                             "<harness>/fixtures/micro)")
    parser.add_argument("--out-dir", default=".",
                        help="directory for public_scoreboard.md/.html")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--target-power", type=float, default=0.80)
    parser.add_argument("--generated-at", default=None,
                        help="ISO timestamp stamped on the page (default: "
                             "now UTC)")
    args = parser.parse_args(argv)

    try:
        harness_root = find_harness_root(args.harness_root)
    except HarnessRootNotFound as exc:
        print(f"stealthlab-public-board: {exc}", file=sys.stderr)
        return 1

    results_path = (Path(args.results) if args.results
                    else harness_root / DEFAULT_RESULTS_NAME)
    if not results_path.is_file() or results_path.stat().st_size == 0:
        print(f"stealthlab-public-board: results file missing or empty: "
              f"{results_path}. A public page is never generated from "
              f"absent data.", file=sys.stderr)
        return 2
    spend_path = (Path(args.spend) if args.spend
                  else default_spend_path(results_path))

    results_rows = load_jsonl(results_path)
    if not results_rows:
        print(f"stealthlab-public-board: no parsable rows in "
              f"{results_path}", file=sys.stderr)
        return 2
    spend_rows = load_jsonl(spend_path if spend_path.is_file() else None)

    fixtures_dir = (Path(args.fixtures_dir) if args.fixtures_dir
                    else harness_root / "fixtures" / "micro")
    generated_at = (args.generated_at
                    or datetime.now(timezone.utc).isoformat(timespec="seconds"))

    model = build_model(
        results_rows, spend_rows,
        generated_at=generated_at, title=args.title,
        results_source=str(results_path), spend_source=str(spend_path),
        fixtures_dir=fixtures_dir, harness_root=harness_root,
        alpha=args.alpha, target_power=args.target_power)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / MD_FILENAME
    html_path = out_dir / HTML_FILENAME
    md_path.write_text(render_markdown(model), encoding="utf-8")
    html_path.write_text(render_html(model), encoding="utf-8")
    print(f"[public scoreboard -> {md_path}]")
    print(f"[public scoreboard -> {html_path}]")
    return 0


if __name__ == "__main__":  # enables `python -m stealthlab_connect.scoreboard_gen`
    raise SystemExit(main())
