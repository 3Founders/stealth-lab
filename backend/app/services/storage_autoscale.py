"""Dynamic storage allocation (storage layout v2, docs/storage_layout_v2.md): databases are added as they are needed,
not allocated up front.

Each role -- knowledge shards (K001..) and search members (S001..) -- keeps a small number of WRITABLE databases with
headroom (status 'active', weight > 0, below the capacity guard's ratio). `shard_capacity.enforce_shard_capacity`
already marks a database `full` at the ratio, which stops new placements on it (its rows stay and remain readable).
This module closes the loop when a role runs short of writable room:

  1. promote an idle registered database of that role (weight 0, active, nearly empty) -- no new project; else
  2. provision a new Neon project (scripts/provision_neon_shards.py: create, migrate, mark its role), register it
     at weight 0 -- readable everywhere but receiving nothing -- wait out the registry cache every process holds
     (shards.SHARD_CACHE_TTL_S), then give it weight. A reader never misses a row placed on a database it does not
     know yet; that is what keeps identity and dedup exact while the group grows.

The new connection string goes to backend/.neon_shards.env, which `shards.shard_dsn` reads when a variable is not in
the process environment, so running processes reach the new database without a restart.

The control database (K000) is never scaled here: when it nears its limit the capacity guard reports it.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from app.services.shards import (HOME_SHARD, ROLE_KNOWLEDGE, ROLE_SEARCH, SHARD_CACHE_TTL_S, invalidate_shard_cache,
                                 list_shards)

log = logging.getLogger(__name__)

PROMOTE_WEIGHT = 100
_PREFIX = {ROLE_KNOWLEDGE: "K", ROLE_SEARCH: "S"}


def min_writable(role: str) -> int:
    """How many writable databases with headroom a role keeps (STEALTH_MIN_WRITABLE_KNOWLEDGE / _SEARCH)."""
    default = {ROLE_KNOWLEDGE: 4, ROLE_SEARCH: 2}[role]
    try:
        return max(1, int(os.environ.get(f"STEALTH_MIN_WRITABLE_{role.upper()}", default)))
    except ValueError:
        return default


def _provisioner() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / "provision_neon_shards.py"
    spec = importlib.util.spec_from_file_location("provision_neon_shards", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _index(shard_id: str) -> Optional[int]:
    m = re.fullmatch(r"[KS](\d{3})", shard_id)
    return int(m.group(1)) if m else None


async def _set_weight(pool: Any, shard_id: str, weight: int) -> None:
    await pool.execute("UPDATE knowledge_shards SET weight = $2, updated_at = now() WHERE shard_id = $1",
                       shard_id, weight)
    invalidate_shard_cache()


async def autoscale(pool: Any, *, apply: bool = True, provision: bool = True,
                    capacity: Optional[list[dict]] = None) -> list[dict]:
    """One pass for every role. `capacity` is `enforce_shard_capacity`'s report when the caller already has it.
    Returns the actions taken (or, with apply=False, that would be taken)."""
    from app.services.shard_capacity import enforce_shard_capacity, full_ratio

    report = capacity if capacity is not None else await enforce_shard_capacity(pool, apply=apply)
    used = {r["shard_id"]: r.get("used_ratio") for r in report}
    ratio = full_ratio()
    shards = [s for s in await list_shards(pool) if s.shard_id != HOME_SHARD]
    actions: list[dict] = []
    for role in (ROLE_KNOWLEDGE, ROLE_SEARCH):
        mine = [s for s in shards if s.role == role]
        if not mine:
            continue                    # the deployment has not opted into this role (one database): never grow it
        roomy = [s for s in mine if s.status == "active" and s.weight > 0 and (used.get(s.shard_id) or 0) < ratio]
        need = min_writable(role) - len(roomy)
        if need <= 0:
            continue
        # an idle database takes no placements, so it has not grown since it was last open (the guard does not
        # measure it -- that would keep its compute awake); one never measured is a freshly provisioned one
        idle = sorted((s for s in mine if s.status == "active" and s.weight == 0
                       and (used.get(s.shard_id) is None or used[s.shard_id] < ratio / 2)),
                      key=lambda s: s.shard_id)
        for s in idle[:need]:
            actions.append({"role": role, "action": "promote", "shard_id": s.shard_id})
            if apply:
                await _set_weight(pool, s.shard_id, PROMOTE_WEIGHT)
        need -= min(need, len(idle))
        if need <= 0:
            continue
        if not provision or not os.environ.get("NEON_API_KEY"):
            actions.append({"role": role, "action": "short_of_room", "missing": need,
                            "why": "provisioning disabled" if not provision else "NEON_API_KEY not set"})
            log.warning("storage role %s is short of %d writable database(s) and cannot provision", role, need)
            continue
        next_index = max([i for i in (_index(s.shard_id) for s in mine) if i] or [0]) + 1
        for index in range(next_index, next_index + need):
            actions.append({"role": role, "action": "provision", "shard_id": f"{_PREFIX[role]}{index:03d}"})
            if apply:
                await _provision_one(pool, role, index)
    return actions


async def _provision_one(pool: Any, role: str, index: int) -> None:
    """Create + migrate + mark + register at weight 0, wait out every process's registry cache, then weight it."""
    prov = _provisioner()
    prov.KIND = role
    sid = prov.shard_id(index)
    # the provisioner registers on DATABASE_URL: this process's own control database
    argv = ["--kind", role, "--start", str(index), "--count", "1", "--weight", "0",
            "--pg-version", os.environ.get("STEALTH_NEON_PG_VERSION", "17")]
    if os.environ.get("STEALTH_NEON_REGION"):
        argv += ["--region", os.environ["STEALTH_NEON_REGION"]]
    rc = int(await asyncio.to_thread(prov.main, argv))
    if rc != 0:
        raise RuntimeError(f"provisioning {sid} failed (exit {rc}); re-run resumes it")
    invalidate_shard_cache()
    await asyncio.sleep(SHARD_CACHE_TTL_S + 5)      # every process now reads the new database before anything lands
    await _set_weight(pool, sid, PROMOTE_WEIGHT)
    log.warning("storage: provisioned %s (%s) and opened it for new placements", sid, role)
