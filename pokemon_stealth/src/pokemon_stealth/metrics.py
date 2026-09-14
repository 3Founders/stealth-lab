"""
Per-run metrics (STEP 19) + comparison summaries (STEP 20). Writes to
results/runs.csv and results/runs.jsonl -- append-only, one row per run.

No fabricated numbers: any field this harness couldn't actually measure
stays None/blank rather than a guessed value (STEP 29).
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Optional

FIELDNAMES = [
    "run_id", "condition", "model", "mission", "seed", "success", "failure_reason",
    "wall_time_s", "emulator_frames", "macro_actions", "llm_calls", "llm_parse_failures",
    "input_tokens", "output_tokens", "estimated_cost_usd", "battles_entered", "battles_lost",
    "party_wipes", "claims_retrieved", "procedures_retrieved", "failures_retrieved",
    "knowledge_items_created", "knowledge_items_reused", "rom_sha256", "pyboy_version",
    "prompt_version", "checkpoint", "timestamp",
]


@dataclass
class RunMetrics:
    run_id: str
    condition: str
    model: str
    mission: str
    seed: Optional[int]
    success: Optional[bool] = None
    failure_reason: Optional[str] = None
    wall_time_s: Optional[float] = None
    emulator_frames: int = 0
    macro_actions: int = 0
    llm_calls: int = 0
    llm_parse_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    battles_entered: int = 0
    battles_lost: int = 0
    party_wipes: int = 0
    claims_retrieved: int = 0
    procedures_retrieved: int = 0
    failures_retrieved: int = 0
    knowledge_items_created: int = 0
    knowledge_items_reused: int = 0
    rom_sha256: str = ""
    pyboy_version: str = ""
    prompt_version: str = "v1"
    checkpoint: str = ""
    timestamp: str = ""

    def to_row(self) -> dict:
        d = asdict(self)
        return {k: ("" if v is None else v) for k, v in d.items()}


def append_run(results_dir: Path, metrics: RunMetrics) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path, jsonl_path = results_dir / "runs.csv", results_dir / "runs.jsonl"
    row = metrics.to_row()

    write_header = not csv_path.is_file()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(metrics)) + "\n")


def load_runs(results_dir: Path) -> list[dict]:
    path = results_dir / "runs.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(runs: list[dict]) -> dict:
    """STEP 20: fresh vs notes vs stealth comparison, grouped by condition."""
    by_condition: dict[str, list[dict]] = {}
    for r in runs:
        by_condition.setdefault(r["condition"], []).append(r)

    summary = {}
    for condition, rows in by_condition.items():
        n = len(rows)
        successes = [r for r in rows if r.get("success")]
        total_tokens = [r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in rows]
        costs = [r.get("estimated_cost_usd", 0.0) for r in rows]
        summary[condition] = {
            "n_runs": n,
            "success_rate": len(successes) / n if n else None,
            "mean_llm_calls": mean(r.get("llm_calls", 0) for r in rows) if n else None,
            "mean_tokens": mean(total_tokens) if n else None,
            "mean_cost_usd": mean(costs) if n else None,
            "mean_actions": mean(r.get("macro_actions", 0) for r in rows) if n else None,
            "mean_wall_time_s": mean(r.get("wall_time_s") or 0 for r in rows) if n else None,
            "failure_count": n - len(successes),
            "cost_per_success": (sum(costs) / len(successes)) if successes else None,
            "success_per_1k_tokens": (
                (len(successes) / (sum(total_tokens) / 1000)) if sum(total_tokens) else None
            ),
        }
    return summary


def learning_curve(runs: list[dict], mission: str, condition: str = "stealth") -> list[dict]:
    """STEP 21: sequential runs for one mission/condition, in the order they
    actually happened (relies on real timestamps, not a fabricated ordering)."""
    rows = [r for r in runs if r["mission"] == mission and r["condition"] == condition]
    rows.sort(key=lambda r: r.get("timestamp", ""))
    return [
        {
            "run_index": i + 1,
            "success": r.get("success"),
            "llm_calls": r.get("llm_calls"),
            "tokens": (r.get("input_tokens", 0) + r.get("output_tokens", 0)),
            "macro_actions": r.get("macro_actions"),
            "estimated_cost_usd": r.get("estimated_cost_usd"),
        }
        for i, r in enumerate(rows)
    ]
