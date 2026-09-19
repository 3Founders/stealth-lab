"""
Prompt builders + strict parsers shared by every LLM-backed provider (Gemini,
Gemma, any future OpenAI-compatible one). Retention judgment and summary
generation are SEPARATE prompts on purpose: a fast decision-oriented judge
(JEV) can serve retention without being forced to synthesize text.

Parsers raise ValueError on any contract violation; the provider turns that
into a retryable ProviderError. They never repair/guess a decision.
"""
from __future__ import annotations

import json
import re
from typing import Any

RETENTION_PROMPT_VERSION = "context_retention@v2"
SUMMARY_PROMPT_VERSION = "context_summary@v1"
RELATION_PROMPT_VERSION = "claim_relation@v1"
IDENTITY_PROMPT_VERSION = "identity@v1"

ACTIONS = ("KEEP_VERBATIM", "KEEP_COMPACT", "KEEP_REFERENCE_ONLY", "DROP")

RETENTION_SYSTEM_PROMPT = (
    "You decide what an AI coding agent still needs in its working context. "
    "You get concise durable state (goal, node, blockers, questions, decisions, "
    "claims, artifacts) and a batch of context UNITS (a tool call+result pair, "
    "or a single message/observation). For EACH unit choose exactly one action:\n"
    "KEEP_VERBATIM: exact content still matters (unresolved failure, exact "
    "output being acted on, user constraint).\n"
    "KEEP_COMPACT: content matters but can be restated as structured facts.\n"
    "KEEP_REFERENCE_ONLY: already persisted as durable state (cite durable_refs) "
    "and unchanged; keep only a pointer.\n"
    "DROP: no longer needed and not persisted-worthy.\n"
    "Routine JUNK is DROP, even in a short session: progress narration "
    "(\"reading this file now\", \"let me check\"), waiting/polling for another "
    "agent or a background task, \"launched background task\" acknowledgements, "
    "heartbeats, empty or duplicate status lines, and tool calls whose result "
    "added nothing that was used later. "
    "Rules: never drop an unresolved failure or blocker. Never drop negative "
    "knowledge (a failed approach that prevents a retry loop) -- use KEEP_COMPACT. "
    "Only use KEEP_REFERENCE_ONLY/DROP for file reads when content_changed is "
    "false and durable_refs cover the facts. Units with pinned=true must not be "
    "DROPped. Respond with EXACTLY one JSON object: "
    '{"decisions":[{"unit_id":"...","action":"KEEP_VERBATIM|KEEP_COMPACT|'
    'KEEP_REFERENCE_ONLY|DROP","relevance":<0-1>,"reason":"<=12 words",'
    '"durable_refs":["C-1"],"confidence":<0-1>}]}. One decision per unit_id; '
    "never invent unit_ids or durable_refs not present in the input."
)

SUMMARY_SYSTEM_PROMPT = (
    "Compress context units into STRUCTURED, terse records for an AI agent -- "
    "not prose. Preserve every operationally relevant literal: file paths, "
    "identifiers, commands, test names and results, error messages, decisions, "
    "dependencies, selected implementation, important values. Record what was "
    "attempted, inputs, result, failure, unresolved issue. Respond with EXACTLY "
    'one JSON object: {"summaries":[{"unit_id":"...","summary":{"kind":"...",'
    '"attempted":"...","inputs":"...","result":"...","failure":"...",'
    '"unresolved":"...","paths":[],"ids":[],"commands":[],"values":{}}}]}. '
    "Omit empty keys. Never add facts that are not in the unit."
)


