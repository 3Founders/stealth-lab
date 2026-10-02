from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ._bootstrap import BackendRootNotFound, get_backend_root, load_mcp_server_module

_DEFAULT_PORT = 8765


SHARDS_ENV_NAME = ".neon_shards.env"


def load_backend_dotenv(backend_root: Path) -> bool:
    """backend/.env, then backend/.neon_shards.env: with storage layout v2 the knowledge shards (K###_DATABASE_URL)
    and search members (S###_DATABASE_URL) live in the second file, which provisioning writes. Neither overrides a
    variable already set in the environment."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    dotenv_path = backend_root / ".env"
    loaded = dotenv_path.is_file()
    if loaded:
        load_dotenv(dotenv_path)
    load_shards_env(backend_root)
    return loaded


def load_shards_env(backend_root: Path) -> int:
    """Load the shard connection strings and point app.services.shards at the same file, so a shard provisioned
    while the server runs is still reachable (shard_dsn re-reads the file when it changes). Returns how many
    connection strings it holds; never prints them."""
    path = backend_root / SHARDS_ENV_NAME
    if not path.is_file():
        return 0
    from dotenv import dotenv_values, load_dotenv

    os.environ.setdefault("STEALTH_SHARDS_ENV_FILE", str(path))
    load_dotenv(path)
    return sum(1 for name, value in dotenv_values(path).items() if name.endswith("_DATABASE_URL") and value)


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
        description="Launch the StealthLab MCP server (9 tools over the bi-temporal "
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
    if not (backend_root / SHARDS_ENV_NAME).is_file():
        print(f"stealthlab-mcp-server: no {SHARDS_ENV_NAME} in {backend_root} -- shard and search-member "
              "databases must then come from the environment", file=sys.stderr)

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
              f"(pip install -e {backend_root})",
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
