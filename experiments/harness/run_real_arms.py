"""
Real-arms runner (board Lane MEASURE-WAVE item 0): drives the three arms
over live OpenRouter calls through openrouter_arms.build_agents, appending
the same resumable JSONL the scripted runners use, then prints the UNCHANGED
scoreboard + power footer.

Spend protection is structural: an existing non-empty results file refuses
to run again WITHOUT --auto-resume (resume = skip every task whose row
already holds valid episodes from all arms; error rows and unparseable-
decision rows retry). The spend ledger (one row PER ATTEMPT) lives beside
the results file and its totals print with the scoreboard - a saturated-
pool sweep shows its attempt profile, not just its bill.

Offline sanity: --dry-run validates fixtures, builds and prints each arm's
prompt for the selected tasks, touches no network and needs no key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import micro_pack  # noqa: E402
import openrouter_arms  # noqa: E402
import run_harness  # noqa: E402
import scoreboard  # noqa: E402
import scripted_arms  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures-dir", default=str(HERE / "fixtures" / "micro"))
    ap.add_argument("--out", default=str(HERE / "real_arms_results.jsonl"))
    ap.add_argument("--spend-log", default=None,
                    help="default: <out stem>_spend.jsonl beside --out")
    ap.add_argument("--task-ids", default=None,
                    help="comma-separated subset; default all fixture tasks")
    ap.add_argument("--models", default=None,
                    help="comma-separated fallback chain; default "
                         f"{','.join(openrouter_arms.DEFAULT_MODEL_CHAIN)}")
    ap.add_argument("--auto-resume", action="store_true",
                    help="skip tasks already holding valid all-arm rows; "
                         "without it an existing results file is refused")
    ap.add_argument("--max-tasks", type=int, default=None,
                    help="cap on NEW tasks this invocation may start "
                         "(spend guard)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate fixtures + print prompts; no network, "
                         "no key required")
    return ap


def _c_surface(agents: dict):
    return getattr(agents.get("C"), "surface", None)


async def _run_task(task: dict, agents: dict) -> dict:
    rec: dict = {"task_id": task["task_id"]}
    for arm in scripted_arms.ARMS:
        ep = await agents[arm].arun(task)
        ep.setdefault("arm", arm)
        rec[arm] = ep
        state = ("INVALID:" + (ep.get("invalid_reason") or "?"))
        print(f"      . {arm} "
              f"{'PASS' if ep['resolved'] else 'fail'}"
              f"{' ' + state if not ep['valid'] else ''} "
              f"tok={ep['tokens_in'] + ep['tokens_out']:,} "
              f"tools={ep['tool_calls']}", flush=True)
    return rec


def dry_run(args, tasks: list[dict]) -> int:
    fixtures = Path(args.fixtures_dir)
    problems = []
    if (fixtures / "scenarios.json").exists():
        problems = micro_pack.validate_micro_fixtures(fixtures)
    if problems:
        print("MICRO PACK REFUSES TO RUN - fixture validation failed:")
        for p in problems:
            print(f"  - {p}")
        return 2
    situations = openrouter_arms.load_situations(fixtures)
    blob_path = fixtures / "rag_corpus.json"
    blobs = {}
    if blob_path.exists():
        blobs = {b["domain"]: b["text"] for b in json.loads(
            blob_path.read_text(encoding="utf-8"))["blobs"]}
    stub = None
    if (fixtures / "procedures.json").exists():
        import mcp_surface
        stub = mcp_surface.StubSurface(fixtures)
    for task in tasks:
        print(f"\n=== {task['task_id']} ===")
        offered = []
        if stub:
            by_id = {c["procedure_id"]: c
                     for c in stub.search(task.get("domain", ""))}
            for pid in filter(None, (task.get("applicable_procedure"),
                                     task.get("stale_offer"))):
                if pid in by_id:
                    offered.append(by_id[pid])
        messages = openrouter_arms.build_messages("A", task, situations)
        messages_b = openrouter_arms.build_messages(
            "B", task, situations,
            rag_blob=(blobs.get(task.get("domain"))
                      if task.get("rag", "none") != "none" else None))
        messages_c = openrouter_arms.build_messages(
            "C", task, situations, offered_cards=offered or None)
        for label, msgs in (("A", messages), ("B", messages_b),
                            ("C", messages_c)):
            print(f"  [{label}] system={len(msgs[0]['content'])}ch "
                  f"user={len(msgs[1]['content'])}ch")
            print(f"    user> {msgs[1]['content'][:220]}...")
    print(f"\ndry-run ok: {len(tasks)} task(s) x 3 arms would be sent to "
          f"{openrouter_arms.DEFAULT_MODEL_CHAIN[0]} (chain of "
          f"{len(openrouter_arms.DEFAULT_MODEL_CHAIN)}). No calls made.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fixtures = Path(args.fixtures_dir)
    out = Path(args.out)
    spend_path = (Path(args.spend_log) if args.spend_log
                  else out.with_name(out.stem + "_spend.jsonl"))

    tasks = json.loads((fixtures / "tasks.json").read_text(
        encoding="utf-8"))["tasks"]
    wanted = ({t.strip() for t in args.task_ids.split(",") if t.strip()}
              if args.task_ids else None)
    picked = [t for t in tasks if wanted is None or t["task_id"] in wanted]

    if args.dry_run:
        return dry_run(args, picked)

    if out.exists() and out.stat().st_size > 0 and not args.auto_resume:
        print(f"REFUSING to overwrite paid history: {out} exists.\n"
              f"Re-run with --auto-resume to skip completed tasks and "
              f"retry error/invalid rows.")
        return 2

    api_key = openrouter_arms.resolve_api_key()
    if not api_key:
        print("OPENROUTER_API_KEY not found (process env, then "
              "backend/.env). Refusing to start a billed run anonymously.")
        return 2

    models = (tuple(m.strip() for m in args.models.split(",") if m.strip())
              if args.models else ())
    done = run_harness.load_done(out) if args.auto_resume else set()
    new_tasks = [t for t in picked if t["task_id"] not in done]
    if args.max_tasks is not None:
        new_tasks = new_tasks[:args.max_tasks]
    print(f"real-arms sweep (LIVE model calls - outcomes are findings-in-"
          f"training, costs are real)")
    print(f"{len(picked)} selected, {len(done)} done via resume, "
          f"{len(new_tasks)} to run; chain: "
          f"{', '.join(models or openrouter_arms.DEFAULT_MODEL_CHAIN)}")

    spend = openrouter_arms.SpendLog(spend_path)
    agents = openrouter_arms.build_agents(fixtures, api_key, models=models,
                                          spend=spend)
    surface = _c_surface(agents)

    async def sweep():
        rows_written = 0
        for i, task in enumerate(new_tasks, 1):
            tid = task["task_id"]
            print(f"\n[{i}/{len(new_tasks)}] {tid} ({task.get('domain', '')}"
                  f"{', unseen' if task.get('unseen') else ''})", flush=True)
            journal_mark = len(surface.journal()) if surface else 0
            try:
                rec = await _run_task(task, agents)
            except Exception as exc:  # noqa: BLE001
                import traceback
                rec = {"task_id": tid,
                       "error": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc()[-2000:]}
                print(f"    error: {rec['error']}", flush=True)
            if surface and not rec.get("error"):
                rec["C_journal"] = surface.journal()[journal_mark:]
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            rows_written += 1
        return rows_written

    rows_written = asyncio.run(sweep())

    if out.exists():
        try:
            text, _detail = scoreboard.build_summary([out], fixtures)
            print("\n" + text)
        except Exception as exc:  # noqa: BLE001
            # e.g. a sweep where every task errored: rows exist, no usable
            # pair set - report honestly instead of crashing on the render.
            print(f"\n[scoreboard skipped: {type(exc).__name__}: {exc}]")
    print(spend.render())
    print(f"[rows appended this run: {rows_written}; spend ledger -> "
          f"{spend_path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
