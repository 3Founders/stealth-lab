"""
Local-only historical chat import (product spec, personal procedure
learning loop): turn a user's OWN exported Claude/ChatGPT chat history
into local procedure CANDIDATES via the SAME pipeline `local_learning.py`
already uses for ad-hoc agent runs (`LocalProcedureStore.
capture_local_procedure`, candidate/fresh/active,
provenance="system_pending_review") -- NOT a parallel memory system, NOT
a second store, NOT a second promotion path. This module never imports
asyncpg or app.db.session, directly or transitively, and never talks to
Postgres -- same DB-free guarantee `local_agent/runner.py` and
`local_learning.py` already hold.

WHY THIS EXISTS. Grepped this repo (per the calling agent's own
confirmation) and found no Claude/ChatGPT export ingestion anywhere --
`local_learning.py`'s local candidate capture only ever fires off a LIVE
`LocalAgentRunner.run()`. A user's exported chat history is a second,
also-real source of "did this person actually get a workflow to work",
sitting on disk, untouched. This module reads it and feeds the SAME
`capture_local_procedure` call `maybe_capture_local_candidate` already
uses -- distinct entry point, identical destination table, identical
V0 gate (`validate_provenance`/`validate_scope`, reused not re-invented),
identical "candidate first, earn verified later" posture.

THE CENTRAL RULE THIS MODULE EXISTS TO ENFORCE (repeated at every layer
below because it is the one way this feature could go wrong): a message
where a model SAYS what it would do, or SUGGESTS an approach ("I would
run X", "you could try Y", "one option is Z", "I recommend..."), is
NEVER evidence that X/Y/Z was actually executed. Only a real sequence
showing a command's real output/result -- a structured tool-call block
if the export carries one, or the human's own later confirmation
("that worked" / "tests passed" / a pasted error-then-fixed exchange) --
counts as "attempted" or better. A conversation whose strongest signal
never clears that bar produces ZERO candidates. This mirrors Rule 4 of
this repo's own hard rules (nothing enters storage without real support)
applied to a brand-new, easy-to-get-wrong input source.

EXPORT SHAPES -- ASSUMED VS VERIFIED. Neither export format is
versioned or formally specified by its vendor; both are reverse-engineered
public knowledge, not a fetched schema for this task. Fields this module
actually reads (everything else in a real export file is ignored, not
validated, not rejected):

  Claude (claude.ai "Export data" -> conversations.json): a JSON array
  at the file's top level. Per conversation object, read: `uuid`,
  `created_at`, `updated_at`, `chat_messages` (a list). Per chat message:
  `sender` (believed values: "human" | "assistant"), `text` (the
  flattened message string, when present), `content` (a list of content
  blocks used as a fallback/supplement when `text` is empty or to detect
  tool activity -- blocks with `type == "text"` contribute their own
  `text`; blocks with `type == "tool_use"` mark `has_tool_use`; blocks
  with `type == "tool_result"` mark `has_tool_result` and contribute any
  nested text found in their own `content`), and `created_at`. VERIFIED:
  the "sender": "human"/"assistant" + "text" + "content" shape matches
  Anthropic's publicly documented Messages API content-block vocabulary
  (`text`, `tool_use`, `tool_result`), which the claude.ai product export
  is known to reuse. ASSUMED (not independently verified this session):
  the exact top-level array-of-conversations wrapper and the
  `chat_messages` key name -- both are the commonly reported shape for
  this export, not fetched from a live Anthropic doc for this task.

  ChatGPT (chatgpt.com "Export data" -> conversations.json): a JSON array
  at the file's top level. Per conversation object, read:
  `conversation_id` (falling back to `id`), `create_time`, `update_time`,
  and `mapping` -- a dict of node_id -> node, where a node with a real
  `message` carries `message.author.role` (believed values: "system" |
  "user" | "assistant" | "tool"), `message.content.parts` (a list of
  strings, joined), and `message.create_time`. VERIFIED: this
  id/parent/children mapping-tree-of-nodes structure (built to support
  edited/regenerated branches) is the widely documented shape of
  OpenAI's data export. ASSUMED: this module does NOT walk the
  parent/children edit-branch graph to pick the single "live" branch --
  it takes every node with a non-empty `message`, sorts by
  `create_time`, and treats that as conversation order. For an export
  with regenerated/edited branches this can include a stale branch
  alongside the live one; documented here as a known, deliberate scope
  limit (linear-conversation assumption) rather than silently claimed to
  be exact. Tool-call structure in the ChatGPT export varies by client
  version and is not reliably present in the public shape, so
  `has_tool_use` is honestly always False for ChatGPT messages
  (`has_tool_result` is set only from `author.role == "tool"`, which IS
  a real, stable signal) -- see `classify_message_evidence` for how that
  asymmetry is handled without ChatGPT candidates being penalized for a
  signal Claude's export happens to carry and ChatGPT's doesn't.

WHAT THIS MODULE DOES NOT DO. No network call, no LLM call, no
generalization pass (unlike `local_learning.py`'s optional
`_abstract_capability_statement_local`, deliberately not reused here --
a first honest pass over a NEW input source should not also take on an
LLM dependency). No parallel store: every candidate this module produces
goes through the exact same `LocalProcedureStore.capture_local_procedure`
signature `local_learning.py` calls. No implicit local -> global
promotion (Rule 6) -- `provenance="system_pending_review"` and nothing
in this file calls `publish.py`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from app.local_agent.local_store import LocalProcedureStore

# ---------------------------------------------------------------------------
# Normalized shapes -- the one representation both parsers produce, so
# every function below this point (classification, candidate extraction,
# import orchestration) is written against ONE shape, never against a
# vendor's raw JSON.
# ---------------------------------------------------------------------------


@dataclass
class NormalizedMessage:
    """One real message from one real export, in the conversation's own
    order. `has_tool_use`/`has_tool_result` are real structural signals
    read from the export's own content blocks (Claude) or author role
    (ChatGPT) -- never inferred from message text."""
    role: str  # "user" | "assistant" | "tool" | "system" | other real value the export carried
    text: str
    timestamp: Optional[object]
    has_tool_use: bool = False
    has_tool_result: bool = False
    index: int = 0


@dataclass
class NormalizedConversation:
    source_type: str  # "claude" | "chatgpt"
    source_id: str
    created_at: Optional[object]
    updated_at: Optional[object]
    messages: list[NormalizedMessage] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_claude_export(path: str) -> list[NormalizedConversation]:
    """Parse a real claude.ai `conversations.json` export file. See this
    module's own docstring for exactly which fields are read and which
    are assumed vs. verified. Returns `[]` for an empty top-level array;
    a conversation with no `chat_messages` yields a `NormalizedConversation`
    with an empty `messages` list, never a fabricated placeholder."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(
            "parse_claude_export: expected a top-level JSON array of "
            "conversation objects (the real claude.ai export shape)"
        )

    conversations: list[NormalizedConversation] = []
    for conv_idx, conv_obj in enumerate(data):
        if not isinstance(conv_obj, dict):
            continue
        source_id = conv_obj.get("uuid") or f"claude_{conv_idx}"
        chat_messages = conv_obj.get("chat_messages") or []

        messages: list[NormalizedMessage] = []
        for i, cm in enumerate(chat_messages):
            if not isinstance(cm, dict):
                continue
            sender = cm.get("sender")
            role = "user" if sender == "human" else (sender or "unknown")

            content_blocks = cm.get("content") or []
            has_tool_use = False
            has_tool_result = False
            content_text_parts: list[str] = []
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    content_text_parts.append(block.get("text") or "")
                elif btype == "tool_use":
                    has_tool_use = True
                elif btype == "tool_result":
                    has_tool_result = True
                    nested = block.get("content")
                    if isinstance(nested, str):
                        content_text_parts.append(nested)
                    elif isinstance(nested, list):
                        for sub in nested:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                content_text_parts.append(sub.get("text") or "")

            text = cm.get("text") or ""
            if not text.strip() and content_text_parts:
                text = "\n".join(p for p in content_text_parts if p)

            messages.append(NormalizedMessage(
                role=role,
                text=text,
                timestamp=cm.get("created_at"),
                has_tool_use=has_tool_use,
                has_tool_result=has_tool_result,
                index=i,
            ))

        conversations.append(NormalizedConversation(
            source_type="claude",
            source_id=source_id,
            created_at=conv_obj.get("created_at"),
            updated_at=conv_obj.get("updated_at"),
            messages=messages,
        ))
    return conversations


