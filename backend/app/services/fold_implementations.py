"""One-time fold of the retired Implementation object into procedures.

Migration 98 snapshots every legacy `implementations` row (+ its procedure links) into
`legacy_implementation_fold` and drops the tables. This converts each snapshot the way the product decision says:

  * linked to a procedure  -> the mechanism becomes a step `binding` on that procedure's live version
                              (only the steps it supported, else every unbound step), via `supersede_procedure`
  * not linked to anything -> a ONE-STEP procedure: the step carries the binding and its own source locator

Everything goes through `capture_procedure` / `supersede_procedure` (embedding, goal identity, dedup), never raw
INSERTs. Re-runnable: rows already stamped `folded_procedure_id` are skipped. Nothing is folded into an executable
state: a legacy `deterministic` script whose bytes were never preserved becomes a `command` binding on a CANDIDATE
procedure like any other ingested method, and stays unverified until evidence says otherwise.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from app.services.source_locators import binding_from_implementation


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _locator_for(impl: dict) -> Optional[dict]:
    loc = _j(impl.get("locator")) or {}
    uri = loc.get("url") or loc.get("uri") or impl.get("source_ref")
    path = loc.get("path")
    out = {k: v for k, v in {
        "source_id": f"legacy-implementation:{impl.get('name')}", "uri": uri, "path": path,
        "commit": loc.get("commit"), "content_hash": impl.get("content_hash"), "granularity": "document",
    }.items() if v}
    return out if (out.get("uri") or out.get("source_id")) else None


async def fold_one(pool: asyncpg.Pool, row: asyncpg.Record, *, embedder: Any = None, judge: Any = None) -> dict:
    from app.services.procedures import capture_procedure, supersede_procedure

    impl = _j(row["implementation"])
    links = _j(row["links"]) or []
    binding = binding_from_implementation({**impl, "locator": _j(impl.get("locator")) or {},
                                           "requirements": _j(impl.get("requirements")) or {},
                                           "verification_contract": _j(impl.get("verification_contract")) or {}})
    locator = _locator_for(impl)
    goal = impl.get("goal") or impl.get("name")

    # 1. linked: put the binding on the procedure's live version
    for link in links:
        pid = link.get("procedure_id")
        live = await pool.fetchrow(
            "SELECT id, steps FROM procedures WHERE procedure_id = $1::uuid AND t_invalid IS NULL ORDER BY version DESC LIMIT 1", str(pid))
        if live is None:
            continue
        steps = _j(live["steps"]) or []
        supported = set(_j(link.get("supported_steps")) or [])
        new_steps, changed = [], False
        for i, st in enumerate(steps):
            if isinstance(st, dict) and not st.get("binding") and (not supported or st.get("order", i) in supported):
                st = {**st, "binding": binding}
                changed = True
            new_steps.append(st)
        if changed:
            v = await supersede_procedure(pool, prior_row_id=str(live["id"]), changed_fields={"steps": new_steps},
                                          superseded_by="fold_implementations",
                                          reason=f"folded legacy implementation {impl.get('name')!r} into step bindings")
            return {"mode": "bound_to_steps", "procedure_id": str((v or {}).get("procedure_id") or pid)}

    # 2. unlinked (or its procedure is gone): a one-step procedure
    step = {"order": 0, "description": impl.get("description") or f"Run {impl.get('name')}", "goal": goal,
            "expected_outcome": impl.get("expected_outcome"), "binding": binding}
    if locator:
        step["source_locator"] = locator
    res = await capture_procedure(
        pool, name=str(impl.get("name")), goal=str(goal), steps=[step], provenance="prior_library",
        scope_type=impl.get("scope_type") or "global", scope_entity_id=impl.get("scope_entity_id"),
        visibility=impl.get("visibility") or "public", owner_id=impl.get("owner_id"),
        created_by="fold_implementations", goal_embedder=embedder, goal_judge=judge, procedure_dedup=True,
        source_key=f"legacy-implementation:{row['implementation_id']}", source_locator=locator,
        require_source_locators=bool(locator),
    )
    return {"mode": "one_step_procedure", "procedure_id": str(res["procedure_id"])}


async def fold_all(pool: asyncpg.Pool, *, embedder: Any = None, judge: Any = None, limit: Optional[int] = None) -> dict:
    rows = await pool.fetch(
        "SELECT * FROM legacy_implementation_fold WHERE folded_procedure_id IS NULL ORDER BY archived_at LIMIT $1", limit or 100000)
    done, failed = [], []
    for r in rows:
        try:
            out = await fold_one(pool, r, embedder=embedder, judge=judge)
            await pool.execute(
                "UPDATE legacy_implementation_fold SET folded_procedure_id = $2::uuid, folded_at = now(), fold_note = $3 "
                "WHERE implementation_id = $1::uuid", str(r["implementation_id"]), out["procedure_id"], out["mode"])
            done.append({"implementation_id": str(r["implementation_id"]), **out})
        except Exception as exc:  # noqa: BLE001 -- one bad row must not stop the rest; it stays unfolded and is reported
            failed.append({"implementation_id": str(r["implementation_id"]), "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"folded": len(done), "failed": len(failed), "results": done, "errors": failed}
