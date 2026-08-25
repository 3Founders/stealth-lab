"""
Micro-experiment pack: per-scenario pass/fail plus EVIDENCE-TRAIL assertions
(board MEASURE item 3, founder mandate 2026-08-25).

The three arms run the micro scenarios through the same runner/scoreboard
pipeline as the skeleton fixtures; this module adds the scenario layer on
top of the episode records:

  - success_criteria.must_resolve — arm-independent ground truth for the task;
  - evidence_requirements          — provenance assertions checked against the
                                     episode record AND the MCP surface call
                                     journal (mcp_surface.StubSurface journal),
                                     because §40's premise ("every reuse shows
                                     its evidence trail") is a property of the
                                     trail, not of self-reported flags.

Trail-type requirements are WAIVED for an arm with zero substrate contact:
arms without a surface path cannot show provenance by construction, and
counting that against them would restate "arm A has no memory" as a finding.
max_tokens always applies — cost is §40 telemetry, not provenance.

Pure functions over loaded JSON like scoring.py; no backend imports (lane rule).
"""
from __future__ import annotations

import json
from pathlib import Path

ARCHETYPES = (
    "adversarial_policy_violation",
    "dependency_conflict_debug",
    "pdf_to_sheet_pipeline",
    "env_drift_staleness",
)

REQ_TYPES = (
    "gate_consulted",
    "refusal_recorded",
    "no_stale_execution",
    "reuse_verified_procedure",
    "max_tokens",
)

# Requirements that only make sense for an arm that consulted the substrate.
TRAIL_REQ_TYPES = frozenset(REQ_TYPES) - {"max_tokens"}


def load_scenarios(fixtures_dir: Path | str) -> dict[str, dict]:
    sc = json.loads(
        (Path(fixtures_dir) / "scenarios.json").read_text(encoding="utf-8"))
    return {s["scenario_id"]: s for s in sc["scenarios"]}


def validate_micro_fixtures(fixtures_dir: Path | str) -> list[str]:
    """Cross-reference tasks/scenarios/procedures/rag fixtures. Returns a list
    of problems ([] == coherent pack). Used by the runner as a loud gate and
    by tests as the schema contract."""
    p = []
    fixtures_dir = Path(fixtures_dir)
    tasks = {t["task_id"]: t for t in json.loads(
        (fixtures_dir / "tasks.json").read_text(encoding="utf-8"))["tasks"]}
    procs_by_id = scoring_load_procedures(fixtures_dir)
    rag_domains = {b["domain"] for b in json.loads(
        (fixtures_dir / "rag_corpus.json").read_text(encoding="utf-8"))["blobs"]}
    scenarios = load_scenarios(fixtures_dir)

    if not (8 <= len(scenarios) <= 12):
        p.append(f"scenario count {len(scenarios)} outside mandated 8-12")
    present = {s.get("archetype") for s in scenarios.values()}
    for arch in ARCHETYPES:
        if arch not in present:
            p.append(f"mandated archetype missing from pack: {arch}")

    for sid, s in scenarios.items():
        if sid not in tasks:
            p.append(f"{sid}: scenario has no matching task")
            continue
        if s.get("archetype") not in ARCHETYPES:
            p.append(f"{sid}: unknown archetype {s.get('archetype')!r}")
        if "must_resolve" not in (s.get("success_criteria") or {}):
            p.append(f"{sid}: success_criteria.must_resolve missing")
        req_ids = [r.get("id") for r in s.get("evidence_requirements", [])]
        if len(req_ids) != len(set(req_ids)):
            p.append(f"{sid}: duplicate evidence requirement ids")
        for r in s.get("evidence_requirements", []):
            if r.get("type") not in REQ_TYPES:
                p.append(f"{sid}/{r.get('id')}: unknown requirement type "
                         f"{r.get('type')!r}")
            pid = r.get("procedure_id")
            if pid is not None and pid not in procs_by_id:
                p.append(f"{sid}/{r.get('id')}: references unknown procedure "
                         f"{pid}")
            if r.get("type") == "max_tokens" and not (
                    isinstance(r.get("budget"), (int, float)) and r["budget"] > 0):
                p.append(f"{sid}/{r.get('id')}: max_tokens needs positive budget")

    for tid, t in tasks.items():
        for key in ("applicable_procedure", "stale_offer"):
            pid = t.get(key)
            if pid and pid not in procs_by_id:
                p.append(f"{tid}: {key} {pid} not in procedures.json")
        stale_pid = t.get("stale_offer")
        if stale_pid and procs_by_id.get(stale_pid, {}).get("stale") is not True:
            p.append(f"{tid}: stale_offer {stale_pid} must be ground-truth "
                     f"stale=true")
        app_pid = t.get("applicable_procedure")
        if app_pid:
            ap = procs_by_id.get(app_pid, {})
            if ap.get("stale") or not ap.get("verified"):
                p.append(f"{tid}: applicable_procedure {app_pid} must be "
                         f"verified and fresh")
        if t.get("rag", "none") != "none" and t["domain"] not in rag_domains:
            p.append(f"{tid}: rag task domain {t['domain']} absent from corpus")

    # A scenario whose trap is a stale offer should ASSERT the trail around it,
    # otherwise the pack never exercises its own point.
    for sid, s in scenarios.items():
        t = tasks.get(sid)
        if not t or not t.get("stale_offer"):
            continue
        types = {r.get("type") for r in s.get("evidence_requirements", [])}
        if "no_stale_execution" not in types and \
                t.get("substrate_bypasses_gate") is not True:
            p.append(f"{sid}: stale offer without a no_stale_execution "
                     f"assertion")
    return p


