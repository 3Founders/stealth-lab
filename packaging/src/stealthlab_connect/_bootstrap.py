from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_VAR = "STEALTHLAB_BACKEND_ROOT"
_SERVER_MARKER = Path("app") / "mcp_server" / "server.py"
_COLLECTOR_MARKER = Path("app") / "services" / "trace_collector.py"


class BackendRootNotFound(RuntimeError):
    pass


def _is_backend_root(candidate: Path) -> bool:
    return (candidate / _SERVER_MARKER).is_file() and (candidate / _COLLECTOR_MARKER).is_file()


def candidate_roots(explicit: str | os.PathLike | None = None) -> list[Path]:
    if explicit:
        return [Path(explicit).resolve()]
    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        return [Path(env_value).resolve()]
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    for base in here.parents:
        candidates.append(base / "backend")
    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents):
        candidates.append(base / "backend")
        candidates.append(base)
    unique: list[Path] = []
    seen: set[Path] = set()
    for c in candidates:
        key = c.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def find_backend_root(explicit: str | os.PathLike | None = None) -> Path:
    searched: list[Path] = []
    for candidate in candidate_roots(explicit):
        if _is_backend_root(candidate):
            return candidate
        searched.append(candidate)
    hint = ""
    if explicit:
        hint = f"The path passed explicitly ({explicit}) is not a StealthLab backend checkout."
    elif os.environ.get(_ENV_VAR):
        hint = f"$STEALTHLAB_BACKEND_ROOT={os.environ[_ENV_VAR]} is not a StealthLab backend checkout."
    raise BackendRootNotFound(
        f"could not locate the StealthLab backend (looked for {_SERVER_MARKER} and "
        f"{_COLLECTOR_MARKER} under each candidate). Set STEALTHLAB_BACKEND_ROOT to "
        f"the backend/ checkout, or pass --backend-root. {hint} "
        f"Candidates tried: {[str(p) for p in searched]}"
    )


def ensure_backend_importable(backend_root: Path) -> Path:
    root = backend_root.resolve()
    if not _is_backend_root(root):
        raise BackendRootNotFound(
            f"{root} is not a StealthLab backend checkout (missing "
            f"{_SERVER_MARKER} or {_COLLECTOR_MARKER})"
        )
    existing = sys.modules.get("app")
    if existing is not None:
        loaded_from = getattr(existing, "__path__", [None])[0]
        if loaded_from is not None and Path(loaded_from).resolve() != root / "app":
            raise RuntimeError(
                f"an 'app' package from {loaded_from} is already imported; it is not "
                f"this backend ({root}). Import stealthlab_connect before importing any "
                f"other 'app' module, or run from a clean interpreter."
            )
    root_str = str(root)
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)
    return root


def get_backend_root(explicit: str | os.PathLike | None = None) -> Path:
    return ensure_backend_importable(find_backend_root(explicit))


def load_trace_collector_module():
    get_backend_root()
    import app.services.trace_collector as module

    return module


def load_mcp_server_module():
    root = get_backend_root()
    if not (root.parent / "experiments" / "swebench_pro" / "agent.py").is_file():
        raise BackendRootNotFound(
            f"find_best_way needs {root.parent / 'experiments' / 'swebench_pro' / 'agent.py'} "
            f"(experiments/swebench_pro must be a sibling of backend/) -- see "
            f"backend/README_MCP_SERVER.md setup step 3"
        )
    import app.mcp_server.server as module

    return module