def _loads_object(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def build_retention_user(state: dict, units: list[dict]) -> str:
    return json.dumps({"state": state, "units": units}, default=str, ensure_ascii=False)


def parse_retention(text: str, unit_ids: list[str]) -> list[dict]:
    """Strict: exactly one valid decision per requested unit_id."""
    data = _loads_object(text)
    raw = data.get("decisions")
    if not isinstance(raw, list):
        raise ValueError("missing decisions[]")
    known = set(unit_ids)
    out: dict[str, dict] = {}
    for d in raw:
        if not isinstance(d, dict):
            raise ValueError("decision must be an object")
        uid = str(d.get("unit_id"))
        if uid not in known:
            raise ValueError(f"unknown unit_id {uid!r}")
        if d.get("action") not in ACTIONS:
            raise ValueError(f"invalid action {d.get('action')!r}")
        out[uid] = {
            "unit_id": uid,
            "action": d["action"],
            "relevance": _unit_float(d.get("relevance"), 0.5),
            "reason": str(d.get("reason", ""))[:200],
            "durable_refs": [str(r) for r in (d.get("durable_refs") or [])],
            "confidence": _unit_float(d.get("confidence"), 0.5),
        }
    missing = known - set(out)
    if missing:
        raise ValueError(f"missing decisions for {sorted(missing)[:5]}")
    return [out[u] for u in unit_ids]


def build_summary_user(state: dict, units: list[dict]) -> str:
    return json.dumps({"state": state, "units": units}, default=str, ensure_ascii=False)


def parse_summaries(text: str, unit_ids: list[str]) -> dict[str, Any]:
    data = _loads_object(text)
    raw = data.get("summaries")
    if not isinstance(raw, list):
        raise ValueError("missing summaries[]")
    out: dict[str, Any] = {}
    for s in raw:
        uid = str(s.get("unit_id")) if isinstance(s, dict) else None
        if uid not in unit_ids:
            raise ValueError(f"unknown unit_id {uid!r}")
        summary = s.get("summary")
        if not summary:
            raise ValueError(f"empty summary for {uid}")
        out[uid] = summary
    missing = set(unit_ids) - set(out)
    if missing:
        raise ValueError(f"missing summaries for {sorted(missing)[:5]}")
    return out


def _unit_float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


# ---- identity resolution (Goal / Claim / Procedure) ------------------------
# Relation of A (the NEW candidate) to B (an EXISTING object). One vocabulary
# per object kind; a reply outside it is a contract violation (retried, never
# repaired or guessed).
IDENTITY_RELATIONS = {
    "goal": ("same", "specializes", "generalizes", "related", "distinct"),
    "claim": ("same", "specializes", "generalizes", "related", "contradicts", "distinct"),
    "procedure": ("same", "refinement", "distinct"),
}

IDENTITY_SYSTEM_PROMPTS = {
    "goal": (
        "You decide whether two GOALS (desired outcomes for an AI agent) are the SAME goal. "
        "Two goals are the same only if achieving one necessarily means achieving the other in "
        "the same context, ignoring wording. A goal that merely shares words, a domain or a tool "
        "is NOT the same. Reply with EXACTLY one JSON object: "
        '{"relation":"same|specializes|generalizes|related|distinct","confidence":<0-1>}. '
        "specializes: A is a narrower case of B. generalizes: A is broader than B. "
        "related: overlapping but neither. distinct: unrelated or different outcome."
    ),
    "claim": (
        "You compare two CLAIMS (propositions). Reply with EXACTLY one JSON object: "
        '{"relation":"same|specializes|generalizes|related|contradicts|distinct","confidence":<0-1>}. '
        "same: identical proposition, any wording. contradicts: they cannot both be true. "
        "Never call two claims the same unless the proposition is identical."
    ),
    "procedure": (
        "You compare two PROCEDURES that achieve the same goal. Reply with EXACTLY one JSON "
        'object: {"relation":"same|refinement|distinct","confidence":<0-1>}. '
        "same: equivalent method. refinement: A is a newer/improved version of the same method B. "
        "distinct: a genuinely different method, even if it reaches the same goal."
    ),
}


def build_identity_user(kind: str, a: str, b: str) -> str:
    return "A (new): " + a + "\nB (existing): " + b


def parse_identity(kind: str, body: dict) -> dict:
    relation = body.get("relation")
    if relation not in IDENTITY_RELATIONS[kind]:
        raise ValueError(f"invalid {kind} relation {relation!r}")
    return {"relation": relation, "confidence": _unit_float(body.get("confidence"), 0.0)}
