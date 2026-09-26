"""Isolation guard for the local BigCodeBench demo. Import this FIRST, before any `app` module.

Everything the demo writes goes to ONE local Postgres database. This module:
  * points DATABASE_URL at the local demo database (environment beats backend/.env);
  * removes every other database setting from the process -- SEARCH_DATABASE_URL,
    CONTROL_DATABASE_URL, DATABASE_URL_DIRECT / _HOSTED, and every K0xx shard URL -- so
    no code path can reach project B or a shard;
  * refuses to continue unless the target host is localhost and the name is the demo DB;
  * after `app.config` loads, asserts the settings still point at the demo database.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

DEMO_DSN = os.environ.get("KEL_DEMO_DSN", "postgresql://postgres@127.0.0.1:55432/kel_bcb_demo")
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RUNS = HERE / "runs"

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NotIsolated(RuntimeError):
    pass


def _check_dsn(dsn: str) -> None:
    parsed = urlparse(dsn)
    if parsed.hostname not in _LOCAL_HOSTS:
        raise NotIsolated(f"demo database must be local, got host {parsed.hostname!r}")
    if not (parsed.path or "").lstrip("/").startswith("kel_bcb_demo"):
        raise NotIsolated("demo database name must start with 'kel_bcb_demo'")


def isolate() -> None:
    _check_dsn(DEMO_DSN)
    for key in list(os.environ):
        if key in ("SEARCH_DATABASE_URL", "CONTROL_DATABASE_URL", "DATABASE_URL_DIRECT", "DATABASE_URL_HOSTED",
                   "DATABASE_URL_LOCAL", "TEST_DATABASE_URL") or re.fullmatch(r"K\d{3}_DATABASE_URL", key):
            del os.environ[key]
    os.environ["DATABASE_URL"] = DEMO_DSN
    for path in (BACKEND, HERE):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    os.chdir(BACKEND)                      # app.config reads backend/.env for provider keys only


def verify_after_import() -> None:
    from app.config import settings
    from app.services import shards

    configured = str(getattr(settings, "database_url", "") or "")
    if configured != DEMO_DSN:
        raise NotIsolated("app settings do not point at the demo database")
    if shards.search_database_url() is not None:
        raise NotIsolated("SEARCH_DATABASE_URL leaked into the demo process")


isolate()
