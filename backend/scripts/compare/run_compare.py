"""Run the comparison: tasks x arms x repeats, scored, paired and summarised (docs/goal_router.md P2-05).

    python -m scripts.compare.run_compare --tasks web-001,web-002 --arms procedure,tool --repeats 2 --dry-run
    python -m scripts.compare.run_compare --tasks web-001,web-002 --arms procedure,tool --repeats 2
    python -m scripts.compare.run_compare --goal security --arms procedure,tool,baseline --repeats 3 --budget-usd 2

Arms: procedure (scripts/compare/procedure_runner.py), tool (tool_runner.py, through the gate) and baseline (the model
alone: no procedure, no tool; OpenRouter, or DeepInfra once OpenRouter is out of credit; COMPARE_BASELINE_MODEL, default
openai/gpt-oss-120b). Every attempt is a RunRecord stored in task_runs. Attempt i of one arm is paired with attempt i
of the others, and each (task, procedure, card) gets one ComparisonRecord. The winner here is only a direction:
fewer than 3 pairs is "undecided"; confidence intervals and the verdict are scripts/compare/report.py's (P2-07).

Guardrails: --budget-usd caps the whole run (an attempt is not started unless its task's budget still fits; the run
stops cleanly and says so); an arm that errors 3 times in a row on a task stops for that task; --dry-run prints the
plan and spends nothing. --out writes the records as JSON. When scripts/compare/store.py (P2-06) exists, the
comparisons are saved through it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from scripts.compare.contracts import ARMS, GOALS, ComparisonRecord, RunRecord, Task
from scripts.compare.corpus import load
from scripts.compare.procedure_runner import dsn, resolve_goal
from scripts.compare.scoring import score, sha

BASELINE_MODEL = os.environ.get("COMPARE_BASELINE_MODEL", "openai/gpt-oss-120b")
# the same model on two OpenAI-compatible providers: OpenRouter first; DeepInfra when OpenRouter's credit is used up
BASELINE_PROVIDERS = (("openrouter", "https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
                      ("deepinfra", "https://api.deepinfra.com/v1/openai/chat/completions", "DEEPINFRA_KEY"))
_SPENT: set[str] = set()   # providers that answered 402 in this process
MAX_CONSECUTIVE_ERRORS = 3
MIN_PAIRS_FOR_DIRECTION = 3


def _key(name: str) -> str:
    if name in os.environ:
        return os.environ[name].strip()
    from dotenv import dotenv_values

    return str(dotenv_values(Path(__file__).resolve().parents[2] / ".env").get(name) or "").strip()


async def run_baseline(pool: Any, task: Task, *, store: bool = True) -> RunRecord:
    """The model alone answers the task; scored like the other arms."""
    import httpx

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    goal_key, _goal_id = await resolve_goal(pool, task)
    status, error_class, cost, tin, tout, sc = "error", None, 0.0, 0, 0, None
    providers = [(n, u, _key(k)) for n, u, k in BASELINE_PROVIDERS if n not in _SPENT and _key(k)]
    if not providers:
        error_class = "baseline:not_configured"
    else:
        body = {"model": BASELINE_MODEL, "temperature": 0, "seed": 7, "max_tokens": 600, "usage": {"include": True},
                "messages": [{"role": "system", "content": "Answer the question directly and briefly. If it asks for "
                                                           "SQL, answer with one SQLite query in a ```sql block."},
                             {"role": "user", "content": task.prompt}]}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                for name, url, key in providers:
                    for wait in (0, 2, 5, 15, 30):   # a rate limit or a busy model is retried, then counted
                        if wait:
                            await asyncio.sleep(wait)
                        r = await client.post(url, json=body if name == "openrouter" else
                                              {k: v for k, v in body.items() if k != "usage"},
                                              headers={"authorization": f"Bearer {key}"})
                        if r.status_code not in (429, 500, 502, 503, 504):
                            break
                    if r.status_code == 402:          # out of credit: the next provider, for the rest of the run
                        _SPENT.add(name)
                        continue
                    break
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage") or {}
            cost = float(usage.get("cost") or usage.get("estimated_cost") or 0.0)
            tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
            answer = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            if cost > task.budget_usd:
                error_class = "budget"
            else:
                sc = await score(task, answer)
                status = sc.status
                error_class = None if sc.status != "error" else f"score:{sc.detail[:60]}"
        except Exception as exc:  # noqa: BLE001 -- a failed model call is the arm's error, recorded
            error_class = f"baseline:{type(exc).__name__}"
    record = RunRecord(run_id="", task_id=task.id, arm="baseline", goal_id=goal_key, procedure_id=None, card_id=None,
                       started_at=started.isoformat(), latency_ms=int((time.monotonic() - t0) * 1000),
                       cost_usd=round(cost, 6), tokens_in=tin, tokens_out=tout, status=status,
                       score=sc.score if sc else 0.0, artifact_hash=sc.raw_hash if sc else None,
                       error_class=error_class, trace_ref=sha(f"baseline {BASELINE_MODEL}"))
    if store:
        from app.tool_routing.runs import finish_run, start_run

        record.run_id = await start_run(pool, task_id=task.id, arm="baseline", goal_id=goal_key, procedure_id=None,
                                        card_id=None)
        await finish_run(pool, record.run_id, {"status": record.status, "latency_ms": record.latency_ms,
                                               "cost_usd": record.cost_usd, "tokens_in": tin, "tokens_out": tout,
                                               "score": record.score, "artifact_hash": record.artifact_hash,
                                               "error_class": record.error_class, "trace_ref": record.trace_ref})
    return record


async def run_arm(pool: Any, arm: str, task: Task) -> RunRecord:
    if arm == "procedure":
        from scripts.compare.procedure_runner import run_task

        return (await run_task(pool, task))[0]
    if arm == "tool":
        from scripts.compare.tool_runner import run_task

        return (await run_task(pool, task))[0]
    return await run_baseline(pool, task)


def compare(task: Task, runs: dict[str, list[RunRecord]]) -> ComparisonRecord:
    """One ComparisonRecord for the task: attempts paired by index across the procedure and tool arms."""
    proc, tool = runs.get("procedure", []), runs.get("tool", [])
    n = min(len(proc), len(tool))
    deltas = [tool[i].score - proc[i].score for i in range(n)]
    cost_d = [tool[i].cost_usd - proc[i].cost_usd for i in range(n)]
    lat_d = [tool[i].latency_ms - proc[i].latency_ms for i in range(n)]
    paired = mean(deltas) if deltas else 0.0
    if n < MIN_PAIRS_FOR_DIRECTION:
        winner = "undecided"
    elif paired > 0:
        winner = "tool"
    elif paired < 0:
        winner = "procedure"
    else:
        winner = "tie"
    first = lambda arm, attr: next((getattr(r, attr) for r in runs.get(arm, []) if getattr(r, attr)), None)
    return ComparisonRecord(
        task_id=task.id, goal_id=first("tool", "goal_id") or first("procedure", "goal_id") or task.goal_hint,
        procedure_id=first("procedure", "procedure_id"), card_id=first("tool", "card_id"), n_pairs=n,
        scores={a: round(mean(r.score for r in rs), 4) for a, rs in runs.items() if rs},
        pass_rates={a: round(sum(r.status == "pass" for r in rs) / len(rs), 4) for a, rs in runs.items() if rs},
        paired_delta=round(paired, 4), cost_delta_usd=round(mean(cost_d), 6) if cost_d else 0.0,
        latency_delta_ms=round(mean(lat_d), 1) if lat_d else 0.0, winner=winner,
        run_ids=[r.run_id for rs in runs.values() for r in rs if r.run_id])


def plan(tasks: list[Task], arms: list[str], repeats: int) -> list[tuple[Task, str, int]]:
    """Interleaved: every arm's attempt i of a task runs before attempt i+1, so a stop leaves pairs, not one arm."""
    return [(t, arm, i) for t in tasks for i in range(1, repeats + 1) for arm in arms]


