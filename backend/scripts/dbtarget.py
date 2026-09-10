#!/usr/bin/env python3
"""
Switch every DB-touching entry point between the LOCAL dev Postgres and the
HOSTED (Supabase) one with a single word.

Two named targets, resolved from `backend/.env` (or the ambient environment):

    local   ->  $DATABASE_URL_LOCAL
    hosted  ->  $DATABASE_URL_HOSTED  (falls back to $DATABASE_URL)

Nothing in the codebase had to change: `migrate.py`, `app.config`, and the
`*_e2e.py` suite all already read `DATABASE_URL` (and the e2e suite also
honours `TEST_DATABASE_URL` via `tests/conftest.py`). This script just sets
those variables to the target you name, then runs whatever you asked for --
or prints the shell lines so you can set them for a whole session.

USAGE

  # one-off: run a command against a target, env set only for that process
  python scripts/dbtarget.py local  -- python scripts/migrate.py --status
  python scripts/dbtarget.py local  -- python scripts/migrate.py
  python scripts/dbtarget.py local  -- python -m pytest tests -q -k e2e
  python scripts/dbtarget.py hosted -- python scripts/migrate.py --status

  # inspect what each target resolves to (secrets redacted)
  python scripts/dbtarget.py show

  # print export lines for the current shell (see also dbtarget.ps1 / dbtarget.sh,
  # which dot-source/source this for you)
  python scripts/dbtarget.py local  --print-env
  python scripts/dbtarget.py hosted --print-env --shell bash

WHAT IT SETS for the child process / shell:

  DATABASE_URL           = <resolved dsn>     # migrate.py, app.config, direct readers
  TEST_DATABASE_URL      = <resolved dsn>     # tests/conftest.py promotes this for *_e2e.py
  STEALTHLAB_DB_TARGET   = local | hosted     # advisory marker, for `show` and scripts

For `hosted` it does NOT set STEALTH_ALLOW_PROD_E2E -- `tests/conftest.py`
still refuses to run the row-writing e2e suite against a Supabase URL unless
you opt in explicitly. That guardrail is intentional; this script does not
weaken it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

TARGETS = ("local", "hosted")
_MANAGED_KEYS = ("DATABASE_URL", "TEST_DATABASE_URL", "STEALTHLAB_DB_TARGET")


def _load_env_file() -> None:
    """Best-effort load of backend/.env, same as migrate.py does. A missing
    python-dotenv or a missing file is a silent no-op -- an ambient
    DATABASE_URL_LOCAL / DATABASE_URL still works."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_ENV_FILE)


def _env_file_value(key: str) -> str | None:
    """Read one KEY=value straight out of backend/.env, ignoring the process
    environment. Needed for the 'hosted' fallback: once you have switched to
    'local', os.environ['DATABASE_URL'] holds the LOCAL dsn, so falling back
    to it would make 'hosted' resolve to 'local'. The .env file is the
    stable source of truth for the hosted URL."""
    if not _ENV_FILE.exists():
        return None
    for raw in _ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != key:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        return v or None
    return None


def resolve(target: str) -> str:
    """The DSN for a named target, or exit(2) with a precise reason.

    Resolution order (both targets): an explicit *_HOSTED / *_LOCAL process
    env var wins; otherwise the value in backend/.env. 'hosted' additionally
    accepts a plain DATABASE_URL from .env (the common single-URL setup).
    A bare DATABASE_URL from the *process* environment is deliberately NOT a
    fallback -- dbtarget itself sets that, so trusting it would make the two
    targets collapse into whichever you switched to last."""
    if target not in TARGETS:
        sys.exit(f"dbtarget: unknown target {target!r} (expected one of {TARGETS})")
    if target == "local":
        dsn = os.environ.get("DATABASE_URL_LOCAL") or _env_file_value("DATABASE_URL_LOCAL")
        if not dsn:
            sys.exit(
                "dbtarget: target 'local' needs DATABASE_URL_LOCAL set "
                f"(in {_ENV_FILE} or the environment)."
            )
        return dsn
    # hosted
    dsn = (
        os.environ.get("DATABASE_URL_HOSTED")
        or _env_file_value("DATABASE_URL_HOSTED")
        or _env_file_value("DATABASE_URL")
    )
    if not dsn:
        sys.exit(
            "dbtarget: target 'hosted' needs DATABASE_URL_HOSTED or DATABASE_URL "
            f"in {_ENV_FILE} (or DATABASE_URL_HOSTED in the environment)."
        )
    return dsn


