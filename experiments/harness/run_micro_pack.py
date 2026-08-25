"""
Run the micro-experiment pack (board MEASURE item 3): the 8-12 tiny
real-life scenarios in fixtures/micro through the same three-arm pipeline as
the skeleton sweep, then grade per scenario with evidence-trail assertions.

Optionally appends dry-run tasks from a session_corpus.py manifest so real
Claude Code prompts flow through all three arms as unscored pipeline
exercises (the P4 dogfooding seed).

Everything offline, deterministic, backend-import-free (lane rule). Output:
the usual resumable JSONL plus a verdict/scoreboard detail JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import micro_pack  # noqa: E402
import run_harness  # noqa: E402
import scoreboard  # noqa: E402
import scripted_arms  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures-dir", default=str(HERE / "fixtures" / "micro"))
    ap.add_argument("--out", default=str(HERE / "micro_results.jsonl"))
    ap.add_argument("--task-ids", default=None,
                    help="comma-separated scenario subset; default all")
    ap.add_argument("--corpus-manifest", default=None,
                    help="session_corpus.py manifest; dry-run tasks appended "
                         "after the scenarios")
    return ap


def _c_surface(agents: dict):
    """Arm C's StubSurface, when this build has one (evidence-trail source)."""
    return getattr(agents.get("C"), "surface", None)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fixtures = Path(args.fixtures_dir)
    out = Path(args.out)

    problems = micro_pack.validate_micro_fixtures(fixtures)
    if problems:
        print("MICRO PACK REFUSES TO RUN — fixture validation failed:")
        for p in problems:
            print(f"  - {p}")
        return 2

    tasks = json.loads((fixtures / "tasks.json").read_text(
        encoding="utf-8"))["tasks"]
    scenarios = micro_pack.load_scenarios(fixtures)
    procedures_by_id = micro_pack.scoring_load_procedures(fixtures)

    corpus_tasks: list[dict] = []
    if args.corpus_manifest:
        import session_corpus
        corpus_tasks = session_corpus.corpus_tasks(args.corpus_manifest)

    wanted = None
    if args.task_ids:
        wanted = {t.strip() for t in args.task_ids.split(",") if t.strip()}
    picked_scenarios = [t for t in tasks
                        if wanted is None or t["task_id"] in wanted]
    picked_corpus = [t for t in corpus_tasks
                     if wanted is None or t["task_id"] in wanted]

    done = run_harness.load_done(out)
    print(f"micro-experiment pack (SYNTHETIC scenarios + optional real-corpus "
          f"dry runs — outcomes are pipeline exercises, NOT findings)")
    print(f"{len(picked_scenarios)} scenarios, {len(picked_corpus)} corpus "
          f"dry-runs selected, {len(done)} already done")

    agents = scripted_arms.build_agents(fixtures)
    surface = _c_surface(agents)

    new_rows: list[dict] = []
    for i, task in enumerate(picked_scenarios + picked_corpus, 1):
        tid = task["task_id"]
        if tid in done:
            print(f"[{i}] skip (done) {tid}")
            continue
        kind = ("scenario" if tid in scenarios else
                "dry-run corpus" if task.get("dry_run") else "task")
        arch = scenarios.get(tid, {}).get("archetype", task.get("domain", ""))
        print(f"\n[{i}] {tid} ({kind}: {arch})", flush=True)
        journal_mark = len(surface.journal()) if surface is not None else 0
        try:
            rec = run_harness.run_task(task, agents)
        except Exception as exc:  # noqa: BLE001
            import traceback
            rec = {"task_id": tid, "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()[-2000:]}
            print(f"    error: {rec['error']}", flush=True)
        if surface is not None and not rec.get("error"):
            # Slice of C's journal attributable to THIS task (single-threaded
            # deterministic loop; the stub is append-only).
            rec["C_journal"] = surface.journal()[journal_mark:]
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        new_rows.append(rec)

    rows = scoreboard.load_rows(out) if out.exists() else []
    by_id = {r["task_id"]: r for r in rows}
    scenario_verdicts: list[dict] = []
    dry_verdicts: list[dict] = []
    for task in picked_scenarios + picked_corpus:
        rec = by_id.get(task["task_id"])
        if not rec or rec.get("error"):
            continue
        episodes = {a: rec[a] for a in scripted_arms.ARMS
                    if isinstance(rec.get(a), dict)}
        if task["task_id"] in scenarios and task["task_id"] not in (
                t["task_id"] for t in picked_corpus):
            sc = scenarios[task["task_id"]]
            for arm, ep in episodes.items():
                # The journal is arm C's surface path; other arms grade with
                # an empty one (their trail requirements waive).
                journal = rec.get("C_journal") or [] if arm == "C" else []
                scenario_verdicts.append(micro_pack.grade_scenario(
                    task, sc, ep, journal, procedures_by_id))
        elif task.get("dry_run"):
            dry_verdicts.append(micro_pack.dry_run_verdict(task, episodes))

    print("\n" + micro_pack.render_verdicts(
        scenario_verdicts, n_dry_runs=len(dry_verdicts)))

    text, sb_detail = scoreboard.build_summary(
        [out], fixtures,
        banner="MICRO PACK SCOREBOARD (synthetic scenarios)")
    print("\n" + text)

    detail_path = out.with_name(out.stem + "_detail.json")
    detail_path.write_text(json.dumps({
        "pack": micro_pack.summarize_pack(scenario_verdicts),
        "verdicts": scenario_verdicts,
        "dry_runs": dry_verdicts,
        "scoreboard": sb_detail,
        "fixture_problems": [],
    }, indent=2), encoding="utf-8")
    print(f"[per-scenario verdicts + scoreboard detail -> {detail_path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
