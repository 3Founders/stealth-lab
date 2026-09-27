"""Isolation + frozen-config guard for the SWE-bench experiment. Import FIRST, before any `app` module.

* DATABASE_URL -> a LOCAL database named `kel_swebench` (env KEL_SWEBENCH_DSN, default
  postgresql://postgres@127.0.0.1:5432/kel_swebench); every other database setting
  (SEARCH_DATABASE_URL, CONTROL_DATABASE_URL, K0xx shards, ...) is removed, so no code path
  can reach production.
* Loads experiment.json (the frozen settings) as CONFIG.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "backend"
CONFIG = json.loads((HERE / "experiment.json").read_text(encoding="utf-8"))
RUNS = HERE / "runs"
CACHE = HERE / "cache"          # repo clones (git) -- large, gitignored
DSN = os.environ.get("KEL_SWEBENCH_DSN", "postgresql://postgres@127.0.0.1:5432/" + CONFIG["kel_database"])


class NotIsolated(RuntimeError):
    pass


def isolate() -> None:
    parsed = urlparse(DSN)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise NotIsolated(f"experiment database must be local, got host {parsed.hostname!r}")
    if (parsed.path or "").lstrip("/") != CONFIG["kel_database"]:
        raise NotIsolated(f"experiment database must be named {CONFIG['kel_database']!r}")
    for key in list(os.environ):
        if key in ("SEARCH_DATABASE_URL", "CONTROL_DATABASE_URL", "DATABASE_URL_DIRECT", "DATABASE_URL_HOSTED",
                   "DATABASE_URL_LOCAL", "TEST_DATABASE_URL") or re.fullmatch(r"K\d{3}_DATABASE_URL", key):
            del os.environ[key]
    os.environ["DATABASE_URL"] = DSN
    os.environ.setdefault("AGENT_FAILED_REQUEST_DIR", str(RUNS / "failed_requests"))   # keep dumps out of the source tree
    for path in (BACKEND, HERE):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    RUNS.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)


def verify_after_import() -> None:
    from app.config import settings
    from app.services import shards

    if str(getattr(settings, "database_url", "") or "") != DSN:
        raise NotIsolated("app settings do not point at the experiment database")
    if shards.search_database_url() is not None:
        raise NotIsolated("SEARCH_DATABASE_URL leaked into the experiment process")


isolate()
