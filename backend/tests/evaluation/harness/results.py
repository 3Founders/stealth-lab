"""Machine-readable result persistence for the gold-set evaluation harness.

One JSONL row per case, matching the field list in evaluation/METRICS.md and
spec section 2. A gold-set test (backend/tests/evaluation/<area>/test_gold_*)
builds one EvalResult per case via gold_runner.run_gold_set() and asserts on
the aggregate; the JSONL rows are the artifact evaluation-results/v1-baseline
is built from.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EvalResult:
    task_id: str
    scenario_id: str
    run_id: str
    baseline_or_treatment: str  # "baseline" | "treatment" | "n/a" for pure component evals
    success: bool
    failure_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    latency_ms: float = 0.0
    cost: float = 0.0
    retries: int = 0
    files_touched: list[str] = field(default_factory=list)
    verification_result: dict[str, Any] | None = None
    # Additive: candidate-specific metrics (recall_at_5, precondition_precision,
    # etc.) that don't fit the generic schema above.
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, sort_keys=True)


def write_jsonl(results: list[EvalResult], path: Path, mode: str = "w") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as f:
        for r in results:
            f.write(r.to_json() + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _git_commit(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True,
            text=True, check=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return None


def build_manifest(
    *,
    repo_root: Path,
    corpus_state: str,
    config: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Manifest recorded alongside every evaluation-results run (spec §33)."""
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(repo_root),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "corpus_state": corpus_state,
        "config": config,
    }
    if extra:
        manifest.update(extra)
    return manifest


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