async def main_async(args: argparse.Namespace) -> int:
    ids = [s.strip() for s in args.tasks.split(",")] if args.tasks else None
    tasks = load(ids=ids, goal=args.goal)
    if not tasks:
        raise SystemExit("no tasks selected")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in ARMS]
    if bad:
        raise SystemExit(f"unknown arm(s): {', '.join(bad)}; arms are {', '.join(ARMS)}")
    steps = plan(tasks, arms, args.repeats)
    worst = sum(t.budget_usd for t, _a, _i in steps)
    print(f"plan: {len(tasks)} task(s) x {len(arms)} arm(s) x {args.repeats} repeat(s) = {len(steps)} attempts; "
          f"worst-case spend ${worst:.2f}; run cap ${args.budget_usd:.2f}")
    if args.dry_run:
        for t, arm, i in steps:
            print(f"  {t.id:<10} {arm:<10} attempt {i}  goal {t.goal_hint:<12} cap ${t.budget_usd:.2f}  "
                  f"verifier {t.verifier['kind']}")
        print("dry run: nothing was run or spent")
        return 0

    url = dsn(args.dsn)
    if not url:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    pool = await asyncpg.create_pool(url, min_size=1, max_size=4, timeout=30)
    runs: dict[str, dict[str, list[RunRecord]]] = {t.id: {a: [] for a in arms} for t in tasks}
    spent, stopped, streak = 0.0, "", {}
    batch = str(uuid.uuid4())[:8]
    try:
        for t, arm, i in steps:
            if spent + t.budget_usd > args.budget_usd:
                stopped = f"budget: ${spent:.4f} spent, the next attempt could exceed the ${args.budget_usd:.2f} cap"
                break
            if streak.get((t.id, arm), 0) >= MAX_CONSECUTIVE_ERRORS:
                continue
            rec = await run_arm(pool, arm, t)
            spent += rec.cost_usd
            runs[t.id][arm].append(rec)
            streak[(t.id, arm)] = streak.get((t.id, arm), 0) + 1 if rec.status == "error" else 0
            print(f"  {t.id:<10} {arm:<10} #{i}  {rec.status:<5} score {rec.score:.2f}  ${rec.cost_usd:.4f}  "
                  f"{rec.latency_ms}ms  {rec.error_class or ''}")
            if streak[(t.id, arm)] == MAX_CONSECUTIVE_ERRORS:
                print(f"  {t.id:<10} {arm:<10} stopped: {MAX_CONSECUTIVE_ERRORS} errors in a row")
        comparisons = [compare(t, runs[t.id]) for t in tasks if any(runs[t.id].values())]
        save = None
        try:
            from scripts.compare import store as _store

            save = getattr(_store, "save", None)
        except ImportError:
            pass
        if save is not None and comparisons:
            await save(pool, comparisons)
        # The runs marked their Goals stale (db/204); refresh them now so the next search
        # sees the new evidence. The app's background loop does the same on a timer.
        try:
            from app.tool_routing import ranking as _ranking

            await _ranking.drain(pool, limit=50, min_age_s=0)
        except Exception:  # noqa: BLE001 -- the comparison result is already saved
            pass
    finally:
        await pool.close()

    records = [r for t in tasks for rs in runs[t.id].values() for r in rs]
    print(f"\n{len(records)} RunRecords, {len(comparisons)} ComparisonRecords, ${spent:.4f} spent  (batch {batch})")
    print(f"{'task':<10} {'pairs':>5} " + " ".join(f"{a + ' score':>15}" for a in arms)
          + f" {'delta t-p':>9} {'cost d $':>9} {'lat d ms':>9}  winner")
    for c in comparisons:
        print(f"{c.task_id:<10} {c.n_pairs:>5} " + " ".join(f"{c.scores.get(a, float('nan')):>15.2f}" for a in arms)
              + f" {c.paired_delta:>9.2f} {c.cost_delta_usd:>9.4f} {c.latency_delta_ms:>9.0f}  {c.winner}")
    if stopped:
        print(f"stopped early: {stopped}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"batch": batch, "spent_usd": spent, "stopped": stopped,
                                              "runs": [asdict(r) for r in records],
                                              "comparisons": [asdict(c) for c in comparisons]}, indent=2),
                                  encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    ap = argparse.ArgumentParser(description="Compare the procedure, tool and baseline arms on corpus tasks.")
    ap.add_argument("--tasks", help="comma-separated task ids")
    ap.add_argument("--goal", choices=GOALS)
    ap.add_argument("--arms", default="procedure,tool")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--budget-usd", type=float, default=1.0, help="cap for the whole run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--dsn", default="")
    args = ap.parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
