"""
Arm definitions and the synthetic scripted agents (skeleton phase).

Arms per spec v4 §40 (L1382-1386):
  A. frontier agent from scratch
  B. frontier agent + conventional memory
  C. frontier agent + verified procedural experience (via the MCP surface)

AgentAdapter.run() is the ONLY contract the runner and scoreboard depend on;
real frontier-agent adapters replace ScriptedAgent without any other change.
Tonight's scripted agents exist so the full pipeline — episode records,
scoring, paired statistics, power footer — is exercisable offline with known
ground truth. Their outcome rates are fixtures, NOT findings; every synthetic
output says so.

Episode record schema (what scoring.py consumes):
  task_id, arm, valid, invalid_reason, resolved,
  reused_procedure_ids      — procedures actually executed (arm C; §40 "procedure reuse")
  followed_memory_ids       — conventional-memory items acted on (arm B analog)
  refused_procedure_ids     — offered procedures the agent declined to use
  reuse_caused_failure      — attribution: the reuse attempt itself caused the
                              failure (§36 cause list; spec v4 L1259)
  stale_offered             — ids of lookalike procedures whose assumptions no
                              longer hold were surfaced to this agent
  tokens_in, tokens_out     — cost inputs (§40 "tokens"/"cost")
  tool_calls, latency_seconds, human_interventions   — §40 telemetry
  unseen_task               — copied from the fixture for the unseen slice
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import mcp_surface

ARMS = ("A", "B", "C")

ARM_LABELS = {
    "A": "frontier_solo",
    "B": "frontier_plus_conventional_memory",
    "C": "frontier_plus_verified_procedures",
}


class AgentAdapter(Protocol):
    def run(self, task: dict) -> dict:
        """Execute one task; return an episode record (schema above)."""
        ...


def _base_episode(task: dict, arm: str) -> dict:
    return {
        "task_id": task["task_id"],
        "arm": arm,
        "valid": True,
        "invalid_reason": None,
        "resolved": False,
        "reused_procedure_ids": [],
        "followed_memory_ids": [],
        "refused_procedure_ids": [],
        "reuse_caused_failure": False,
        "stale_offered": [p for p in [task.get("stale_offer")] if p],
        "tokens_in": 0,
        "tokens_out": 0,
        "tool_calls": 0,
        "latency_seconds": 0.0,
        "human_interventions": 0,
        "unseen_task": bool(task.get("unseen")),
    }


class SoloFrontierAgent:
    """Arm A: from scratch. Baseline token/latency profile."""

    def __init__(self, price_hint_tokens: tuple[int, int] = (48000, 6000)):
        self.base_in, self.base_out = price_hint_tokens

    def run(self, task: dict) -> dict:
        ep = _base_episode(task, "A")
        # Arm A has no retrieval surface of any kind: no procedures are
        # surfaced, so there are no refusal opportunities to score (§40's
        # stale-procedure-detection metric only applies where offers exist).
        ep["stale_offered"] = []
        ep["resolved"] = task["solo_outcome"] == "pass"
        ep["tokens_in"], ep["tokens_out"] = self.base_in, self.base_out
        ep["tool_calls"] = 22
        ep["latency_seconds"] = round((self.base_in + self.base_out) / 900, 1)
        return ep


class ConventionalMemoryAgent(SoloFrontierAgent):
    """Arm B: same frontier agent plus a plain RAG store.

    No verification, no applicability gate — a helpful blob rescues a failing
    solo run; a misleading blob causes a failure attributed to the memory it
    followed (the B-side false-reuse analogue: §36's `false_reuse` marks a
    reuse attempt itself causing the failure).
    """

    def __init__(self, fixtures_dir: Path | str):
        super().__init__()
        blobs = json.loads(
            (Path(fixtures_dir) / "rag_corpus.json").read_text(encoding="utf-8"))
        self._blob_by_domain = {}
        for b in blobs["blobs"]:
            self._blob_by_domain.setdefault(b["domain"], b["blob_id"])

    def run(self, task: dict) -> dict:
        ep = super().run(task)
        ep["arm"] = "B"
        rag = task.get("rag", "none")
        if rag == "none":
            return ep
        blob = f"{self._blob_by_domain.get(task['domain'], 'rag-missing')}"
        ep["followed_memory_ids"].append(blob)
        ep["tool_calls"] += 3
        ep["tokens_in"] += 8000  # retrieved context resent on every call
        if rag == "helpful":
            ep["resolved"] = True
            ep["latency_seconds"] += 4.0
        else:  # misleading
            ep["resolved"] = False
            ep["reuse_caused_failure"] = True
        return ep


class VerifiedProcedureAgent(SoloFrontierAgent):
    """Arm C: frontier agent plus the substrate via the MCP surface.

    Policy: consult surface.search -> check_applicability on each candidate.
    A passing applicable procedure is reused cheaply; a failing gate means the
    procedure is REFUSED (recorded — §39 invariant 11) and the agent falls
    back to solo behavior. The poisoned fixture case (bypasses_gate) makes the
    gate fail open so the false-reuse scorer is demonstrable on arm C itself.
    """

    def __init__(self, fixtures_dir: Path | str):
        super().__init__()
        self.surface: mcp_surface.McpSurface = mcp_surface.StubSurface(fixtures_dir)

    def run(self, task: dict) -> dict:
        ep = super().run(task)
        ep["arm"] = "C"
        domain = task["domain"]
        applicable_id = task.get("applicable_procedure")
        stale_offer = task.get("stale_offer")
        bypass = bool(task.get("substrate_bypasses_gate"))

        candidates = [c for c in self.surface.search(domain)]
        ep["tool_calls"] += len(candidates) + 1

        # Refuse the stale offer when its gate fails (or accept it under the
        # bypass). Ground truth grading happens in scoring.py, not here.
        used_stale = None
        ctx = {"bypasses_gate": True} if bypass else {}
        if stale_offer:
            if self.surface.check_applicability(stale_offer, ctx):
                used_stale = stale_offer
            else:
                ep["refused_procedure_ids"].append(stale_offer)
                self.surface.record_refusal(stale_offer, "assumptions no longer hold")

        if used_stale:
            # Reuse attempt itself caused the failure (spec v4 L1259).
            ep["resolved"] = False
            ep["reused_procedure_ids"].append(used_stale)
            ep["reuse_caused_failure"] = True
            ep["tokens_in"] = 18000
            ep["tokens_out"] = 3000
        elif applicable_id:
            ctx = {}
            if self.surface.check_applicability(applicable_id, ctx):
                ep["resolved"] = True
                ep["reused_procedure_ids"].append(applicable_id)
                ep["tokens_in"], ep["tokens_out"] = 18000, 3000
                ep["tool_calls"] += 6
                ep["latency_seconds"] = round(ep["latency_seconds"] * 0.35, 1)
            else:
                ep["refused_procedure_ids"].append(applicable_id)
        # else: no applicable procedure -> honest solo fallback (unseen tasks
        # land here; §40's transfer/unseen slices read that difference).

        ep["latency_seconds"] += round((ep["tokens_in"] + ep["tokens_out"]) / 20000, 1)
        return ep


def build_agents(fixtures_dir: Path | str) -> dict[str, AgentAdapter]:
    return {
        "A": SoloFrontierAgent(),
        "B": ConventionalMemoryAgent(fixtures_dir),
        "C": VerifiedProcedureAgent(fixtures_dir),
    }
