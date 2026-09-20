"""Neon-backed provisioning, snapshots, capacity rollover and repair wrappers.

Every database-changing step goes through an EXISTING primitive:
  migrations       scripts/migrate.py --dsn (env DATABASE_URL)          registration   admin register-shard (schema-verifying)
  weights/status   admin shard-weight / shard-status                    verification   admin verify-refs / metrics
This module only sequences them and talks to the Neon API. Nothing here deletes or moves canonical data.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from typing import Any, Optional

from . import core
from .core import OK, WARN, FAIL, OpsError, admin, load_env, load_state, migrate, update_state, write_secret_env
from .neon import Neon

REQUIRED_EXTENSIONS = ("vector", "pgcrypto", "btree_gist")


def shard_ids(count: int) -> list[str]:
    return [f"K{n:03d}" for n in range(1, count + 1)]


def env_var(shard_id: str) -> str:
    return "CONTROL_DATABASE_URL" if shard_id == "K000" else f"{shard_id}_DATABASE_URL"


# ------------------------------------------------------------------ direct DB probes (read-only, tiny)


def _probe(dsn: str, sql: str) -> Any:
    import asyncpg

    async def go() -> Any:
        conn = await asyncpg.connect(dsn, timeout=30)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()
    return asyncio.run(go())


def check_extensions(dsn: str) -> list[str]:
    have = {r[0] for r in _probe(dsn, "SELECT extname FROM pg_extension")}
    return [e for e in REQUIRED_EXTENSIONS if e not in have]


def has_data_ledger(dsn: str) -> bool:
    return bool(_probe(dsn, "SELECT to_regclass('public.schema_migrations') IS NOT NULL AS x")[0]["x"])


def pending_migrations(dsn: str) -> list[str]:
    p = migrate(dsn, "--status")
    if not p.ok:
        raise OpsError(f"migrate --status failed: {(p.err or p.out)[-400:]}")
    return [ln.split()[1] for ln in p.out.splitlines() if ln.startswith("pending")]


# ------------------------------------------------------------------ DSN + registry


def resolve_dsn(neon: Neon, shard_id: str) -> tuple[str, dict]:
    """(dsn, project). The DSN comes from the private secrets file / environment when present, else from Neon,
    and is then saved to the private secrets file (never the repo)."""
    project, created = neon.ensure_project(shard_id)
    name = env_var(shard_id)
    dsn = load_env().get(name)
    if not dsn:
        dsn = neon.connection_uri(project["id"])
        write_secret_env(name, dsn)
        if shard_id == "K000":
            write_secret_env("DATABASE_URL", dsn)   # legacy name migrate.py-era scripts still read
    st = load_state()
    st.setdefault("projects", {})[shard_id] = {"project_id": project["id"], "name": project.get("name"), "env": name}
    core.save_state(st)
    project["_created"] = created
    return dsn, project


def registry() -> list[dict]:
    p = admin("shards", "--json")
    if not p.ok:
        raise OpsError(f"cannot read the shard registry (is the control DB migrated?): {(p.err or p.out)[-300:]}")
    return p.json()


def take_snapshot(neon: Neon, shard_id: str, name: str) -> str:
    proj = load_state().get("projects", {}).get(shard_id, {}).get("project_id")
    if not proj:
        p = neon.find_project(neon.project_name(shard_id))
        if not p:
            return f"{shard_id}: no Neon project (skipped)"
        proj = p["id"]
    branch, created = neon.snapshot(proj, name)
    st = load_state()
    st.setdefault("snapshots", {}).setdefault(name, {})[shard_id] = branch.get("id")
    core.save_state(st)
    return f"{shard_id}: branch {name!r} {'created' if created else 'already exists'}"


def snapshot_prod(name: str, *, shards: Optional[list[str]] = None) -> list[str]:
    neon = Neon()
    ids = shards or ["K000"] + [s["shard_id"] for s in _registry_safe() if s["shard_id"] != "K000"]
    return [take_snapshot(neon, sid, name) for sid in ids]


def _registry_safe() -> list[dict]:
    try:
        return registry()
    except OpsError:
        return []


# ------------------------------------------------------------------ provisioning


def provision(shard_id: str, *, weight: int = 100, log=print) -> dict[str, Any]:
    """create project -> DSN -> (snapshot if data) -> migrate -> extensions -> register -> weight/status -> verify."""
    neon = Neon()
    dsn, project = resolve_dsn(neon, shard_id)
    log(f"{shard_id}: Neon project {project['name']} ({'created' if project['_created'] else 'exists'})")
    if project["_created"] and shard_id != "K000":
        pass   # brand-new database: nothing to snapshot
    elif has_data_ledger(dsn):
        pend = pending_migrations(dsn)
        if pend:
            log(f"{shard_id}: {len(pend)} pending migration(s) on an existing database -> snapshot first")
            log("  " + take_snapshot(neon, shard_id, f"pre-migration-{dt.date.today():%Y%m%d}"))
    p = migrate(dsn)
    if not p.ok:
        raise OpsError(f"{shard_id}: migration failed (nothing was registered): {core.redact((p.err or p.out)[-500:])}")
    missing = check_extensions(dsn)
    if missing:
        raise OpsError(f"{shard_id}: required extensions missing: {missing}. Run migrations as the project owner role.")
    log(f"{shard_id}: migrated; extensions {', '.join(REQUIRED_EXTENSIONS)} present")
    result: dict[str, Any] = {"shard_id": shard_id, "project_id": project["id"], "registered": False}
    if shard_id != "K000":
        existing = next((s for s in registry() if s["shard_id"] == shard_id), None)
        if existing:
            log(f"{shard_id}: already registered (status={existing['status']}, weight={existing['weight']}) -- left unchanged")
        else:
            r = admin("register-shard", shard_id, "--dsn-env", env_var(shard_id), "--weight", str(weight))
            if not r.ok:
                raise OpsError(f"{shard_id}: register-shard refused: {(r.err or r.out)[-400:]}")
            log(f"{shard_id}: registered (active, weight {weight}); connectivity + schema verified by register-shard")
            result["registered"] = True
    refs = admin("verify-refs")
    if not refs.ok:
        raise OpsError(f"{shard_id}: verify-refs failed: {(refs.out or refs.err)[-400:]}")
    log(f"{shard_id}: verify-refs OK")
    return result


def provision_shards(count: int, *, weight: int = 100, log=print) -> list[dict]:
    ids = shard_ids(count)
    existing = {s["shard_id"] for s in _registry_safe()}
    todo = [s for s in ids if s not in existing]
    log(f"target shards: {', '.join(ids)}; already registered: {sorted(existing & set(ids)) or 'none'}; to create: {todo or 'none'}")
    # registered shards are still re-verified (cheap, idempotent) but never re-created
    return [provision(sid, weight=weight, log=log) for sid in ids]


def verify_shards(log=print) -> tuple[bool, list[str]]:
    problems: list[str] = []
    reg = registry()
    m = admin("metrics")
    probes = {s["shard_id"]: s for s in (m.json()["shards"] if m.ok else [])}
    if not m.ok:
        problems.append(f"metrics failed: {(m.err or m.out)[-200:]}")
    env = load_env()
    for s in reg:
        sid = s["shard_id"]
        pr = probes.get(sid, {})
        if s["status"] == "retired":
            continue
        if not pr.get("reachable"):
            problems.append(f"{sid}: unreachable ({pr.get('error', 'no probe')})")
            continue
        log(f"{sid}: {s['status']:<9} weight={s['weight']:<4} rtt={pr['rtt_ms']}ms size={pr['size_bytes'] / 1e6:.0f}MB conns={pr['connections']}/{pr['max_connections']}")
        dsn = env.get(s["dsn_env"] or "CONTROL_DATABASE_URL")
        if dsn:
            pend = pending_migrations(dsn)
            if pend:
                problems.append(f"{sid}: {len(pend)} pending migration(s), e.g. {pend[0]}")
            miss = check_extensions(dsn)
            if miss:
                problems.append(f"{sid}: missing extensions {miss}")
        elif sid != "K000":
            problems.append(f"{sid}: env var {s['dsn_env']} not set here (cannot verify schema)")
    refs = admin("verify-refs")
    if not refs.ok:
        problems.append("verify-refs failed")
    return not problems, problems


# ------------------------------------------------------------------ capacity rollover


def capacity_plan(shards: list[dict], reg: list[dict], *, warn_bytes: float, rollover_bytes: float) -> dict[str, Any]:
    """Pure policy. Only ACTIVE shards roll over; K000 (control) never becomes 'full' -- its PUBLIC weight drops to 0
    instead (private/org rows always live on K000). Objects never move."""
    if not rollover_bytes:
        return {"configured": False, "actions": []}
    actions, warn = [], []
    by_id = {s["shard_id"]: s for s in reg}
    roomy_remote = 0     # active remote shards that can still take new public data
    for s in shards:
        st = by_id.get(s["shard_id"], {}).get("status")
        if not s.get("reachable") or st != "active":
            continue
        if s["size_bytes"] >= rollover_bytes:
            actions.append({"shard_id": s["shard_id"], "do": "set-weight-0" if s["shard_id"] == "K000" else "mark-full",
                            "size_bytes": s["size_bytes"]})
        else:
            if s["shard_id"] != "K000":
                roomy_remote += 1
            if warn_bytes and s["size_bytes"] >= warn_bytes:
                warn.append(s["shard_id"])
    nxt = max((int(re.sub(r"\D", "", i) or 0) for i in by_id if i != "K000"), default=0) + 1
    need_new = bool(actions) and roomy_remote == 0
    if need_new:
        actions.append({"shard_id": f"K{nxt:03d}", "do": "provision-and-activate"})
    return {"configured": True, "warn": warn, "actions": actions}


def capacity(*, apply: bool, warn_bytes: float, rollover_bytes: float, log=print) -> dict[str, Any]:
    m = admin("metrics")
    if not m.ok:
        raise OpsError(f"metrics failed: {(m.err or m.out)[-300:]}")
    plan = capacity_plan(m.json()["shards"], registry(), warn_bytes=warn_bytes, rollover_bytes=rollover_bytes)
    if not plan["configured"]:
        log("capacity thresholds not configured: set OPS_SHARD_WARN_BYTES / OPS_SHARD_ROLLOVER_BYTES (bytes) for your Neon plan")
        return plan
    for w in plan["warn"]:
        log(f"WARN {w} above warning threshold")
    for a in plan["actions"]:
        log(f"{'APPLY' if apply else 'PLAN '} {a['do']} {a['shard_id']}")
        if not apply:
            continue
        if a["do"] == "mark-full":
            r = admin("shard-status", a["shard_id"], "full")
            if not r.ok:
                raise OpsError(f"shard-status failed: {r.err[-300:]}")
        elif a["do"] == "set-weight-0":
            r = admin("shard-weight", "K000", "0")
            if not r.ok:
                raise OpsError(f"shard-weight failed: {r.err[-300:]}")
        else:
            provision(a["shard_id"], log=log)   # migrate -> verify -> register ACTIVE -> new placements use it
    return plan


# ------------------------------------------------------------------ repair / disaster-recovery wrappers


def disable_shard(shard_id: str, *, replace: bool = False, log=print) -> None:
    if shard_id == "K000":
        raise OpsError("K000 is the control database; it cannot be disabled (use shard-weight to stop public placement)")
    m = admin("metrics")
    probe = next((s for s in (m.json()["shards"] if m.ok else []) if s["shard_id"] == shard_id), {})
    status = "readonly" if probe.get("reachable") else "unhealthy"
    r = admin("shard-status", shard_id, status)
    if not r.ok:
        raise OpsError(f"could not mark {shard_id} {status}: {r.err[-300:]}")
    log(f"{shard_id} -> {status}: no new placements; routing rows untouched; retrieval reports it as "
        f"{'read-only' if status == 'readonly' else 'unavailable (degraded=true)'}")
    for name in ("verify-refs", "verify-projections"):
        p = admin(name)
        log(f"{name}: {'OK' if p.ok else 'FAILED (see admin ' + name + ')'}")
    if replace:
        n = max(int(re.sub(r"\D", "", s["shard_id"]) or 0) for s in registry())
        provision(f"K{n + 1:03d}", log=log)


def repair_projections(*, rebuild: bool, log=print) -> bool:
    log("drain-projections: " + admin("drain-projections").out.strip()[:200])
    if admin("verify-projections").ok:
        log("projections verified")
        return True
    if not rebuild:
        log("projections still differ; re-run with --rebuild (snapshots first, then admin reindex all)")
        return False
    try:
        for line in snapshot_prod(f"pre-reindex-{dt.datetime.now():%Y%m%d-%H%M}"):
            log("snapshot " + line)
    except OpsError as exc:
        raise OpsError(f"refusing to rebuild without a snapshot: {exc}")
    admin("reindex", "all", timeout=3600)
    admin("drain-projections")
    ok = admin("verify-projections").ok
    log("projections " + ("verified after reindex" if ok else "STILL differ; see projection_outbox (status='failed', last_error)"))
    return ok


def repair_shard(shard_id: str, *, reactivate: bool, log=print) -> bool:
    reg = {s["shard_id"]: s for s in registry()}
    if shard_id not in reg:
        raise OpsError(f"unknown shard {shard_id}")
    m = admin("metrics")
    probe = next((s for s in (m.json()["shards"] if m.ok else []) if s["shard_id"] == shard_id), {})
    ok = bool(probe.get("reachable"))
    log(f"{shard_id}: {'reachable' if ok else 'UNREACHABLE ' + probe.get('error', '')}")
    refs = admin("verify-refs")
    ok = ok and refs.ok
    log(f"verify-refs: {'OK' if refs.ok else 'FAILED'}")
    if ok and reactivate and reg[shard_id]["status"] != "active":
        admin("shard-status", shard_id, "active")
        log(f"{shard_id} re-activated")
    elif ok and reg[shard_id]["status"] != "active":
        log(f"{shard_id} looks healthy but is {reg[shard_id]['status']}; add --reactivate to accept new placements again")
    return ok
