"""
`.stealth/artifacts/` -- Prompt 2 Sec 5's real local artifact store,
the last missing piece of the `.stealth/` ABI list (index.md, goals.md,
claims.md, procedures.md, run.md, events.jsonl, artifacts/). Completes
this session's own Goal-DAG execution durability work
(`goal_execution.py`'s `.stealth/events.jsonl` + `.stealth/goal_run.md`)
by storing the REAL output files an Implementation execution actually
produced, not just a reference to them.

Stores under `.stealth/artifacts/<goal_id>/<execution_id>/<filename>` --
addressable and inspectable directly from the local checkout (`rg`,
`ls`, a file browser), no DB round-trip needed to see what a Goal
execution actually produced.

Deliberately SEPARATE from the EXISTING Postgres `record_artifact()`
(`app/execution/recorder.py`) -- that mechanism stores a HASH REFERENCE
into a Procedure-run's event stream (`execution_run_id`-anchored,
migration-based `executions` table), never the file content itself.
This module is the local, content-storing counterpart for Goal-DAG
execution specifically, which has no `execution_run_id` at all (see
`goal_execution.py`'s own module docstring on why) -- matching that
module's `workspace_root`-based, opt-in, journal-backed durability
posture exactly, not a competing mechanism.
"""
from __future__ import annotations

import hashlib
import os

from app.stealth.atomic import atomic_write_bytes
from app.stealth.journal import STEALTH_DIRNAME


def artifacts_dir(workspace_root: str, goal_id: str, execution_id: str) -> str:
    return os.path.join(workspace_root, STEALTH_DIRNAME, "artifacts", goal_id, execution_id)


def write_execution_artifacts(
    workspace_root: str, goal_id: str, execution_id: str, output_files: dict[str, bytes],
) -> list[dict]:
    """Writes every real output file a real Implementation execution
    produced (`NodeResult.data["output_files"]`, `DeterministicProvider`'s
    own sandbox result -- never fabricated content) to
    `.stealth/artifacts/<goal_id>/<execution_id>/<filename>`.

    Returns a real manifest (`[{filename, path, sha256, size_bytes}, ...]`)
    for the caller to fold into `goal_run.md`/telemetry -- never a
    guessed path. Empty `output_files` writes nothing and returns `[]`,
    an honest common case (most Implementation kinds produce no file
    artifacts at all), not an error.

    `filename` is normalized and any attempt to escape the artifacts
    directory (a leading `/` or a `..` segment) is dropped, never
    written outside the intended tree -- the sandbox executor's own
    output-file names are trusted data from a real run, but this is a
    real filesystem write, so the same defensive posture
    `stage_input_files`/`InputPathEscape` already applies on the input
    side is applied here on the output side too.
    """
    if not output_files:
        return []
    base = artifacts_dir(workspace_root, goal_id, execution_id)
    manifest: list[dict] = []
    for filename, content in output_files.items():
        safe_name = os.path.normpath(filename).replace("\\", "/").lstrip("/")
        if safe_name == ".." or safe_name.startswith("../"):
            continue
        path = os.path.join(base, *safe_name.split("/"))
        if os.path.commonpath([os.path.abspath(base), os.path.abspath(path)]) != os.path.abspath(base):
            continue
        atomic_write_bytes(path, content)
        manifest.append({
            "filename": filename, "path": path,
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
        })
    return manifest
