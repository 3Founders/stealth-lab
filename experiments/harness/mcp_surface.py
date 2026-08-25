"""
Arm C's substrate surface, shaped like the MCP server's tool calls.

Lane rule: nothing under experiments/harness may import backend/** at write
time (board: Lane MEASURE owns experiments/harness only), so this module
defines the SURFACE — method names and payload shapes mirroring the MCP tool
calls an external agent would make — with an offline fixture-backed stub.
When CORE-A's persistence lands, replace StubSurface with a client that speaks
the real MCP protocol; scoring.py and scoreboard.py never change.

§40 measures "procedure reuse", "false reuse" and "stale-procedure detection"
(spec v4 L1390-1401). All three are properties OF THIS SURFACE'S CONTRACT:
an agent can only be credited with a refusal if check_applicability() is the
gate it consulted, so the harness records every call's verdict.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class McpSurface:
    """Protocol arm C programs against. Real implementation = MCP client."""

    def search(self, domain: str) -> list[dict]:
        """Candidate procedures for a task, as the substrate would rank them."""
        raise NotImplementedError

    def get_procedure(self, procedure_id: str) -> dict:
        """Full record INCLUDING assumptions/evidence, EXCLUDING stale ground truth."""
        raise NotImplementedError

    def check_applicability(self, procedure_id: str, context: dict) -> bool:
        """Gate: do this procedure's assumptions hold for this task context?"""
        raise NotImplementedError

    def record_refusal(self, procedure_id: str, reason: str) -> None:
        """The substrate must be able to refuse reuse (spec §39 invariant 11)."""
        raise NotImplementedError


class StubSurface(McpSurface):
    """Fixture-backed offline stand-in. Deterministic; no network, no DB."""

    def __init__(self, fixtures_dir: Path | str):
        fixtures_dir = Path(fixtures_dir)
        procs = json.loads(
            (fixtures_dir / "procedures.json").read_text(encoding="utf-8"))
        self._by_id = {p["procedure_id"]: p for p in procs["procedures"]}

    def _public(self, p: dict) -> dict:
        # Ground-truth staleness is withheld from agents; grading uses it.
        return {k: v for k, v in p.items() if k not in ("stale", "staleness_reason")}

    def search(self, domain: str) -> list[dict]:
        return [self._public(p) for p in self._by_id.values()
                if p["domain"] == domain]

    def get_procedure(self, procedure_id: str) -> dict:
        p = self._by_id.get(procedure_id)
        if p is None:
            raise KeyError(procedure_id)
        return self._public(p)

    def check_applicability(self, procedure_id: str, context: dict) -> bool:
        p = self._by_id.get(procedure_id)
        if p is None:
            raise KeyError(procedure_id)
        if context.get("bypasses_gate"):
            return True  # poisoned case: gate fails open, scorer must catch it
        return not p["stale"]

    def record_refusal(self, procedure_id: str, reason: str) -> None:
        pass  # telemetry only in the stub

    def ground_truth_stale(self, procedure_id: str) -> Optional[bool]:
        p = self._by_id.get(procedure_id)
        return None if p is None else p["stale"]
