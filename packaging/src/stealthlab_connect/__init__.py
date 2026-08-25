from __future__ import annotations

from pathlib import Path

from ._bootstrap import (
    BackendRootNotFound,
    candidate_roots,
    ensure_backend_importable,
    find_backend_root,
    get_backend_root,
    load_mcp_server_module,
    load_trace_collector_module,
)
from .collector_entry import build_event, collect_payload

__version__ = "0.1.0"

__all__ = [
    "BackendRootNotFound",
    "append_trace_event",
    "build_event",
    "candidate_roots",
    "collect_payload",
    "collect_stdin_payload",
    "ensure_backend_importable",
    "find_backend_root",
    "get_backend_root",
    "load_mcp_server_module",
    "load_trace_collector_module",
    "trace_drop_count",
]


def append_trace_event(event: dict, file_path: str | Path, session_id: str,
                       event_type: str, sequence: int | None = None,
                       max_lines: int | None = None) -> dict:
    collector = load_trace_collector_module()
    kwargs = {} if max_lines is None else {"max_lines": max_lines}
    return collector.append_event(
        event, Path(file_path), session_id=session_id, event_type=event_type,
        sequence=sequence, **kwargs,
    )


def trace_drop_count(file_path: str | Path) -> int:
    return load_trace_collector_module().read_drop_count(Path(file_path))


def collect_stdin_payload(payload: dict, trace_dir=None, file_path=None) -> dict | None:
    return collect_payload(payload, trace_dir=trace_dir, file_path=file_path)
