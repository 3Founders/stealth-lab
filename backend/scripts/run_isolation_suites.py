"""Run the database-backed isolation suites against a THROWAWAY loopback database (securityp1.md §5.2).

    python scripts/run_isolation_suites.py --dsn postgresql://ROLE@127.0.0.1:PORT/scratch_db [--label owner]

Every database variable the code could read (DATABASE_URL, TEST_DATABASE_URL, SEARCH_DATABASE_URL and every
*_DATABASE_URL named in backend/.env and backend/.neon_shards.env) is set to --dsn first, so no test can reach a real
database through a shard variable or the .env fallback. A non-loopback --dsn is refused. Prints one line per file:
passed / failed / skipped / errors (a skip is NOT a pass), and writes the same as JSON when --json is given.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]
SUITES = [
    "tests/test_mcp_tenant_isolation_e2e.py",
    "tests/test_cross_user_isolation_e2e.py",
    "tests/test_retrieval_fixture_isolation_e2e.py",
    "tests/test_agents_file_download_isolation_e2e.py",
    "tests/test_ingestion_episode_extraction_privacy_e2e.py",
    "tests/test_product_model_privacy_e2e.py",
    "tests/test_hardening_h2_rls_backstop.py",
    "tests/test_access.py",
    "tests/test_routing_isolation_e2e.py",
    "tests/test_org_governance_e2e.py",
    "tests/test_tenant_isolation_p1a_e2e.py",
    "tests/evaluation/privacy",
]


def db_var_names() -> set[str]:
    names = {"DATABASE_URL", "TEST_DATABASE_URL", "SEARCH_DATABASE_URL", "CONTROL_DATABASE_URL", "DATABASE_URL_DIRECT",
             "DATABASE_URL_LOCAL"}
    for f in (BACKEND / ".env", BACKEND / ".neon_shards.env"):
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*([A-Z0-9_]*DATABASE_URL[A-Z0-9_]*)\s*=", line)
                if m:
                    names.add(m.group(1))
    return names


def counts(output: str) -> dict[str, int]:
    out = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    tail = [ln for ln in output.splitlines() if re.search(r"\d+ (passed|failed|skipped|errors?)", ln)]
    for m in re.finditer(r"(\d+) (passed|failed|skipped|errors?)", tail[-1] if tail else ""):
        key = "errors" if m.group(2).startswith("error") else m.group(2)
        out[key] = int(m.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--json")
    ap.add_argument("--only", nargs="*", help="run only these suite paths")
    a = ap.parse_args()
    if (urlparse(a.dsn).hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        print("refusing: --dsn is not a loopback database")
        return 2
    # the server token comes from backend/.env as in a real start (a different value in the environment is refused)
    env = {**os.environ, "STEALTHLAB_ENV": "TEST", "PYTHONUTF8": "1"}
    for name in db_var_names():
        env[name] = a.dsn
    results = {}
    for suite in a.only or SUITES:
        if not (BACKEND / suite).exists():
            results[suite] = {"missing": True}
            print(f"{suite}: (missing)")
            continue
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE", suite], cwd=BACKEND,
                           env=env, capture_output=True, text=True)
        c = counts(p.stdout + p.stderr)
        failures = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.startswith(("FAILED", "ERROR"))][:20]
        results[suite] = {**c, "failures": failures}
        print(f"{suite}: {c['passed']} passed, {c['failed']} failed, {c['skipped']} skipped, {c['errors']} errors")
        for ln in failures:
            print(f"   {ln[:200]}")
    if a.json:
        Path(a.json).write_text(json.dumps({"label": a.label, "role": urlparse(a.dsn).username, "results": results},
                                           indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
