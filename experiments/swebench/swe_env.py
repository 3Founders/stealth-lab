"""Isolation + frozen-config guard for the SWE-bench experiment. Import FIRST, before any `app` module.

* DATABASE_URL -> a LOCAL database named `kel_swebench` (env KEL_SWEBENCH_DSN, default
  postgresql://postgres@127.0.0.1:5432/kel_swebench); every other database setting
  (SEARCH_DATABASE_URL, CONTROL_DATABASE_URL, K0xx shards, ...) is removed, so no code path
  can reach production.
* Loads experiment.json (the frozen settings) as CONFIG, and sets Kel's knowledge flags from
  `kel_settings` for every step (learning and answering must use the same settings).
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
# KEL_SWEBENCH_CONFIG selects another experiment's frozen settings (e.g. ../swebench_rebench/experiment.json);
# its runs/ and cache/ live next to that file, so two experiments never share state. Default: this folder.
CONFIG_PATH = Path(os.environ.get("KEL_SWEBENCH_CONFIG") or HERE / "experiment.json").resolve()
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _load_experiment_env() -> None:
    """KEL_SWEBENCH_DSN and the agent endpoint may live in backend/.env (gitignored) instead of the shell.
    Only these three keys are read, and a value already in the environment wins."""
    path = BACKEND / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in ("KEL_SWEBENCH_DSN", "EXPERIMENT_BASE_URL", "EXPERIMENT_API_KEY"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_experiment_env()
if sys.platform == "win32" and str(HERE / "winshim") not in sys.path:
    sys.path.append(str(HERE / "winshim"))     # POSIX `resource` stand-in so swebench 4.x imports (see winshim/)
RUNS = CONFIG_PATH.parent / "runs"
CACHE = CONFIG_PATH.parent / "cache"          # repo clones (git) -- large, gitignored
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
    os.environ["KNOWLEDGE_VERIFIED_EXAMPLES"] = "true" if CONFIG["kel_settings"]["verified_examples"] else "false"
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
    if bool(settings.knowledge_verified_examples) != bool(CONFIG["kel_settings"]["verified_examples"]):
        raise NotIsolated("KNOWLEDGE_VERIFIED_EXAMPLES does not match experiment.json kel_settings")
    if shards.search_database_url() is not None:
        raise NotIsolated("SEARCH_DATABASE_URL leaked into the experiment process")


isolate()
