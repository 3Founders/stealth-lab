"""
Data model for semantic context compaction.

Compaction produces a DERIVED working-context view. It never mutates the
`ContextItem`s it is given and never touches the canonical raw trajectory
(trace_events / collector files); a view can always be recomputed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

CHARS_PER_TOKEN = 4  # same rough heuristic as services/context_compiler.py


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def sha(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Action(str, Enum):
    KEEP_VERBATIM = "KEEP_VERBATIM"
    KEEP_COMPACT = "KEEP_COMPACT"
    KEEP_REFERENCE_ONLY = "KEEP_REFERENCE_ONLY"
    DROP = "DROP"


# Pair-level vocabulary for a tool call + result, derived from the action.
PAIR_DISPOSITION = {
    Action.KEEP_VERBATIM: "KEEP_RESULT",
    Action.KEEP_COMPACT: "KEEP_COMPACT",
    Action.KEEP_REFERENCE_ONLY: "KEEP_CALL_ONLY",
    Action.DROP: "DROP_PAIR",
}

KINDS = (
    "system", "user_message", "assistant_message", "tool_call", "tool_result",
    "error", "test", "file_read", "search", "command", "status",
)


@dataclass
class ContextItem:
    item_id: str
    kind: str
    text: str
    sequence: int = 0
    tool_name: Optional[str] = None
    call_id: Optional[str] = None      # links a tool_call to its tool_result
    path: Optional[str] = None         # file_read target
    content_hash: Optional[str] = None  # hash of what was read (for staleness)
    failed: bool = False               # the call errored / non-zero exit / failing test
    raw_ref: Optional[str] = None      # id of the canonical raw event this came from
    meta: dict = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    def fingerprint(self) -> str:
        return sha(self.kind, self.text, self.tool_name, self.call_id, self.path, self.failed)


@dataclass
class Unit:
    """One judged unit: a tool call+result PAIR, or a single item."""
    unit_id: str
    items: list[ContextItem]

    @property
    def tokens(self) -> int:
        return sum(i.tokens for i in self.items)

    @property
    def result(self) -> ContextItem:
        return self.items[-1]

    def fingerprint(self) -> str:
        return sha([i.fingerprint() for i in self.items])


@dataclass
class StealthState:
    """Concise durable-state context for retention judgment -- retrieved
    RELEVANT state, never the whole database."""
    goal: str = ""
    goal_version: str = ""
    run_id: Optional[str] = None
    node_id: Optional[str] = None
    node_version: str = ""
    node_instructions: str = ""
    blockers: list[str] = field(default_factory=list)          # open blockers
    open_questions: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)           # {claim_id, statement}
    artifacts: list[str] = field(default_factory=list)         # persisted artifact refs
    evidence: list[str] = field(default_factory=list)
    implementations: list[str] = field(default_factory=list)   # persisted command/implementation refs
    constraints: list[str] = field(default_factory=list)       # user/security constraints
    handoff: Optional[str] = None                              # current ownership/handoff info
    file_hashes: dict = field(default_factory=dict)            # path -> CURRENT content hash

    def durable_ref_ids(self) -> set[str]:
        ids = {str(c.get("claim_id") or c.get("id")) for c in self.claims}
        ids.update(self.artifacts)
        ids.update(self.evidence)
        ids.update(self.implementations)
        return ids

    def for_prompt(self) -> dict:
        return {
            "goal": self.goal, "node": self.node_id, "node_instructions": self.node_instructions[:600],
            "open_blockers": self.blockers[:20], "open_questions": self.open_questions[:20],
            "recent_decisions": self.decisions[:20],
            "claims": [{"id": str(c.get("claim_id") or c.get("id")), "statement": str(c.get("statement"))[:240]}
                       for c in self.claims[:40]],
            "artifacts": self.artifacts[:30], "evidence": self.evidence[:30],
            "implementations": self.implementations[:30], "constraints": self.constraints[:20],
        }

    def state_hash(self) -> str:
        p = self.for_prompt()
        p["file_hashes"] = self.file_hashes
        return sha(p)


@dataclass
class ContextRetentionDecision:
    item_id: str
    action: Action
    relevance: float = 0.5
    reason: str = ""
    durable_refs: list[str] = field(default_factory=list)
    confidence: float = 0.5
    source: str = "semantic"           # "semantic" | "pin" | "guard" (a deterministic safety upgrade)
    pair_disposition: Optional[str] = None


@dataclass
class ViewEntry:
    unit_id: str
    item_ids: list[str]
    action: Action
    content: str
    durable_refs: list[str] = field(default_factory=list)
    tokens: int = 0


@dataclass
class CompactionResult:
    status: str  # compacted | skipped_below_threshold | skipped_unavailable
    view: list[ViewEntry]
    decisions: list[ContextRetentionDecision] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    provider: Optional[str] = None
    fallback_used: bool = False
    retry_job_id: Optional[int] = None
    used_last_valid_view: bool = False
    reason: str = ""

    def render(self) -> str:
        return "\n".join(e.content for e in self.view)
