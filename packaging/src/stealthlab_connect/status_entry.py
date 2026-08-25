from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._bootstrap import BackendRootNotFound, get_backend_root

_DEFAULT_PORT = 8766


def load_backend_dotenv(backend_root: Path) -> bool:
    dotenv_path = backend_root / ".env"
    if not dotenv_path.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(dotenv_path)
    return True


def preflight() -> list[str]:
    """DATABASE_URL is the only hard requirement: the status page opens a
    real pool at startup. No token preflight -- this surface adds no auth
    of its own (it reuses authn.py exactly as app/main.py does)."""
    problems: list[str] = []
    try:
        from app.config import settings

        settings.require("database_url")
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
        problems.append(f"DATABASE_URL is not usable: {exc}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stealthlab-status-page",
        description="Serve the StealthLab minimal status surface: one read-only "
        "page listing episodes -> claims -> procedures with capability scores and "
        "evidence trails. Loopback by default; no write endpoints exist.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1 -- keep it loopback)")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT,
                        help=f"port (default {_DEFAULT_PORT}; 8000 is app/main.py's API, "
                             "8765 is stealthlab-mcp-server)")
    parser.add_argument("--backend-root", default=None,
                        help="path to the StealthLab backend checkout "
                             "(default: $STEALTHLAB_BACKEND_ROOT or auto-discovery)")
    args = parser.parse_args(argv)

    try:
        backend_root = get_backend_root(args.backend_root)
    except BackendRootNotFound as exc:
        print(f"stealthlab-status-page: {exc}", file=sys.stderr)
        return 1

    load_backend_dotenv(backend_root)

    problems = preflight()
    for problem in problems:
        print(f"stealthlab-status-page: {problem}", file=sys.stderr)
    if problems:
        return 2

    try:
        from stealthlab_connect.status_server import create_status_app
    except Exception as exc:  # noqa: BLE001
        print(f"stealthlab-status-page: importing status server failed: {exc!r}\n"
              f"  check that backend dependencies are installed (pip install -e {backend_root})",
              file=sys.stderr)
        return 1

    print(
        f"stealthlab-status-page: serving http://{args.host}:{args.port}/\n"
        f"  read-only: episodes, claims, procedures, capability scores, evidence trails\n"
        f"  deep links point at the main API (override with ${'STEALTHLAB_API_BASE'})",
        file=sys.stderr,
    )

    import uvicorn

    uvicorn.run(create_status_app(), host=args.host, port=args.port, log_level="info")
    return 0
