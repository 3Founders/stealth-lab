"""
Offline DEMO baseline adapter for the error-floor instrument.

Mirrors the PUBLISHED rules of backend/app/services/observations.py's
deterministic_v1 extractor -- including its known quirks -- WITHOUT
importing backend (harness lane rule). Purpose:

1. Lets `run_error_floor.py` produce a real, non-trivial precision/recall
   reading fully offline ("instrument ready and tested offline first").
2. Serves as the rule-based BASELINE ticket 04 explicitly wanted measured
   before any model-based extractor is judged.

THIS IS A MIRROR, NOT THE SOURCE OF TRUTH. Production runs pass the real
extractor by dotted path:

    --adapter app.services.observations:extract_deterministic_observations

The five deliberate divergences from the hand-gold fixtures (compound git
commit, flagged git commit, pip install pytest-cov, NotebookEdit, and the
semantic_label layer it simply does not attempt) are pinned by test in
tests/test_error_floor_end_to_end.py -- drift there is loud on purpose.
"""
from __future__ import annotations

import json

DEMO_EXTRACTOR_NAME = "deterministic_v1_demo(mirror of observations.py@lane/measure)"

_TEST_COMMAND_MARKERS = (
    "pytest", "npm test", "npm run test", "go test", "cargo test", "jest"
)


def _looks_like_test_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _TEST_COMMAND_MARKERS)


def deterministic_v1_demo(trace_event: dict) -> list[dict]:
    """trace_event -> observations, per deterministic_v1's published rules."""
    observations: list[dict] = []
    tool_name = trace_event.get("tool_name")
    tool_input = trace_event.get("tool_input") or {}
    if isinstance(tool_input, str):
        tool_input = json.loads(tool_input)

    if tool_name in ("Edit", "Write", "MultiEdit") and tool_input.get("file_path"):
        observations.append({
            "observation_type": "file_touched",
            "label": f"Modified {tool_input['file_path']}",
            "properties": {"file_path": tool_input["file_path"], "tool_name": tool_name},
        })

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if not command:
            pass
        elif command.strip().startswith("git commit"):
            observations.append({
                "observation_type": "commit_made",
                "label": f"Committed: {command.strip()}",
                "properties": {"command": command},
            })
        elif _looks_like_test_command(command):
            observations.append({
                "observation_type": "test_run",
                "label": f"Ran tests: {command.strip()}",
                "properties": {"command": command},
            })
        else:
            observations.append({
                "observation_type": "command_executed",
                "label": f"Executed: {command.strip()}",
                "properties": {"command": command},
            })

    return observations
