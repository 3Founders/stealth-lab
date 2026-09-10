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

from app.services.access import AccessScope, visibility_predicate
from app.services.claims import capture_claim
from app.services.embeddings import Embedder

CREATED_BY = "observation_extraction"

DETERMINISTIC_EXTRACTOR_NAME = "deterministic_v1"
DETERMINISTIC_CODE_VERSION = "1"

MODEL_EXTRACTOR_NAME = "semantic_label_v1"
MODEL_CODE_VERSION = "1"

# Band 2.8: claim promotion is itself a versioned extraction step -- this
# stamp lands in the promoted claim's properties so replay can verify it
# the same way it verifies every other stage's stamp.
CLAIM_PROMOTION_STAMP = "claim_promotion@1"

_TEST_COMMAND_MARKERS = ("pytest", "npm test", "npm run test", "go test", "cargo test", "jest")


def _looks_like_test_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _TEST_COMMAND_MARKERS)


_MAX_JSON_UNWRAP = 4


def _decode_json_field(value: Any) -> dict:
    """Decode a JSONB column that may have been encoded once, twice, or not
    at all, into a dict -- returning {} for anything that isn't one.

    WHY A LOOP AND NOT A SINGLE json.loads(): RUNBOOK.md's own documented
    pitfall -- "asyncpg jsonb codec is registered on app pools; passing
    pre-dumped JSON strings to $n::jsonb DOUBLE-ENCODES them (stored as
    json-string)". A double-encoded column decodes to a *str*, not a dict,
    so the previous single-shot `if isinstance(str): json.loads()` handed
    a str to the caller and every `.get()` after it raised
    `'str' object has no attribute 'get'`. That is not hypothetical: it
    failed 32 of 3313 real ingestion jobs on 2026-08-28.

    Bounded rather than `while True` so a pathological value cannot spin;
    _MAX_JSON_UNWRAP is far above the two levels any real double-encode
    produces. Undecodable or non-dict input yields {} -- an observation
    extractor must skip a malformed event, never crash the job that owns
    a whole trace_event.
    """
    for _ in range(_MAX_JSON_UNWRAP):
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return {}
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


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
    tool_input = _decode_json_field(trace_event.get("tool_input"))

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
            # `trace_events.success` is the collector's recorded outcome
            # for this concrete tool invocation. Preserve it when it is an
            # actual boolean; absence remains absent (UNKNOWN), never an
            # assumed pass. The episode->procedure gate requires this
            # explicit signal rather than treating the command text itself
            # as verification.
            properties = {"command": command}
            if isinstance(trace_event.get("success"), bool):
                properties["passed"] = trace_event["success"]
            observations.append({
                "observation_type": "test_run",
                "label": f"Ran tests: {command.strip()}",
                "properties": properties,
            })
        else:
            observations.append({
                "observation_type": "command_executed",
                "label": f"Executed: {command.strip()}",
                "properties": {"command": command},
            })

    return observations


