"""
The observation layer (ticket 04, memory-substrate map): interprets raw
trace_events into structured, immutable observations. Sits between
"what literally happened" (trace_events, ticket 06) and "what we believe"
(claims, ticket 03). Observations are explicitly NOT facts -- spec.md is
emphatic on this, and nothing here changes it.

Two extractors, both built, per ticket 04's own reasoning: "no study
reports a rule-based baseline before adding a model for agent traces, so
the honest move is to build both and measure the delta rather than
assume one."

Same WHY-NOT-KnowledgeUpdater reasoning as claims.py/failure_capture.py:
these are trusted, internal writes, not a dispatch through
apply()/apply_generated()'s op-type machinery.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

import asyncpg

from app.services.claims import capture_claim
from app.services.embeddings import Embedder

CREATED_BY = "observation_extraction"

DETERMINISTIC_EXTRACTOR_NAME = "deterministic_v1"
DETERMINISTIC_CODE_VERSION = "1"

MODEL_EXTRACTOR_NAME = "semantic_label_v1"
MODEL_CODE_VERSION = "1"

_TEST_COMMAND_MARKERS = ("pytest", "npm test", "npm run test", "go test", "cargo test", "jest")


def _looks_like_test_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _TEST_COMMAND_MARKERS)


def extract_deterministic_observations(trace_event: dict) -> list[dict]:
    """
    Pure function, no I/O, no LLM call: given one real trace_event row
    (as returned by asyncpg -- a dict-like Record), derive zero or more
    deterministic observations. Covers exactly the categories ticket 04
    names: "files touched, tests run, commands executed, commits made."

    Returns plain dicts (observation_type, label, properties) -- not yet
    persisted; see persist_observation() for the write path. Kept as a
    pure function specifically so it's trivially unit-testable without a
    database or network at all.
    """
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


_SEMANTIC_LABEL_SYSTEM_PROMPT = """You interpret a single coding-agent tool call and produce a
short, semantic label describing what it actually did, in the same spirit as this real example:
"edit file X" -> "authentication implementation was modified".

Rules:
- One sentence, plain language, no code, no quotes.
- Describe what changed or happened, not how (no tool names, no file-format details).
- If the tool call is too generic to say anything semantic (e.g. a directory listing), reply
  with exactly: NONE
"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def extract_model_observation(
    trace_event: dict,
    client: Any,
    model: str = "gemma-4-31B-it",
) -> Optional[dict]:
    """
    Real LLM call -- NOT testable live in this environment (no network
    path to General Compute from this sandbox, confirmed the same way
    every other LLM-calling function in this codebase was this session).
    `client` is injected (same pattern as solve_task/decompose_task) so
    the surrounding logic -- prompt construction, response parsing,
    version-component hashing -- is fully testable with a scripted fake
    client, even though the real call itself isn't verified here.

    Returns None if the model judged the event too generic to label
    (real, deliberate "NONE" contract above) -- not every event deserves
    a semantic observation, and forcing one would be exactly the kind of
    noise ticket 04's own confidence-field decision already warns against
    for a different field.
    """
    tool_name = trace_event.get("tool_name") or "unknown tool"
    tool_input = trace_event.get("tool_input") or {}
    if isinstance(tool_input, str):
        tool_input = json.loads(tool_input)
    tool_output = trace_event.get("tool_output") or {}
    if isinstance(tool_output, str):
        tool_output = json.loads(tool_output)

    user_prompt = (
        f"Tool: {tool_name}\n"
        f"Input: {json.dumps(tool_input, default=str)[:1000]}\n"
        f"Output: {json.dumps(tool_output, default=str)[:1000]}"
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SEMANTIC_LABEL_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=100,
    )
    label = response.choices[0].message.content.strip()
    if label == "NONE" or not label:
        return None

    return {
        "observation_type": "semantic_label",
        "label": label,
        "properties": {"tool_name": tool_name},
        "model_id": model,
        "prompt_hash": _hash(_SEMANTIC_LABEL_SYSTEM_PROMPT),
        "decoding_params_hash": _hash(json.dumps({"temperature": 0.0, "max_tokens": 100})),
    }


async def persist_observation(
    pool: asyncpg.Pool,
    *,
    observation_type: str,
    label: str,
    extractor_kind: str,
    event_ids: list[str],
    properties: Optional[dict] = None,
    model_id: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    decoding_params_hash: Optional[str] = None,
) -> str:
    """
    Writes one observation row plus one observation_events link per real
    event_id given. Real idempotency note, stated honestly rather than
    silently assumed: this function does NOT deduplicate -- re-extracting
    from the same event twice produces two distinct observation rows,
    consistent with observations being immutable/re-derived rather than
    superseded (ticket 04's own reasoning). Deduplication, if wanted, is
    the caller's job (e.g. checking observation_events for this event_id
    + this extractor_name before calling this).
    """
    extractor_name = (
        DETERMINISTIC_EXTRACTOR_NAME if extractor_kind == "deterministic"
        else MODEL_EXTRACTOR_NAME
    )
    code_version = (
        DETERMINISTIC_CODE_VERSION if extractor_kind == "deterministic"
        else MODEL_CODE_VERSION
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            obs_id = await conn.fetchval(
                "INSERT INTO observations "
                "(observation_type, label, extractor_kind, extractor_name, "
                " code_version, model_id, prompt_hash, decoding_params_hash, "
                " properties, created_by) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id",
                observation_type, label, extractor_kind, extractor_name,
                code_version, model_id, prompt_hash, decoding_params_hash,
                properties or {}, CREATED_BY,
            )
            for event_id in event_ids:
                await conn.execute(
                    "INSERT INTO observation_events (observation_id, event_id) "
                    "VALUES ($1, $2)",
                    obs_id, event_id,
                )
    return str(obs_id)


async def promote_observation_to_claim(
    pool: asyncpg.Pool,
    *,
    observation_id: str,
    task_ids: list[str],
    justification_episode_id: Optional[str] = None,
    embedder: Optional[Embedder] = None,
) -> Optional[str]:
    """
    Real wiring ticket 04 owns per ticket 10's amendment: reads a real
    observation row and creates a claim from it, with epistemic_status
    assigned correctly from the observation's own extractor_kind --
    'observed' for deterministic, 'inferred' for model-derived. This is
    the one decision ticket 04 was explicitly given ("Ticket 04 owns how
    the value is assigned"), not invented here.

    extraction_version passed through as a single string built from the
    observation's own stored components -- claims.py's ClaimProperties
    validates extraction_version as one string field, while observations
    stores the components separately (ticket 04's own reasoning: a
    single hash destroys the ability to ask "which came from model X").
    Both are honored: components stay queryable on the observation row
    that produced this claim; the claim gets a readable composite.
    """
    row = await pool.fetchrow(
        "SELECT observation_type, label, extractor_kind, extractor_name, "
        "code_version, model_id FROM observations WHERE id = $1",
        observation_id,
    )
    if row is None:
        return None

    epistemic_status = "observed" if row["extractor_kind"] == "deterministic" else "inferred"
    version_parts = [row["extractor_name"], row["code_version"]]
    if row["model_id"]:
        version_parts.append(row["model_id"])
    extraction_version = ":".join(version_parts)

    return await capture_claim(
        pool,
        statement=row["label"],
        task_ids=task_ids,
        justification_episode_id=justification_episode_id,
        claim_type=row["observation_type"],
        epistemic_status=epistemic_status,
        extraction_version=extraction_version,
        embedder=embedder,
    )
