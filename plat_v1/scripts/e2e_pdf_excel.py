"""
End-to-end check against a real database.

Separate from `tests/` on purpose: everything in there passes with no
database and no API keys, and this needs both a Postgres with pgvector and a
seeded graph. Run it the way backend_v2 runs its integration checks -- by
hand, against a real instance, when something structural has changed.

    python scripts/seed.py
    python scripts/e2e_pdf_excel.py

What it asserts:

  1. a real PDF goes in and an .xlsx comes out
  2. a trace row exists for every stage
  3. an identical second run hits the cache and does strictly less work
  4. retrieval finds the seeded tasks from a natural-language prompt
  5. a document with the same layout and different content also hits the cache

Check 3 measures attempts and cost. With only deterministic implementations
in play the cost of both runs is zero and the saving shows up as attempts;
with ANTHROPIC_API_KEY set the script additionally runs a variant whose
column names do not match the target schema, which forces the model mapping
on the first run and replays it for free on the second. That second variant
is where "the cache makes marginal cost collapse" is actually demonstrated,
so run it that way at least once.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()

from make_sample_pdf import DEFAULT_ROWS, VARIANT_ROWS, write_sample  # noqa: E402

from app.api.deps import build_services  # noqa: E402
from app.db import create_pool, verify_isolation  # noqa: E402
from app.services import runs as run_store  # noqa: E402
from app.services.typecheck import typecheck, typecheck_report  # noqa: E402

# Column names that line up with the PDF's headers, so the free template
# match wins and no API key is needed.
MATCHING_SCHEMA = {
    "properties": {"sku": {}, "description": {}, "qty": {}, "unit_price": {}, "ordered": {}}
}
# Names that do not line up, forcing the model mapping on a first-time layout.
RENAMED_SCHEMA = {
    "properties": {"part_number": {}, "item_name": {}, "units": {}, "price_each": {}}
}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{f' -- {detail}' if detail else ''}")
    if not condition:
        failures.append(label)


async def run_once(pool, services, pdf: Path, target_schema: dict, label: str):
    task = await services.graph.get_task_by_name("pdf_to_excel")
    if task is None:
        raise SystemExit("pdf_to_excel is not seeded. Run scripts/seed.py first.")

    plan = await services.graph.plan_for_task(task)
    context = await services.graph.load_typecheck_context(plan)
    problems = typecheck(plan, context)
    if problems:
        for message in typecheck_report(problems)["messages"]:
            print(f"    {message}")
        raise SystemExit("the seeded workflow does not typecheck")

    inputs = {"pdf_path": str(pdf), "target_schema": target_schema}
    run_id = await run_store.create_run(pool, f"e2e {label}", inputs, plan, "running")
    result = await services.executor.execute(plan, inputs, run_id=run_id)
    await run_store.finish_run(pool, run_id, result)
    return run_id, result


async def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set")
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="plat_v1_e2e_"))
    invoice = write_sample(workdir / "invoice.pdf", DEFAULT_ROWS)
    same_layout = write_sample(workdir / "other_invoice.pdf", VARIANT_ROWS)
    print(f"sample documents in {workdir}\n")

    pool = await create_pool()
    try:
        # Before anything writes. Without this, an unseeded schema sends the
        # unqualified INSERTs at backend_v2's tables; the trace insert would
        # fail on their different columns, PostgresTraceRecorder swallows the
        # error by design, and the script would report "no trace row per
        # stage" -- pointing at the executor instead of at the database.
        async with pool.acquire() as conn:
            await verify_isolation(conn)

        services = build_services(pool)

        # -- 1 & 2: a PDF becomes a spreadsheet, and every stage is traced ---
        print("run 1: a PDF becomes a spreadsheet")
        run_id, result = await run_once(pool, services, invoice, MATCHING_SCHEMA, "first")

        check("run succeeded", result.status == "succeeded", result.error or "")
        output_path = result.outputs.get("path")
        check("an .xlsx was produced", bool(output_path) and Path(str(output_path)).exists(),
              str(output_path))
        check("row count matches the PDF", result.outputs.get("row_count") == len(DEFAULT_ROWS),
              str(result.outputs.get("row_count")))

        summary = await run_store.load_run(pool, run_id)
        traced = {s.node_ref for s in summary.stages}
        # Six leaves plus the composite roll-up.
        check("a trace row exists per stage", len(traced) >= 6,
              f"{len(traced)} distinct stages traced")
        check("failures are traced too, not just successes",
              all(s.outcome in ("success", "failure") for s in summary.stages))

        first_attempts = sum(s.attempts for s in summary.stages)
        first_cost = summary.total_cost
        print(f"    {len(summary.stages)} trace rows, {first_attempts} attempts, "
              f"cost {first_cost:.4f}")

        # -- 3: the same document again, from cache ------------------------
        print("\nrun 2: the identical document again")
        run_id_2, result_2 = await run_once(pool, services, invoice, MATCHING_SCHEMA, "repeat")
        summary_2 = await run_store.load_run(pool, run_id_2)

        check("run succeeded", result_2.status == "succeeded", result_2.error or "")
        check("at least one stage came from the cache",
              any(s.cache_hit for s in summary_2.stages),
              f"{sum(1 for s in summary_2.stages if s.cache_hit)} cached stages")

        second_attempts = sum(s.attempts for s in summary_2.stages)
        check("no more work than the first run",
              second_attempts <= first_attempts and summary_2.total_cost <= first_cost,
              f"{second_attempts} attempts vs {first_attempts}, "
              f"cost {summary_2.total_cost:.4f} vs {first_cost:.4f}")

        # -- 5: a different document with the same layout ------------------
        print("\nrun 3: a different document with the same layout")
        run_id_3, result_3 = await run_once(
            pool, services, same_layout, MATCHING_SCHEMA, "same layout"
        )
        summary_3 = await run_store.load_run(pool, run_id_3)
        check("run succeeded", result_3.status == "succeeded", result_3.error or "")
        check("the layout cache applied to a document it had never seen",
              any(s.cache_hit for s in summary_3.stages),
              "this is the property a content hash would destroy")

        # -- 4: retrieval --------------------------------------------------
        print("\nretrieval")
        matches = await services.matcher.search(
            "turn the tables in this pdf into a spreadsheet", top_k=5
        )
        names = [m.task.name for m in matches]
        check("retrieval returns the seeded workflow", "pdf_to_excel" in names, ", ".join(names))
        if matches:
            print(f"    top: {matches[0].task.name} at {matches[0].score:.4f} "
                  f"({'+'.join(matches[0].matched_by)})")

        # -- the model path, when a key is available ------------------------
        if os.environ.get("ANTHROPIC_API_KEY"):
            print("\nmodel mapping: a target schema whose names do not match the columns")
            os.environ["ALLOW_UNREVIEWED_FIRST_LAYOUT_MAPPING"] = "true"
            from app.config import settings

            settings.allow_unreviewed_first_layout_mapping = True

            renamed = write_sample(workdir / "renamed.pdf", DEFAULT_ROWS)
            run_a, result_a = await run_once(pool, services, renamed, RENAMED_SCHEMA, "model 1")
            summary_a = await run_store.load_run(pool, run_a)
            check("first run succeeded via the model", result_a.status == "succeeded",
                  result_a.error or "")

            run_b, result_b = await run_once(pool, services, renamed, RENAMED_SCHEMA, "model 2")
            summary_b = await run_store.load_run(pool, run_b)
            check("second run succeeded", result_b.status == "succeeded", result_b.error or "")
            check("the second run cost strictly less",
                  summary_b.total_cost < summary_a.total_cost,
                  f"{summary_b.total_cost:.4f} vs {summary_a.total_cost:.4f}")
        else:
            print("\nANTHROPIC_API_KEY is not set -- skipping the model-mapping check.")
            print("  The cost-collapse property is only demonstrated with it. Run once with a key.")

        print()
        if failures:
            print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
            return 1
        print("all checks passed")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