_SEMANTIC_LABEL_SYSTEM_PROMPT = """You interpret a single coding-agent tool call and produce a
TERSE semantic label describing what it actually did, in the same spirit as this real example:
"edit file X" -> "authentication implementation was modified".

Rules:
- Aim for 3-6 words, never more than 8: subject + past-tense verb, nothing else. Good:
  "authentication implementation was modified", "build output directory was deleted",
  "continuous integration pipeline configuration added", "database container started".
- Do NOT quote file paths, commands, commit hashes, or other exact strings from the event.
- Do NOT add parentheticals, clauses, or explanations of why/how/for-what-purpose -- those pad
  the label without changing its meaning and are wrong even when true.
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
    `client` is injected (same pattern as find_best_way/decompose_task) so
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
    # Same double-encode hazard as the deterministic extractor above --
    # one shared decoder so the two cannot drift apart.
    tool_input = _decode_json_field(trace_event.get("tool_input"))
    tool_output = _decode_json_field(trace_event.get("tool_output"))

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
    owner_id: Optional[str] = None,
    visibility: str = "public",
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

    REAL GAP FIXED: `14_observations.sql` gives this table real
    `owner_id`/`visibility` columns (ticket 09's pair, correctly present
    together), but this INSERT never populated them -- every observation
    silently landed as `visibility='public'`, `owner_id=NULL` regardless
    of who or what produced it. Now real parameters, not decorative
    columns.
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"visibility must be 'public' or 'private', got {visibility!r}")

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
                " properties, created_by, owner_id, visibility) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::visibility_level) "
                "RETURNING id",
                observation_type, label, extractor_kind, extractor_name,
                code_version, model_id, prompt_hash, decoding_params_hash,
                properties or {}, CREATED_BY, owner_id, visibility,
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
    scope: Optional[AccessScope] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
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

    REAL GAP FIXED: this fetch was previously unscoped -- any caller
    could promote (and thereby read the content of, via the resulting
    claim) any observation by id regardless of visibility. `scope` is
    now applied via access.py's visibility_predicate() (defaults to
    unrestricted() for internal callers, same convention as elsewhere).
    The resulting claim also inherits the observation's own
    owner_id/visibility rather than silently reverting to public -- a
    private observation promoted to a claim must not leak into the
    shared commons just because capture_claim()'s defaults are public.

    `scope_type`/`scope_entity_id`: an explicit caller value always wins.
    When omitted and `justification_episode_id` is given, derived from
    that episode's own `project_id` (db/17_episode_project_columns.sql --
    a real, existing column, populated by trace_worker.py where it's
    known) as scope_type='project'. When neither an explicit value nor a
    derivable project_id exists, left None -- same as capture_claim()'s
    own honest default, not silently promoted to 'global'.
    """
    scope = scope or AccessScope.unrestricted()
    if scope_type is None and justification_episode_id is not None:
        project_id = await pool.fetchval(
            "SELECT project_id FROM episodes WHERE id = $1::uuid", justification_episode_id,
        )
        if project_id:
            scope_type, scope_entity_id = "project", project_id
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    row = await pool.fetchrow(
        "SELECT observation_type, label, extractor_kind, extractor_name, "
        "code_version, model_id, owner_id, visibility::text AS visibility "
        f"FROM observations WHERE id = $1 AND {vis_sql}",
        observation_id, *vis_params,
    )
    if row is None:
        return None

    epistemic_status = "observed" if row["extractor_kind"] == "deterministic" else "inferred"
    version_parts = [row["extractor_name"], row["code_version"]]
    if row["model_id"]:
        version_parts.append(row["model_id"])
    extraction_version = ":".join(version_parts)

    claim_id = await capture_claim(
        pool,
        statement=row["label"],
        task_ids=task_ids,
        justification_episode_id=justification_episode_id,
        claim_type=row["observation_type"],
        epistemic_status=epistemic_status,
        extraction_version=extraction_version,
        properties={"promoted_by": CLAIM_PROMOTION_STAMP},
        embedder=embedder,
        owner_id=row["owner_id"],
        visibility=row["visibility"],
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )

    # Band 2.8 (replayability): first-class provenance link from the
    # claim back to the observation it was promoted from. Without this
    # row the spec's replay sentence -- "claim C was produced by
    # extractor X from trace E" -- stops at the claim: the extractor
    # version rode in properties.extraction_version, but nothing joined
    # a claim to its source observation/events. Written after the
    # capture (same follow-up-write idiom procedure_extraction/__init__.py
    # uses for its post-capture_procedure UPDATE), ON CONFLICT DO
    # NOTHING so a re-promotion of the same pair is idempotent.
    if claim_id is not None:
        await pool.execute(
            "INSERT INTO claim_sources (claim_id, observation_id) "
            "VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING",
            claim_id, observation_id,
        )
    return claim_id
