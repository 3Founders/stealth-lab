"""Local smoke test for the Railway production startup path.

Validates, without any real production credentials, that:
  - declared dependencies install and `app.main` imports cleanly
  - the app fails FAST with a clear message when required production
    config is missing (DATABASE_URL; ENVIRONMENT=production + a
    localhost/wildcard FRONTEND_ORIGIN)
  - the app boots, binds 0.0.0.0:$PORT, and /health responds when config
    is complete (using the repo's own docker-compose Postgres as a stand-in
    for a real production database -- not a mock, but not a secret either)
  - nothing printed during any of the above contains a configured secret

Run from backend/:
    python scripts/railway_smoke_test.py

Requires `docker compose` for the full-boot checks; the import and
missing-config checks run without it. Exits nonzero on any failure.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

FAKE_SECRET = "sk-smoke-test-should-never-appear-in-any-output"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(label: str, cmd: list[str], env: dict, expect_ok: bool, must_not_contain: list[str] | None = None,
          must_contain: list[str] | None = None, timeout: int = 30) -> None:
    print(f"\n=== {label} ===")
    proc = subprocess.run(
        cmd, cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )
    combined = proc.stdout + proc.stderr
    ok = (proc.returncode == 0) == expect_ok
    for secret in must_not_contain or []:
        if secret and secret in combined:
            print(combined)
            print(f"FAIL: secret/placeholder value leaked into output for '{label}'")
            sys.exit(1)
    for needle in must_contain or []:
        if needle not in combined:
            print(combined)
            print(f"FAIL: expected output to contain {needle!r} for '{label}'")
            sys.exit(1)
    if not ok:
        print(combined)
        print(f"FAIL: '{label}' exit code {proc.returncode} (expected success={expect_ok})")
        sys.exit(1)
    print(f"OK ({'succeeded' if proc.returncode == 0 else 'failed as expected'})")


def base_env(**overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("STEALTHLAB_")}
    # Strip anything a developer's real backend/.env might already export
    # into this shell, so the test exercises exactly the env we construct.
    for key in (
        "DATABASE_URL", "FRONTEND_ORIGIN", "ENVIRONMENT",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FIREWORKS_API_KEY", "GOOGLE_API_KEY",
        "VOYAGE_API_KEY", "GEMINI_API_KEY",
    ):
        env.pop(key, None)
    env.update(overrides)
    return env


def check_import() -> None:
    _run(
        "1. dependencies + import",
        [sys.executable, "-c", "import app.main"],
        env=base_env(DATABASE_URL="postgresql://placeholder/placeholder"),
        expect_ok=True,
        timeout=120,  # cold bytecode compile across ~150 modules can be slow on first run
    )


def check_missing_database_url() -> None:
    # _env_file=None disables loading a developer's real backend/.env, so
    # this exercises the same "nothing set" posture a fresh Railway
    # container has (no .env file there at all), not this machine's local
    # dev config.
    _run(
        "2. missing DATABASE_URL fails clearly",
        [
            sys.executable, "-c",
            "from app.config import Settings; Settings(_env_file=None).require('database_url')",
        ],
        env=base_env(),
        expect_ok=False,
        must_contain=["Missing required setting", "database_url"],
    )


def check_production_cors_guard() -> None:
    _run(
        "3. ENVIRONMENT=production with default FRONTEND_ORIGIN fails clearly",
        [sys.executable, "-c", "from app.config import Settings; Settings(_env_file=None, environment='production').assert_production_config()"],
        env=base_env(),
        expect_ok=False,
        must_contain=["ENVIRONMENT=production"],
    )
    _run(
        "4. ENVIRONMENT=production with wildcard FRONTEND_ORIGIN fails clearly",
        [sys.executable, "-c", "from app.config import Settings; Settings(_env_file=None, environment='production', frontend_origin='*').assert_production_config()"],
        env=base_env(),
        expect_ok=False,
        must_contain=["wildcard" if False else "not safe in production"],
    )
    _run(
        "5. ENVIRONMENT=production with a real origin passes",
        [sys.executable, "-c", "from app.config import Settings; Settings(_env_file=None, environment='production', frontend_origin='https://example.com').assert_production_config()"],
        env=base_env(),
        expect_ok=True,
    )


def check_full_boot() -> None:
    if shutil.which("docker") is None:
        print("\n=== 6. full boot + /health (skipped: docker not found) ===")
        return
    print("\n=== 6. full boot + /health (docker compose db) ===")
    subprocess.run(["docker", "compose", "up", "-d", "db"], cwd=REPO_ROOT, check=True)
    dsn = "postgresql://stealthlab:stealthlab@127.0.0.1:5433/stealthlab"
    for _ in range(30):
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "stealthlab", "-d", "stealthlab"],
            cwd=REPO_ROOT, capture_output=True,
        )
        if r.returncode == 0:
            break
        time.sleep(1)
    else:
        print("FAIL: local Postgres never became ready")
        sys.exit(1)

    env = base_env(DATABASE_URL=dsn)
    migrate = subprocess.run([sys.executable, "scripts/migrate.py"], cwd=BACKEND_ROOT, env=env, capture_output=True, text=True)
    if migrate.returncode != 0:
        print(migrate.stdout + migrate.stderr)
        print("FAIL: migrate.py did not exit 0 against the local db")
        sys.exit(1)

    port = _free_port()
    env = base_env(
        DATABASE_URL=dsn,
        FRONTEND_ORIGIN="https://example.com",
        ANTHROPIC_API_KEY=FAKE_SECRET,
        PORT=str(port),
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=BACKEND_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        body = None
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                    body = resp.read().decode()
                    break
            except Exception:
                time.sleep(1)
        if body is None:
            server.terminate()
            out = server.stdout.read() if server.stdout else ""
            print(out)
            print("FAIL: /health never responded")
            sys.exit(1)
        if '"status":"ok"' not in body.replace(" ", ""):
            print(f"FAIL: unexpected /health body: {body!r}")
            sys.exit(1)
        print(f"OK: 0.0.0.0:{port} bound, GET /health -> {body.strip()}")
    finally:
        server.terminate()
        try:
            out, _ = server.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            out, _ = server.communicate()
        if FAKE_SECRET in (out or ""):
            print("FAIL: a configured secret value appeared in server output")
            sys.exit(1)


def main() -> None:
    check_import()
    check_missing_database_url()
    check_production_cors_guard()
    check_full_boot()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
