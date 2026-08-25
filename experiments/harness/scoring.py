"""
Episode classification for the §40 harness.

Consumes episode records (schema in scripted_arms.py) plus procedure ground
truth, produces per-episode metric rows. Pure functions, no I/O — directly
unit-testable, same discipline as run_graph_experiment.node_metrics().

Semantics pinned by spec v4:
  - task success ............ episode.resolved (§40 L1390)
  - procedure reuse ......... reused_procedure_ids non-empty (§40 L1392)
  - false reuse ............. failed AND the reuse attempt itself caused the
                              failure (§36 cause list; L1259: "`false_reuse`
                              marks a reuse attempt itself causing the
                              failure"). A success despite a rocky reuse is
                              NOT false reuse.
  - stale refusal ........... refused a procedure whose assumptions no longer
                              hold (§40 strongest result, L1406), graded
                              against fixture ground truth the agents never
                              saw (§39 invariant 11: the system can refuse).
  - transfer ................ success on an unseen task WITH reuse (§40 L1394)
  - cost .................... tokens x price table (§40 L1396/L1398)
"""
from __future__ import annotations

import json
from pathlib import Path

# Placeholder frontier pricing until a real adapter lands ($/Mtok). Synthetic
# rows still need a computable cost so the scoreboard pipeline is exercised;
# real runs must pass their own model prices.
DEFAULT_PRICE_PER_MTOK = {"input": 2.50, "output": 10.00}


def load_procedures(fixtures_dir: Path | str) -> dict[str, dict]:
    procs = json.loads(
        (Path(fixtures_dir) / "procedures.json").read_text(encoding="utf-8"))
    return {p["procedure_id"]: p for p in procs["procedures"]}


def cost_usd(episode: dict, price_per_mtok: dict | None = None) -> float:
    price = price_per_mtok or DEFAULT_PRICE_PER_MTOK
    return round(
        episode.get("tokens_in", 0) / 1e6 * price["input"]
        + episode.get("tokens_out", 0) / 1e6 * price["output"], 6)


def classify(episode: dict, procedures_by_id: dict[str, dict]) -> dict:
    """Episode record -> flat metric row. Never raises on missing fields so
    legacy/partial JSONL rows stay summarizable (reference node_metrics rule).
    """
    resolved = bool(episode.get("resolved"))
    reused = list(episode.get("reused_procedure_ids") or [])
    followed_memory = list(episode.get("followed_memory_ids") or [])
    refused = list(episode.get("refused_procedure_ids") or [])

    any_reuse_attempt = bool(reused or followed_memory)
    false_reuse = (
        not resolved
        and bool(episode.get("reuse_caused_failure"))
        and any_reuse_attempt)

    # Ground truth the surface never showed the agent (mcp_surface._public).
    def _is_stale(pid: str) -> bool:
        p = procedures_by_id.get(pid)
        return bool(p and p.get("stale"))

    correct_stale_refusals = [pid for pid in refused if _is_stale(pid)]
    stale_used = [pid for pid in reused if _is_stale(pid)]
    stale_offer_opportunity = [pid for pid in (episode.get("stale_offered") or [])
                               if _is_stale(pid)]

    unseen = bool(episode.get("unseen_task"))
    return {
        "task_id": episode.get("task_id"),
        "arm": episode.get("arm"),
        "valid": bool(episode.get("valid", True)),
        "pass": resolved,
        "unseen_task": unseen,
        "reused": any_reuse_attempt,
        "n_reused_procedures": len(reused),
        "false_reuse": false_reuse,
        "stale_refusal_correct": bool(correct_stale_refusals),
        "stale_refusal_missed": bool(stale_used),
        "stale_offer_opportunity": bool(stale_offer_opportunity),
        "transfer_success": resolved and unseen and any_reuse_attempt,
        "tokens_in": int(episode.get("tokens_in") or 0),
        "tokens_out": int(episode.get("tokens_out") or 0),
        "tool_calls": int(episode.get("tool_calls") or 0),
        "latency_seconds": float(episode.get("latency_seconds") or 0.0),
        "human_interventions": int(episode.get("human_interventions") or 0),
        "cost_usd": cost_usd(episode),
    }