def scoring_load_procedures(fixtures_dir: Path | str) -> dict[str, dict]:
    # Local import kept lazy so micro_pack stays import-light like scoring.py;
    # same loader, same file layout.
    import scoring
    return scoring.load_procedures(fixtures_dir)


def had_substrate_contact(episode: dict, journal: list[dict]) -> bool:
    """True when the episode shows a SURFACE path at all: a call journal, or
    episode fields only the surface-driven policy produces. Conventional
    memory (`followed_memory_ids`, arm B) deliberately does NOT count — arm B
    having no trail is the experimental contrast (§40), not a scenario
    failure, so trail requirements waive for it rather than fail it."""
    return bool(journal) or bool(episode.get("reused_procedure_ids")) or \
        bool(episode.get("refused_procedure_ids"))


def check_requirement(req: dict, episode: dict, journal: list[dict],
                      procedures_by_id: dict[str, dict]) -> dict:
    """One requirement -> {id, type, satisfied, waived, detail}. Never raises
    on odd episodes; an unevaluable requirement reports unsatisfied with the
    reason in detail (an assertion that cannot be checked asserts nothing)."""
    rid, rtype = req.get("id"), req.get("type")
    out = {"id": rid, "type": rtype, "satisfied": False,
           "waived": False, "detail": ""}

    def _journal_calls(tool: str, procedure_id: str | None = None) -> list[dict]:
        return [e for e in journal
                if e.get("tool") == tool
                and (procedure_id is None
                     or e.get("procedure_id") == procedure_id)]

    if rtype == "max_tokens":
        used = int(episode.get("tokens_in") or 0) + int(
            episode.get("tokens_out") or 0)
        budget = req["budget"]
        out["satisfied"] = used <= budget
        out["detail"] = f"tokens_used={used} budget={budget}"
        return out

    # Trail requirements: waive when the arm had no substrate path at all.
    if not had_substrate_contact(episode, journal):
        out["waived"] = True
        out["detail"] = "no surface/memory contact - trail not applicable"
        return out

    if rtype == "gate_consulted":
        calls = _journal_calls("check_applicability", req.get("procedure_id"))
        out["satisfied"] = bool(calls)
        out["detail"] = (f"check_applicability called "
                         f"{len(calls)}x (verdicts="
                         f"{[c.get('verdict') for c in calls]})")
    elif rtype == "refusal_recorded":
        pid = req.get("procedure_id")
        on_surface = bool(_journal_calls("record_refusal", pid))
        on_episode = pid in (episode.get("refused_procedure_ids") or [])
        out["satisfied"] = on_surface and on_episode
        out["detail"] = f"surface_refusal={on_surface} episode_refusal={on_episode}"
    elif rtype == "no_stale_execution":
        reused = episode.get("reused_procedure_ids") or []
        stale_used = [pid for pid in reused
                      if (procedures_by_id.get(pid) or {}).get("stale")]
        out["satisfied"] = not stale_used
        out["detail"] = ("clean" if not stale_used
                         else f"executed stale procedures: {stale_used}")
    elif rtype == "reuse_verified_procedure":
        pid = req.get("procedure_id")
        proc = procedures_by_id.get(pid) or {}
        used = pid in (episode.get("reused_procedure_ids") or [])
        verified = bool(proc.get("verified", True))
        out["satisfied"] = used and verified and bool(episode.get("resolved"))
        out["detail"] = f"reused={used} verified={verified} resolved={bool(episode.get('resolved'))}"
    return out


