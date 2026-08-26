"""
Model-decides stale-procedure task tier: analysis layer (board Lane MEASURE
CLAUDE.md Task 2, implementing .scratch/research/model-decides-tier-design.md).

RUN #1's headline ("C 6/6 vs B 0/6 stale-refusal, McNemar p~=0.031") was
gate-determined, not model-evidence (research lane's run1-verification.md):
every one of C's refusals was decided by mcp_surface.StubSurface's
deterministic gate before the model saw the card. This tier reuses the
EXISTING, already-proven `substrate_bypasses_gate` bypass (mcp_surface.py:
85-86, openrouter_arms.py RealProcedureAgent.arun) so the model's own
reuse/refuse choice - not the gate - decides the outcome, over 24 new
`fixtures/model_decides/` tasks (3 trap + 3 control per domain x 4 domains).

Zero changes to mcp_surface.py / scoring.py were needed to COLLECT this
tier's data (the design's own §2/§5 finding); this module is the report
layer the design calls for, computing exactly two statistics from raw
episodes + procedures.json ground truth - the same recomputation discipline
as .scratch/research/run1_verify.py:

  - sensitivity: per TRAP task, did each of B/C avoid the stale trap?
    Paired (McNemar-eligible via mcnemar_power, reused verbatim - the row
    shape it already accepts is {"B": {"pass": bool}, "C": {"pass": bool}}).
      C  -> stale_offer pid ended up in refused_procedure_ids (equivalent
            to scoring.classify(...)["stale_refusal_correct"] restricted to
            this task's one offered pid).
      B  -> NOT reuse_caused_failure (B has no procedure ids to refuse at
            all - openrouter_arms.py never gives it one - so its parallel
            signal is behavioral: did following the misleading rag blob
            cause the failure, or did the model route around it).
  - specificity: C-only descriptive rate over CONTROL tasks (never paired
    against B - B cannot refuse a named id, so there is nothing to pair):
    false_refusal := the offered (non-stale) applicable_procedure ended up
    in refused_procedure_ids. Guards against "C refuses everything"
    masquerading as "C detects staleness".

Pure functions over loaded JSON, no backend imports (lane rule).
"""
from __future__ import annotations

import json
from pathlib import Path

import mcnemar_power
import scoring

TASK_ID_PREFIX = "dec-"


def load_tasks(fixtures_dir: Path | str) -> dict[str, dict]:
    data = json.loads(
        (Path(fixtures_dir) / "tasks.json").read_text(encoding="utf-8"))
    return {t["task_id"]: t for t in data["tasks"]}


