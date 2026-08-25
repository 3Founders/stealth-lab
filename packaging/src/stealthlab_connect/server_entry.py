from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ._bootstrap import BackendRootNotFound, get_backend_root, load_mcp_server_module

_DEFAULT_PORT = 8765


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


def preflight_http() -> list[str]:
    problems: list[str] = []
    if not os.environ.get("STEALTHLAB_MCP_TOKEN"):
        problems.append(
            "STEALTHLAB_MCP_TOKEN is not set -- generate one with "
            '`python -c "import secrets; print(secrets.token_urlsafe(32))"` and add '
            "it to backend/.env (the server itself refuses to start without it)"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stealthlab-mcp-server",
        description="Launch the StealthLab MCP server (8 tools over the bi-temporal "
        "knowledge/task graph) for an external agent. Default: Streamable HTTP on "
        "loopback, bearer-token gated. Use --stdio for a stdio-transport client.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address for HTTP mode (default 127.0.0.1 -- keep it loopback)")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT,
                        help=f"HTTP port (default {_DEFAULT_PORT}; 8000 is app/main.py's FastAPI app)")
    parser.add_argument("--stdio", action="store_true",
                        help="serve over stdio instead of HTTP (no token needed; stdio bypasses auth by protocol design)")
    parser.add_argument("--backend-root", default=None,
                        help="path to the StealthLab backend checkout (default: $STEALTHLAB_BACKEND_ROOT or auto-discovery)")
    args = parser.parse_args(argv)

    try:
        backend_root = get_backend_root(args.backend_root)
    except BackendRootNotFound as exc:
        print(f"stealthlab-mcp-server: {exc}", file=sys.stderr)
        return 1

    load_backend_dotenv(backend_root)

    if not args.stdio:
        problems = preflight_http()
        for problem in problems:
            print(f"stealthlab-mcp-server: {problem}", file=sys.stderr)
        if problems:
            return 2

    try:
        module = load_mcp_server_module()
    except Exception as exc:
        print(f"stealthlab-mcp-server: importing app.mcp_server.server failed: {exc!r}\n"
              f"  check that backend dependencies are installed "
              f"(pip install -e {backend_root}) and experiments/swebench_pro exists",
              file=sys.stderr)
        return 1

    if args.stdio:
        module.server.run()
        return 0

    print(
        f"stealthlab-mcp-server: serving on http://{args.host}:{args.port}/mcp\n"
        f"  connect Claude Code:\n"
        f'    claude mcp add --transport http stealthlab http://127.0.0.1:{args.port}/mcp \\\n'
        f'      --header "Authorization: Bearer $STEALTHLAB_MCP_TOKEN" --scope local\n'
        f"  smoke test (expect 401): curl -s -o /dev/null -w '%{{http_code}}\\n' "
        f"-X POST http://127.0.0.1:{args.port}/mcp",
        file=sys.stderr,
    )

    import uvicorn

    uvicorn.run(module.app, host=args.host, port=args.port, log_level="info")
    return 0