def parse_chatgpt_export(path: str) -> list[NormalizedConversation]:
    """Parse a real chatgpt.com `conversations.json` export file. See
    this module's own docstring for the linear-conversation scope limit
    (no parent/children branch selection) and the honest
    always-False `has_tool_use`. Returns `[]` for an empty top-level
    array."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(
            "parse_chatgpt_export: expected a top-level JSON array of "
            "conversation objects (the real ChatGPT export shape)"
        )

    conversations: list[NormalizedConversation] = []
    for conv_idx, conv_obj in enumerate(data):
        if not isinstance(conv_obj, dict):
            continue
        source_id = conv_obj.get("conversation_id") or conv_obj.get("id") or f"chatgpt_{conv_idx}"
        mapping = conv_obj.get("mapping") or {}

        ordered: list[tuple] = []
        for node in mapping.values():
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if not isinstance(msg, dict):
                continue
            author_role = (msg.get("author") or {}).get("role") or "unknown"
            content = msg.get("content") or {}
            parts = content.get("parts") or []
            text = "\n".join(p for p in parts if isinstance(p, str))
            create_time = msg.get("create_time")
            if not text.strip():
                # A real, common case: hidden system/tool-config nodes with
                # no visible text -- skipped honestly, not turned into an
                # empty placeholder message.
                continue
            ordered.append((create_time, author_role, text))

        # No parent/children branch-walk (documented scope limit above):
        # sort by real create_time, treating None as earliest so a node
        # missing a timestamp is never silently dropped.
        ordered.sort(key=lambda t: (t[0] is None, t[0]))

        messages: list[NormalizedMessage] = []
        for i, (create_time, author_role, text) in enumerate(ordered):
            messages.append(NormalizedMessage(
                role=author_role,
                text=text,
                timestamp=create_time,
                has_tool_use=False,  # honestly never available -- see module docstring
                has_tool_result=(author_role == "tool"),
                index=i,
            ))

        conversations.append(NormalizedConversation(
            source_type="chatgpt",
            source_id=source_id,
            created_at=conv_obj.get("create_time"),
            updated_at=conv_obj.get("update_time"),
            messages=messages,
        ))
    return conversations


# ---------------------------------------------------------------------------
# Evidence classification
# ---------------------------------------------------------------------------

EVIDENCE_LEVELS = ("discussion", "suggested", "attempted", "completed", "verified")
_LEVEL_RANK = {level: i for i, level in enumerate(EVIDENCE_LEVELS)}

# Explicit confirmation of a CHECK (tests, CI, a build) -- the bar for
# "verified". Deliberately narrower than "completed": a plain "that
# worked" does not name a check.
_VERIFIED_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\btests?\s+pass(ed|es|ing)?\b",
        r"\ball tests? (pass|passed|passing|green)\b",
        r"\bci\s+(is\s+)?green\b",
        r"\bbuild\s+(is\s+)?green\b",
        r"\bconfirmed\s+(it\s+)?works?\b",
        r"\bverified\s+(it\s+)?works?\b",
        r"\bsuite\s+pass(ed|es)?\b",
    )
]

# The human reporting the outcome actually landed, but without naming a
# specific check -- "completed", one rung below "verified".
_COMPLETED_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bthat worked\b",
        r"\bit worked\b",
        r"\bworks now\b",
        r"\bthat fixed it\b",
        r"\bthat did it\b",
        r"\bfixed\s+it\b",
        r"\bno more error\b",
        r"\bresolved\b",
    )
]

# A human turn showing they actually ran something and are reporting a
# real (possibly still-failing) result -- pasted output/error, not a
# plan. This is "attempted", not "completed": running a command and
# seeing an error is real evidence of an attempt, not of success.
_ATTEMPTED_OUTPUT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"here'?s the (output|error|result|traceback)",
        r"here is the (output|error|result|traceback)",
        r"^traceback \(most recent call last\)",
        r"\bi ran (it|that|the command|this)\b",
        r"\bran it and\b",
        r"\bgot this error\b",
        r"\bgot the following error\b",
    )
]

# Hedged, not-yet-executed language -- "I would", "you could", "you
# might", "consider", "I recommend/suggest". This is the CRITICAL never-
# treat-as-executed bucket the task calls out by name.
_SUGGESTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bi would\b",
        r"\byou could\b",
        r"\byou might\b",
        r"\byou can\b",
        r"\bconsider\b",
        r"\bmaybe try\b",
        r"\btry running\b",
        r"\bone option\b",
        r"\bi suggest\b",
        r"\bi recommend\b",
        r"\byou should\b",
    )
]


def classify_message_evidence(msg: NormalizedMessage) -> str:
    """Classify ONE message's own evidence strength, conservatively.
    Returns one of `EVIDENCE_LEVELS`. Defaults to "discussion" whenever
    nothing stronger is clearly present -- ambiguity never rounds up.

    THE RULE THIS FUNCTION ENFORCES: a model (or a human) merely
    DESCRIBING an action ("I would run X", "you could do X") never
    classifies above "suggested" -- regardless of how confident or
    detailed the description is. Only a REAL structural signal (a
    `tool_result` block, an export's own "tool" role message) or the
    HUMAN's own later confirmation/pasted-output text can reach
    "attempted" or higher.

    Role matters: an assistant message's own TEXT (no tool blocks) can
    reach "suggested" at most, never higher -- an assistant claiming "I
    ran it and it works" in plain prose, with no accompanying tool_result
    block, is still just the assistant's unverified claim, not real
    evidence. A `tool_result`/`tool` role message, or a `tool_use` block
    on an assistant message, is real structural evidence of an actual
    call and can reach "attempted" independent of role.
    """
    text = msg.text or ""

    # Structural tool signals are role-independent real evidence.
    if msg.has_tool_result or msg.role == "tool":
        if any(p.search(text) for p in _VERIFIED_PATTERNS):
            return "verified"
        if any(p.search(text) for p in _COMPLETED_PATTERNS):
            return "completed"
        return "attempted"

    if msg.role == "user":
        # A human's own words are real evidence of what actually
        # happened on their machine.
        if any(p.search(text) for p in _VERIFIED_PATTERNS):
            return "verified"
        if any(p.search(text) for p in _COMPLETED_PATTERNS):
            return "completed"
        if any(p.search(text) for p in _ATTEMPTED_OUTPUT_PATTERNS):
            return "attempted"
        if any(p.search(text) for p in _SUGGESTION_PATTERNS):
            return "suggested"
        return "discussion"

    # assistant / system / unknown role, text only (no tool_result above):
    # capped at "suggested" no matter what the text claims -- see
    # docstring. A real tool_use block (assistant-initiated call) is
    # still structural evidence that a call was actually made, so it can
    # reach "attempted" even though the assistant's own prose cannot.
    if msg.has_tool_use:
        if any(p.search(text) for p in _VERIFIED_PATTERNS):
            return "verified"
        if any(p.search(text) for p in _COMPLETED_PATTERNS):
            return "completed"
        return "attempted"
    if any(p.search(text) for p in _SUGGESTION_PATTERNS):
        return "suggested"
    return "discussion"


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

_MIN_CANDIDATE_LEVEL = _LEVEL_RANK["attempted"]


def _derive_goal(conv: NormalizedConversation) -> str:
    """The conversation's own first non-empty user message, truncated --
    never a generic template, never derived from assistant text (the
    HUMAN's ask is the real goal, not the model's framing of it)."""
    for msg in conv.messages:
        if msg.role == "user" and (msg.text or "").strip():
            return msg.text.strip()[:200]
    return f"imported {conv.source_type} conversation {conv.source_id}"


def extract_candidates_from_conversation(conv: NormalizedConversation) -> list[dict]:
    """Real, conservative extraction: classifies every message in the
    conversation's own order, and emits AT MOST ONE candidate dict --
    shaped for `LocalProcedureStore.capture_local_procedure` -- only
    when the conversation's strongest real evidence signal reaches
    "attempted" or better. A conversation whose strongest signal stays at
    "discussion"/"suggested" (including one containing hedged language
    like "I would ...") returns `[]`: no candidate is ever fabricated
    from a recommendation alone.

    Steps are built ONE PER real message, in the conversation's own
    order -- never fabricated, never reordered, never summarized away.
    Each step's own `properties.evidence_level` records THAT message's
    own classification, so the evidence trail supporting the final
    candidate is auditable message-by-message, not just asserted at the
    conversation level.
    """
    if not conv.messages:
        return []

    levels = [classify_message_evidence(m) for m in conv.messages]
    max_level = max(levels, key=lambda level: _LEVEL_RANK[level])
    if _LEVEL_RANK[max_level] < _MIN_CANDIDATE_LEVEL:
        return []

    goal = _derive_goal(conv)
    steps = []
    for msg, level in zip(conv.messages, levels):
        steps.append({
            "order": msg.index,
            "goal": (msg.text or "").strip()[:200],
            "properties": {
                "role": msg.role,
                "evidence_level": level,
                "has_tool_use": msg.has_tool_use,
                "has_tool_result": msg.has_tool_result,
                "timestamp": msg.timestamp,
            },
        })

    evidence_ref = {
        "kind": f"{conv.source_type}_chat_history",
        "source_conversation_id": conv.source_id,
        "evidence_level": max_level,
        "message_count": len(conv.messages),
    }

    candidate = {
        "name": f"chat history candidate: {goal[:80]}",
        "goal": goal,
        "steps": steps,
        "scope": {
            "source_type": f"{conv.source_type}_chat",
            "source_conversation_id": conv.source_id,
            "privacy": "local-only",
        },
        "evidence_refs": [evidence_ref],
        "source_episode_ids": [],
        "provenance": "system_pending_review",
        "scope_type": "session",
        "scope_entity_id": conv.source_id,
    }
    return [candidate]


# ---------------------------------------------------------------------------
# Import orchestration
# ---------------------------------------------------------------------------

_PARSERS = {
    "claude": parse_claude_export,
    "chatgpt": parse_chatgpt_export,
}


def import_chat_history(
    path: str,
    source_type: str,
    local_store: LocalProcedureStore,
) -> dict:
    """Parse a real export file and write real candidates into
    `local_store` via the SAME `capture_local_procedure` call
    `local_learning.py` uses -- no parallel store, no parallel schema.
    `source_type` must be "claude" or "chatgpt".

    Returns `{"conversations_parsed", "candidates_created",
    "discussion_only_skipped"}` -- real counts from this run, not
    estimates. `discussion_only_skipped` counts conversations that
    produced zero candidates (the honest, expected outcome for most
    real chat history, per this module's own conservative evidence
    bar).
    """
    parser = _PARSERS.get(source_type)
    if parser is None:
        raise ValueError(
            f"import_chat_history: unknown source_type {source_type!r} "
            f"(expected one of {sorted(_PARSERS)})"
        )

    conversations = parser(path)

    conversations_parsed = 0
    candidates_created = 0
    discussion_only_skipped = 0

    for conv in conversations:
        conversations_parsed += 1
        candidates = extract_candidates_from_conversation(conv)
        if not candidates:
            discussion_only_skipped += 1
            continue
        for candidate in candidates:
            local_store.capture_local_procedure(
                name=candidate["name"],
                goal=candidate["goal"],
                steps=candidate["steps"],
                scope=candidate["scope"],
                evidence_refs=candidate["evidence_refs"],
                source_episode_ids=candidate["source_episode_ids"],
                provenance=candidate["provenance"],
                scope_type=candidate["scope_type"],
                scope_entity_id=candidate["scope_entity_id"],
            )
            candidates_created += 1

    return {
        "conversations_parsed": conversations_parsed,
        "candidates_created": candidates_created,
        "discussion_only_skipped": discussion_only_skipped,
    }