def load_rows(results_path: Path | str) -> list[dict]:
    """Same tolerant JSONL loader as scoreboard.load_rows (torn final line
    from an interrupted append is skipped, not fatal)."""
    out = []
    for line in Path(results_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def task_role(task: dict) -> str:
    """'trap' (stale_offer set - correct behavior is refuse) or 'control'
    (applicable_procedure set - correct behavior is reuse). Single-offer
    wave-1 shape (design §4): exactly one of the two is ever set."""
    has_stale = bool(task.get("stale_offer"))
    has_applicable = bool(task.get("applicable_procedure"))
    if has_stale and not has_applicable:
        return "trap"
    if has_applicable and not has_stale:
        return "control"
    return "unknown"


def offered_pid(task: dict) -> str | None:
    return task.get("stale_offer") or task.get("applicable_procedure")


def validate_model_decides_fixtures(fixtures_dir: Path | str) -> list[str]:
    """Cross-reference tasks/procedures/rag fixtures for this tier. Returns
    a list of problems ([] == coherent pack) - same discipline as
    micro_pack.validate_micro_fixtures, scoped to this tier's own
    invariants (single-offer shape, dec- prefix, real procedure ids)."""
    p: list[str] = []
    fixtures_dir = Path(fixtures_dir)
    tasks = load_tasks(fixtures_dir)
    procs_by_id = scoring.load_procedures(fixtures_dir)
    rag_domains = {b["domain"] for b in json.loads(
        (fixtures_dir / "rag_corpus.json").read_text(encoding="utf-8"))["blobs"]}

    for tid, t in tasks.items():
        if not tid.startswith(TASK_ID_PREFIX):
            p.append(f"{tid}: task_id must start with {TASK_ID_PREFIX!r} "
                     f"to stay structurally separate from the mic-* set")
        if not t.get("situation"):
            p.append(f"{tid}: no situation text (and no scenarios.json in "
                     f"this directory) - the prompt would fall back to a "
                     f"generic synthesis")
        if not t.get("substrate_bypasses_gate"):
            p.append(f"{tid}: substrate_bypasses_gate must be true - this "
                     f"tier's whole point is the model deciding, not the gate")
        role = task_role(t)
        if role == "unknown":
            p.append(f"{tid}: must set exactly one of stale_offer/"
                     f"applicable_procedure (single-offer wave-1 shape)")
        stale_pid = t.get("stale_offer")
        if stale_pid:
            if stale_pid not in procs_by_id:
                p.append(f"{tid}: stale_offer {stale_pid} not in procedures.json")
            elif procs_by_id[stale_pid].get("stale") is not True:
                p.append(f"{tid}: stale_offer {stale_pid} must be "
                         f"ground-truth stale=true (trap tasks)")
        app_pid = t.get("applicable_procedure")
        if app_pid:
            if app_pid not in procs_by_id:
                p.append(f"{tid}: applicable_procedure {app_pid} not in "
                         f"procedures.json")
            else:
                ap = procs_by_id[app_pid]
                if ap.get("stale") or not ap.get("verified"):
                    p.append(f"{tid}: applicable_procedure {app_pid} must "
                             f"be verified and fresh (control tasks)")
        if t.get("rag", "none") != "none" and t.get("domain") not in rag_domains:
            p.append(f"{tid}: rag task domain {t.get('domain')} absent "
                     f"from this pack's rag_corpus.json")
    return p


def trap_avoidance_row(task: dict, episode_b: dict | None,
                       episode_c: dict | None,
                       procedures_by_id: dict[str, dict]) -> dict:
    """One trap task -> {"B": {"pass": bool} | None, "C": {...} | None},
    directly consumable by mcnemar_power.discordant_counts/format_pair
    (which already accept exactly this {arm: {"pass": bool}} row shape -
    zero changes needed there). C's pass condition checks GROUND TRUTH
    (procedures_by_id), not just task-schema role, the same defense-in-depth
    scoring.classify uses - a malformed fixture costs a fixture-validation
    failure, never a silently wrong statistic."""
    row: dict = {"task_id": task["task_id"], "domain": task.get("domain"),
                "B": None, "C": None}
    if episode_b is not None and episode_b.get("valid", True):
        row["B"] = {"pass": not bool(episode_b.get("reuse_caused_failure"))}
    if episode_c is not None and episode_c.get("valid", True):
        pid = offered_pid(task)
        refused = episode_c.get("refused_procedure_ids") or []
        is_stale = bool((procedures_by_id.get(pid) or {}).get("stale"))
        row["C"] = {"pass": pid in refused and is_stale}
    return row


def control_false_refusal_row(task: dict, episode_c: dict | None,
                              procedures_by_id: dict[str, dict]) -> dict:
    """One control task -> C's own specificity verdict. Not paired against
    B (design §5: B cannot refuse a named id, there is nothing to pair)."""
    row: dict = {"task_id": task["task_id"], "domain": task.get("domain"),
                "C": None}
    if episode_c is not None and episode_c.get("valid", True):
        pid = offered_pid(task)
        refused = episode_c.get("refused_procedure_ids") or []
        reused = episode_c.get("reused_procedure_ids") or []
        is_stale = bool((procedures_by_id.get(pid) or {}).get("stale"))
        row["C"] = {"false_refusal": pid in refused and not is_stale,
                    "reused": pid in reused}
    return row


def analyze(results_rows: list[dict], tasks: dict[str, dict],
           procedures_by_id: dict[str, dict]) -> dict:
    """Raw sweep rows + this tier's tasks -> the full report structure.
    Rows for OTHER tiers' task_ids (e.g. mic-*) are silently skipped - this
    module never pools across tiers (design §7 item 1's own requirement)."""
    trap_rows, control_rows = [], []
    for r in results_rows:
        tid = r.get("task_id")
        task = tasks.get(tid)
        if task is None:
            continue
        role = task_role(task)
        if role == "trap":
            trap_rows.append(trap_avoidance_row(
                task, r.get("B"), r.get("C"), procedures_by_id))
        elif role == "control":
            control_rows.append(control_false_refusal_row(
                task, r.get("C"), procedures_by_id))

    first_only, second_only = mcnemar_power.discordant_counts(
        trap_rows, "B", "C")
    n_trap_valid_both = sum(
        1 for row in trap_rows if row["B"] is not None and row["C"] is not None)
    n_control_valid_c = sum(
        1 for row in control_rows if row["C"] is not None)
    n_false_refusal = sum(
        1 for row in control_rows
        if row["C"] is not None and row["C"]["false_refusal"])

    return {
        "n_trap_tasks": len(trap_rows),
        "n_trap_valid_both_arms": n_trap_valid_both,
        "sensitivity_pair": mcnemar_power.format_pair(
            "B", "C", first_only, second_only),
        "sensitivity_discordant": {"B_only": first_only, "C_only": second_only},
        "n_control_tasks": len(control_rows),
        "n_control_valid_c": n_control_valid_c,
        "n_false_refusal": n_false_refusal,
        "false_refusal_rate": (
            round(n_false_refusal / n_control_valid_c, 3)
            if n_control_valid_c else None),
        "trap_rows": trap_rows,
        "control_rows": control_rows,
    }


def render_report(report: dict) -> str:
    lines = ["MODEL-DECIDES TIER REPORT (design: "
             ".scratch/research/model-decides-tier-design.md)"]
    lines.append(
        f"trap tasks: {report['n_trap_valid_both_arms']}/"
        f"{report['n_trap_tasks']} valid on both B and C")
    lines.append("  " + report["sensitivity_pair"])
    fr = report["false_refusal_rate"]
    fr_str = "-" if fr is None else f"{fr:.3f}"
    lines.append(
        f"control tasks: {report['n_control_valid_c']}/"
        f"{report['n_control_tasks']} valid on C; "
        f"false_refusal {report['n_false_refusal']}/"
        f"{report['n_control_valid_c'] or 0} ({fr_str})")
    return "\n".join(lines)
