"""Isolation guard for the local DS-1000 experiment. Import this FIRST, before any `app` module.

Same guard as experiments/bigcodebench/demo_env.py, pointed at its OWN fresh database
(`kel_ds1000_demo`), so nothing learned in the BigCodeBench demo can leak into this one:
  * DATABASE_URL -> the local experiment database (environment beats backend/.env);
  * SEARCH_DATABASE_URL, CONTROL_DATABASE_URL, DATABASE_URL_* and every K0xx shard URL
    are removed from the process, so no code path can reach project B or a shard;
  * refuses to continue unless the host is local and the name is the experiment DB;
  * after `app.config` loads, asserts the settings still point at it.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

DEMO_DSN = os.environ.get("KEL_DS1000_DSN", "postgresql://postgres@127.0.0.1:55432/kel_ds1000_demo")
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RUNS = HERE / os.environ.get("KEL_DS1000_RUNS", "runs")   # round 2 (confirmation) uses runs2
ROUND1_RUNS = HERE / "runs"
EVAL_PYTHON = HERE / ".evalenv" / "Scripts" / "python.exe"     # pinned DS-1000 libraries, no app code

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NotIsolated(RuntimeError):
    pass


def _check_dsn(dsn: str) -> None:
    parsed = urlparse(dsn)
    if parsed.hostname not in _LOCAL_HOSTS:
        raise NotIsolated(f"experiment database must be local, got host {parsed.hostname!r}")
    if (parsed.path or "").lstrip("/") not in ("kel_ds1000_demo", "kel_ds1000_pl", "kel_ds1000_r3"):
        raise NotIsolated("experiment database must be 'kel_ds1000_demo' or 'kel_ds1000_pl' (production-like)")


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
    RUNS.mkdir(parents=True, exist_ok=True)


def verify_after_import() -> None:
    from app.config import settings
    from app.services import shards

    if str(getattr(settings, "database_url", "") or "") != DEMO_DSN:
        raise NotIsolated("app settings do not point at the experiment database")
    if shards.search_database_url() is not None:
        raise NotIsolated("SEARCH_DATABASE_URL leaked into the experiment process")


isolate()