def _redact(dsn: str) -> str:
    """postgresql://user:secret@host:port/db -> postgresql://user:***@host:port/db"""
    if "://" not in dsn or "@" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    creds, tail = rest.split("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        creds = f"{user}:***"
    return f"{scheme}://{creds}@{tail}"


def _child_env(target: str, dsn: str) -> dict:
    env = dict(os.environ)
    env["DATABASE_URL"] = dsn
    env["TEST_DATABASE_URL"] = dsn
    env["STEALTHLAB_DB_TARGET"] = target
    return env


def _print_env(target: str, dsn: str, shell: str) -> None:
    pairs = {"DATABASE_URL": dsn, "TEST_DATABASE_URL": dsn, "STEALTHLAB_DB_TARGET": target}
    if shell == "powershell":
        for k, v in pairs.items():
            print(f'$env:{k} = "{v}"')
    else:  # bash / sh
        for k, v in pairs.items():
            print(f'export {k}="{v}"')


def _cmd_show() -> int:
    _load_env_file()
    active = os.environ.get("STEALTHLAB_DB_TARGET") or "(none set; bare DATABASE_URL in use)"
    print(f"active target : {active}")
    for t in TARGETS:
        try:
            dsn = resolve(t)
            print(f"  {t:7s}    : {_redact(dsn)}")
        except SystemExit as exc:
            print(f"  {t:7s}    : -- {exc}")
    cur = os.environ.get("DATABASE_URL")
    if cur:
        print(f"DATABASE_URL  : {_redact(cur)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] == ["show"]:
        return _cmd_show()

    # Split on the FIRST '--': everything before is options for this script,
    # everything after is the command to run. (argparse.REMAINDER swallows
    # our own --print-env / --shell flags, so we do the split by hand.)
    if "--" in argv:
        cut = argv.index("--")
        opt_args, cmd = argv[:cut], argv[cut + 1:]
    else:
        opt_args, cmd = argv, []

    ap = argparse.ArgumentParser(
        prog="dbtarget.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    ap.add_argument("target", choices=TARGETS, help="which database to point at")
    ap.add_argument(
        "--print-env", action="store_true",
        help="print shell export lines instead of running a command",
    )
    ap.add_argument(
        "--shell", choices=("powershell", "bash"), default="powershell",
        help="shell dialect for --print-env (default: powershell)",
    )
    args = ap.parse_args(opt_args)

    _load_env_file()
    dsn = resolve(args.target)

    if args.print_env:
        _print_env(args.target, dsn, args.shell)
        return 0

    if not cmd:
        sys.exit(
            "dbtarget: nothing to run. Give a command after '--', or use "
            "--print-env, or 'show'.\n"
            f"  e.g. python scripts/dbtarget.py {args.target} -- python scripts/migrate.py --status"
        )

    env = _child_env(args.target, dsn)
    sys.stderr.write(
        f"[dbtarget] {args.target} -> {_redact(dsn)}\n[dbtarget] exec: {' '.join(cmd)}\n"
    )

    if os.name == "nt":
        # os.execvpe on Windows detaches the console for some child types;
        # subprocess keeps stdio wired and the exit code intact.
        import subprocess

        return subprocess.run(cmd, env=env).returncode
    os.execvpe(cmd[0], cmd, env)  # replaces this process; no return
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