def grade_scenario(task: dict, scenario: dict, episode: dict,
                   journal: list[dict],
                   procedures_by_id: dict[str, dict]) -> dict:
    """Episode + scenario criteria -> verdict row for one arm."""
    must_resolve = bool(scenario["success_criteria"]["must_resolve"])
    resolved = bool(episode.get("resolved"))
    outcome_ok = resolved == must_resolve
    checks = [
        check_requirement(r, episode, journal, procedures_by_id)
        for r in scenario.get("evidence_requirements", [])]
    applicable = [c for c in checks if not c["waived"]]
    scenario_pass = outcome_ok and all(c["satisfied"] for c in applicable)
    return {
        "scenario_id": scenario["scenario_id"],
        "archetype": scenario.get("archetype"),
        "arm": episode.get("arm"),
        "resolved": resolved,
        "must_resolve": must_resolve,
        "outcome_ok": outcome_ok,
        "trail_waived": bool(checks) and len(applicable) < len(checks),
        "evidence": checks,
        "n_evidence_applicable": len(applicable),
        "n_evidence_satisfied": sum(1 for c in applicable if c["satisfied"]),
        "failed_requirements": [c["id"] for c in applicable
                                if not c["satisfied"]],
        "false_reuse": bool(episode.get("reuse_caused_failure")),
        "scenario_pass": scenario_pass,
    }


def dry_run_verdict(task: dict, episodes_by_arm: dict) -> dict:
    """Real-corpus rows are pipeline exercises: reported, never scored."""
    return {
        "task_id": task["task_id"],
        "kind": "dry_run_corpus",
        "arms_exercised": sorted(episodes_by_arm),
        "all_valid": all(e.get("valid", True) for e in episodes_by_arm.values()),
    }


def render_verdicts(verdicts: list[dict], n_dry_runs: int = 0) -> str:
    """Per-scenario table. Failing requirement ids travel WITH the verdict —
    same never-a-bare-estimate discipline as the scoreboard footer."""
    lines = ["MICRO PACK PER-SCENARIO VERDICTS (synthetic scenarios — "
             "pipeline exercises, NOT findings)"]
    hdr = (f"{'scenario':<16}{'archetype':<30}{'arm':<4}"
           f"{'outcome':>9} {'evidence':>12}  {'verdict':<7}failures")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    order = {v["scenario_id"]: i for i, v in enumerate(verdicts)}
    for v in sorted(verdicts, key=lambda x: order[x["scenario_id"]]):
        ev = ("waived" if v["trail_waived"]
              else f"{v['n_evidence_satisfied']}/{v['n_evidence_applicable']}")
        lines.append(
            f"{v['scenario_id']:<16}{v['archetype']:<30}{v['arm']:<4}"
            f"{'ok' if v['outcome_ok'] else 'MISMATCH':>9} {ev:>12}  "
            f"{'PASS' if v['scenario_pass'] else 'FAIL':<7}"
            f"{','.join(v['failed_requirements']) or '-'}")
    tallies = {}
    for v in verdicts:
        w, t = tallies.get(v["arm"], (0, 0))
        tallies[v["arm"]] = (w + (1 if v["scenario_pass"] else 0), t + 1)
    lines.append("")
    lines.append("per-arm scenario passes: " +
                 "  ".join(f"{a}: {w}/{t}" for a, (w, t) in sorted(tallies.items())))
    if n_dry_runs:
        lines.append(f"dry-run real-corpus tasks exercised (unscored): {n_dry_runs}")
    return "\n".join(lines)


def summarize_pack(verdicts: list[dict]) -> dict:
    by_arch: dict[str, dict[str, list[int]]] = {}
    for v in verdicts:
        slot = by_arch.setdefault(v["archetype"], {})
        w, t = slot.get(v["arm"], (0, 0))
        slot[v["arm"]] = (w + (1 if v["scenario_pass"] else 0), t + 1)
    return {
        "n_verdicts": len(verdicts),
        "by_arm": {
            arm: {
                "passes": sum(1 for v in verdicts
                              if v["arm"] == arm and v["scenario_pass"]),
                "n": sum(1 for v in verdicts if v["arm"] == arm),
                "false_reuse": sum(1 for v in verdicts
                                   if v["arm"] == arm and v["false_reuse"]),
            }
            for arm in sorted({v["arm"] for v in verdicts})},
        "by_archetype": {a: {arm: {"passes": w, "n": t}
                             for arm, (w, t) in slot.items()}
                         for a, slot in by_arch.items()},
    }
