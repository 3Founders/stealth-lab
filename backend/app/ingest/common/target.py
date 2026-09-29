"""Which database a run writes to, and proof that it can write nowhere else.

Two targets, no others:

  local       -- a Postgres on this machine. The DSN comes from the environment variable named by `--dsn-env`
                 (never typed on the command line, never printed). Every other database address the process
                 could reach -- DATABASE_URL, DATABASE_URL_DIRECT, CONTROL_DATABASE_URL, SEARCH_DATABASE_URL and
                 every registered shard's DSN variable -- is re-bound to that same local DSN or must itself be
                 local; one hosted address anywhere and the run refuses. `backend/.env` points DATABASE_URL at
                 production, so without this a "local" run could write production through any core function that
                 reads the settings.

  production  -- the database in backend/.env. Refused unless `--approved-by` names the person who approved this
                 run; the name is stored on the run record.

Why so strict: the 2026-09-29 production SkillMD run happened because the old pipeline had no such boundary.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Every environment variable through which core code can reach a database.
DSN_VARIABLES = ("DATABASE_URL", "DATABASE_URL_DIRECT", "CONTROL_DATABASE_URL", "SEARCH_DATABASE_URL")


class TargetRefused(RuntimeError):
    """The run may not start against this target. The message says why."""


@dataclass(frozen=True)
class Target:
    name: str                     # "local" | "production"
    dsn: str                      # never printed
    approved_by: Optional[str]    # production only

    def describe(self) -> dict:
        host = urlparse(self.dsn).hostname or "?"
        db = (urlparse(self.dsn).path or "/").lstrip("/") or "?"
        return {"target": self.name, "host": host if self.name == "local" else "hosted", "database": db,
                "approved_by": self.approved_by}


def is_loopback(dsn: Optional[str]) -> bool:
    if not dsn:
        return False
    return (urlparse(dsn).hostname or "").lower() in LOOPBACK_HOSTS


def resolve_target(name: str, *, dsn_env: Optional[str] = None, approved_by: Optional[str] = None,
                   env=os.environ) -> Target:
    """Pick the DSN for `name`. Pure apart from reading `env` and the settings."""
    if name == "local":
        if not dsn_env:
            raise TargetRefused("--target local needs --dsn-env NAME (an environment variable holding a local DSN)")
        if "://" in dsn_env:
            raise TargetRefused("--dsn-env takes the NAME of an environment variable, never a connection string")
        dsn = env.get(dsn_env)
        if not dsn:
            raise TargetRefused(f"environment variable {dsn_env} is not set")
        if not is_loopback(dsn):
            raise TargetRefused(f"{dsn_env} does not point at this machine; --target local only writes a local database")
        return Target("local", dsn, None)
    if name == "production":
        if not (approved_by or "").strip():
            raise TargetRefused("--target production needs --approved-by NAME: the person who approved this run")
        from app.config import settings

        dsn = env.get("DATABASE_URL_DIRECT") or getattr(settings, "database_url_direct", None) or settings.database_url
        if not dsn:
            raise TargetRefused("no production DATABASE_URL is configured")
        if is_loopback(dsn):
            raise TargetRefused("--target production resolved to a local database; use --target local")
        return Target("production", dsn, approved_by.strip())
    raise TargetRefused(f"unknown target {name!r}: use local or production")


def bind_local(target: Target, env=os.environ) -> None:
    """Point every database address this process can reach at the local target, then prove it.

    Settings are loaded once from backend/.env at import time, so the environment alone is not enough: the
    settings object's own fields are re-bound too.
    """
    if target.name != "local":
        return
    from app.config import settings

    for var in DSN_VARIABLES:
        if var == "SEARCH_DATABASE_URL":
            env.pop(var, None)            # project B collapses onto the control database
        else:
            env[var] = target.dsn
    settings.database_url = target.dsn
    if hasattr(settings, "database_url_direct"):
        settings.database_url_direct = target.dsn
    settings.search_database_url = None
    assert_no_hosted_address(env)


def assert_no_hosted_address(env=os.environ) -> None:
    """Every configured database address must be local (or unset). Used after bind_local."""
    from app.config import settings

    offenders = [var for var in DSN_VARIABLES if env.get(var) and not is_loopback(env.get(var))]
    for field in ("database_url", "database_url_direct", "search_database_url"):
        value = getattr(settings, field, None)
        if value and not is_loopback(value):
            offenders.append(f"settings.{field}")
    if offenders:
        raise TargetRefused("a local run can still reach a hosted database through: " + ", ".join(sorted(offenders)))


async def assert_shards_local(pool: Any, env=os.environ) -> None:
    """Registered shards are reached through their DSN variable; on a local run each must be local too."""
    rows = await pool.fetch("SELECT shard_id, dsn_env FROM knowledge_shards WHERE dsn_env IS NOT NULL")
    offenders = [r["shard_id"] for r in rows if not is_loopback(env.get(r["dsn_env"]))]
    if offenders:
        raise TargetRefused("registered shards without a local DSN on a local run: " + ", ".join(offenders))
