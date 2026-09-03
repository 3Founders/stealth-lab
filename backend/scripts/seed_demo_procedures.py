"""Seed a small REAL demo set of procedures + Implementation Registry rows
so the registry MCP tools (resolve_implementation / list_task_implementations
/ inspect_implementation / get_implementation_capability) and the
"reuse a verified procedure" paths have real data to return.

problems.md finding D: the `implementations` table was empty and the
verified-procedure corpus was entirely test fixtures.

Everything goes through the REAL product write paths -- no ad-hoc INSERTs
into `procedures` / `implementations` / `evidence`:

  - `procedures.capture_procedure`        -> the procedure rows
  - `procedures.record_execution_outcome` -> real ticket-13 lifecycle: 10
        successful outcomes across 3 distinct contexts, 0 failures, drives
        `verification_state` candidate -> verified (each call also writes
        one real `execution_result` evidence row; the promotion trigger
        in db/30 fires in-transaction on the 10th). NOT a raw UPDATE.
  - `procedures.approve_procedure`        -> approval_status -> approved
  - `implementation_registry.register`    -> the implementation rows
        (+ `implementation_tasks` link via task_node_ids)
  - `implementation_registry.activate` / `.verify` -> real status /
        verification_status transitions (directive Sec 33/61 -- a caller
        decides WHEN, these functions only perform the transition)

The one direct INSERT is the anchor `task_nodes` row -- the same thing
`tests/test_implementation_registry_e2e.py` does (`INSERT INTO task_nodes
(name, skill_ref) ...`); there is no higher-level capture for a bare task
node, and the "no ad-hoc INSERT" rule is about the capture-gated tables
above, not the task anchor.

Every seeded row is tagged `scope_entity_id = 'demo-procedures'` /
`created_by = 'seed_demo_procedures'` (+ `domain_payload.demo_seed = true`
on procedures) so `--clear` removes exactly this set.

    python scripts/seed_demo_procedures.py            # create the demo set
    python scripts/seed_demo_procedures.py --dry-run  # print, touch nothing
    python scripts/seed_demo_procedures.py --clear     # remove just this set
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.db.session import create_pool  # noqa: E402
from app.execution import implementation_registry  # noqa: E402
from app.services.procedures import (  # noqa: E402
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)

_SCOPE_ENTITY = "demo-procedures"
_AUTHOR = "seed_demo_procedures"
_TASK_SKILL = "demo:ops-runbook"
_DOMAIN = "ops"

# (key, name, goal, [step goals], verify?)
_PROCEDURES: list[tuple[str, str, str, list[str], bool]] = [
    ("migration",
     "Roll a Postgres schema migration safely",
     "Apply a schema change to a live database with no downtime and a clean rollback path",
     ["Confirm the change is additive (new nullable column / new table / new index CONCURRENTLY); "
      "if it is destructive, split it into an expand step now and a contract step a release later",
      "Write the migration idempotent and re-runnable (IF NOT EXISTS / guarded DO blocks)",
      "Dry-run against a throwaway copy of production schema + a representative row sample",
      "Apply in a transaction where the engine allows it; take an explicit lock-timeout so a "
      "blocked migration fails fast instead of stalling writers",
      "Verify row counts and a checksum of the touched columns are unchanged for pre-existing data"],
     True),

    ("release",
     "Cut a patch release",
     "Ship a reviewed fix to production as a tagged patch release without disturbing the release line",
     ["Rebase the fix branch onto the current release head; resolve conflicts, re-run the fast suite",
      "Run the full offline suite + the targeted e2e for the changed surface; paste the counts",
      "Bump the patch version, update the changelog with the fix + its regression test",
      "Fast-forward the release branch, create an annotated tag, push branch then tag (never --force)",
      "Verify the tag resolves to the intended commit and the predecessor tags are unmoved"],
     True),

    ("ci_triage",
     "Triage a failing CI job",
     "Turn a red CI run into either a real defect with a repro or a quarantined known-flake",
     ["Read the actual failing step's log, not just the summary; capture the real error + traceback",
      "Re-run the single failing test in isolation locally to confirm it is genuinely failing",
      "If it fails identically on the last known-good commit, it is pre-existing -- record it and move on",
      "If it is new, bisect between known-good and the current head to find the causing change"],
     False),
]

# (name, kind, provider, description, locator, invocation, verify?, activate?)
_IMPLEMENTATIONS: list[tuple] = [
    ("pg-migrate-runner", "deterministic", "stealthlab.demo",
     "Deterministic runner that applies a single guarded SQL migration file inside a "
     "lock-timeout'd transaction and reports row-count + column-checksum deltas.",
     {"scheme": "local", "path": "scripts/migrate.py"},
     {"entrypoint": "migrate", "args_schema": {"migration_file": "string", "lock_timeout_ms": "integer"}},
     True, True),

    ("release-cutter", "tool", "stealthlab.demo",
     "Tool implementation that rebases a fix branch onto the release head, runs the offline + "
     "targeted e2e suites, bumps the patch version, tags, and pushes branch-then-tag.",
     {"scheme": "mcp", "tool": "cut_patch_release"},
     {"entrypoint": "cut_patch_release",
      "args_schema": {"fix_branch": "string", "release_branch": "string"}},
     False, True),
]


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------
async def _ensure_task_node(pool) -> str:
    row = await pool.fetchrow(
        "SELECT id::text FROM task_nodes WHERE skill_ref = $1 AND t_invalid IS NULL", _TASK_SKILL,
    )
    if row is not None:
        return row["id"]
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name, description, skill_ref, created_by, provenance, "
        " scope_type, scope_entity_id) "
        "VALUES ($1, $2, $3, $4, 'company_ingested', 'project', $5) RETURNING id::text",
        "Demo: run an ops runbook",
        "Anchor task_node for the demo procedure/implementation set "
        "(scripts/seed_demo_procedures.py).",
        _TASK_SKILL, _AUTHOR, _SCOPE_ENTITY,
    )
    return row["id"]


async def seed(pool) -> None:
    task_id = await _ensure_task_node(pool)
    print(f"  task_node {task_id}  (skill_ref={_TASK_SKILL!r})")

    proc_ids: dict[str, dict] = {}
    for i, (key, name, goal, step_goals, do_verify) in enumerate(_PROCEDURES):
        steps = [{"order": j, "goal": g} for j, g in enumerate(step_goals)]
        res = await capture_procedure(
            pool,
            name=name, goal=goal, steps=steps,
            provenance="prior_library", domain=_DOMAIN,
            domain_payload={"demo_seed": True, "seed_key": key},
            created_by=_AUTHOR,
            scope_type="project", scope_entity_id=_SCOPE_ENTITY,
            # link one procedure to the anchor task so the proc<->task
            # graph has a real DECOMPOSES/migrated edge to show.
            migrated_from_task_node_id=task_id if i == 0 else None,
        )
        proc_ids[key] = {"id": res["id"], "procedure_id": res["procedure_id"], "verified": False}
        print(f"  procedure {key:10s} row={res['id']}  procedure_id={res['procedure_id']}")

        if not do_verify:
            continue
        # REAL ticket-13 promotion: MIN_SUCCESSES_FOR_VERIFIED successful
        # outcomes across MIN_DISTINCT_CONTEXTS_FOR_VERIFIED contexts, 0
        # failures. Each call writes one execution_result evidence row;
        # the 10th trips the candidate->verified transition + db/30 gate
        # in the same transaction. No raw UPDATE.
        updated = None
        for n in range(MIN_SUCCESSES_FOR_VERIFIED):
            ctx = f"demo-ctx-{n % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}"
            updated = await record_execution_outcome(
                pool, procedure_row_id=res["id"], success=True, context_key=ctx,
                steps_used=len(steps),
            )
        if updated is None or updated["verification_state"] != "verified":
            raise RuntimeError(
                f"{key}: expected verification_state='verified' after "
                f"{MIN_SUCCESSES_FOR_VERIFIED} clean outcomes, got "
                f"{updated and updated['verification_state']!r}"
            )
        await approve_procedure(pool, procedure_row_id=res["id"], approved_by=_AUTHOR)
        proc_ids[key]["verified"] = True
        print(f"    -> verified + approved via {MIN_SUCCESSES_FOR_VERIFIED} real outcomes / "
              f"{MIN_DISTINCT_CONTEXTS_FOR_VERIFIED} contexts")

    impl_ids: list[str] = []
    for (name, kind, provider, description, locator, invocation, do_verify, do_activate) in _IMPLEMENTATIONS:
        impl = await implementation_registry.register(
            pool,
            name=name, kind=kind, provider=provider, created_by=_AUTHOR,
            description=description, locator=locator, invocation=invocation,
            input_schema={"type": "object"}, output_schema={"type": "object"},
            license="MIT", author=_AUTHOR,
            scope_type="project", scope_entity_id=_SCOPE_ENTITY,
            task_node_ids=[task_id],
        )
        impl_ids.append(impl["id"])
        line = f"  implementation {name:18s} {impl['id']}  kind={kind}"
        if do_activate:
            await implementation_registry.activate(pool, impl["id"])
            line += "  status=active"
        if do_verify:
            await implementation_registry.verify(pool, impl["id"])
            line += "  verification_status=verified"
        print(line)

    n_verified = sum(1 for v in proc_ids.values() if v["verified"])
    print(f"\nseeded {len(proc_ids)} procedures ({n_verified} verified+approved), "
          f"{len(impl_ids)} implementations (linked to task {task_id}), 1 task_node. "
          f"scope_entity_id={_SCOPE_ENTITY!r}")


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------
async def clear(pool) -> None:
    proc_rows = await pool.fetch(
        "SELECT id::text FROM procedures "
        "WHERE created_by = $1 AND scope_entity_id = $2 AND t_invalid IS NULL",
        _AUTHOR, _SCOPE_ENTITY,
    )
    proc_row_ids = [r["id"] for r in proc_rows]

    ev = 0
    if proc_row_ids:
        ev = await pool.fetchval(
            "WITH d AS (DELETE FROM evidence WHERE target_type = 'procedure' "
            " AND target_id::text = ANY($1::text[]) RETURNING 1) SELECT count(*) FROM d",
            proc_row_ids,
        )
    procs = await pool.execute(
        "UPDATE procedures SET t_invalid = now(), t_expired = now() "
        "WHERE created_by = $1 AND scope_entity_id = $2 AND t_invalid IS NULL",
        _AUTHOR, _SCOPE_ENTITY,
    )

    impl_rows = await pool.fetch(
        "SELECT id::text FROM implementations WHERE created_by = $1 AND scope_entity_id = $2",
        _AUTHOR, _SCOPE_ENTITY,
    )
    impl_ids = [r["id"] for r in impl_rows]
    links = 0
    if impl_ids:
        links = await pool.fetchval(
            "WITH d AS (DELETE FROM implementation_tasks WHERE implementation_id::text = ANY($1::text[]) "
            " RETURNING 1) SELECT count(*) FROM d",
            impl_ids,
        )
    impls = await pool.execute(
        "DELETE FROM implementations WHERE created_by = $1 AND scope_entity_id = $2",
        _AUTHOR, _SCOPE_ENTITY,
    )

    tn = await pool.execute(
        "UPDATE task_nodes SET t_invalid = now(), t_expired = now() "
        "WHERE skill_ref = $1 AND t_invalid IS NULL", _TASK_SKILL,
    )
    print(f"cleared: procedures {procs} ({ev} evidence rows), implementations {impls} "
          f"({links} task links), task_nodes {tn}")


async def main(*, do_clear: bool, dry_run: bool) -> None:
    if dry_run:
        for key, name, goal, steps, do_verify in _PROCEDURES:
            print(f"[dry-run] procedure {key}: {name} ({len(steps)} steps)"
                  f"{' -> verify+approve' if do_verify else ''}")
        for name, kind, *_ in _IMPLEMENTATIONS:
            print(f"[dry-run] implementation {name} (kind={kind})")
        print("[dry-run] nothing written")
        return
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await (clear(pool) if do_clear else seed(pool))
    finally:
        await pool.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clear", action="store_true", help="remove exactly the seeded demo set")
    p.add_argument("--dry-run", action="store_true", help="print what would be seeded, touch nothing")
    a = p.parse_args()
    asyncio.run(main(do_clear=a.clear, dry_run=a.dry_run))
