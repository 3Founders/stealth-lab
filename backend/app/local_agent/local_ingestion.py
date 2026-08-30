"""
SKILL.md -> LocalProcedureStore ingestion. The local-store counterpart to
app/services/skill_ingestion.py::ingest_skill_md -- reuses that module's
REAL parser (parse_skill_md, pure, no DB) and writes into
LocalProcedureStore instead of Postgres via capture_procedure().

DELIBERATELY NOT reusing check_novelty() -- that function calls
find_applicable_procedures() against Postgres to do an embedding-based
dedup check against the GLOBAL corpus, which is exactly the network/DB
dependency this local path exists to avoid. A local corpus is small
enough per workspace that novelty checking (if wanted) can be a thin
lexical pre-check by the caller before calling this, not a hidden
network round trip inside it. This module does not attempt dedup at all
-- an honest, disclosed omission, not a silent gap.
"""
from __future__ import annotations

from typing import Any, Optional

from app.local_agent.local_store import LocalProcedureStore
from app.services.skill_ingestion import parse_skill_md


def ingest_local_skill_md(
    store: LocalProcedureStore,
    content: str,
    *,
    fallback_name: str = "unnamed-skill",
    scope_type: str = "repository",
    scope_entity_id: Optional[str] = None,
    provenance: str = "prior_library",
    embedding: Optional[list[float]] = None,
    invariants: Optional[list[dict]] = None,
) -> dict:
    """
    Parse + write, end to end, entirely local (no network, no DB).
    Returns {"status": "captured", "id", "procedure_id"} -- same shape
    ingest_skill_md() returns on capture, minus the "duplicate" branch
    (see module docstring: novelty checking is out of scope here).

    `scope_type` defaults to "repository" (not "global"/"entity" like the
    global ingestor) -- a SKILL.md dropped into ONE local workspace's
    library is, by construction, scoped to that workspace unless the
    caller says otherwise; `scope_entity_id` should be the repo's own
    identifier (e.g. its path or name) when scope_type requires one
    (every scope_type except "global" does -- v0_gate.validate_scope
    enforces this the same way it does for the global writer).

    `applies_when` (parsed, kept as prose by parse_skill_md) is carried
    into the local row's `scope` field as
    {"applies_when_prose": "..."} when present -- NOT fabricated into a
    structured precondition, same "un-normalizable stays as agent-facing
    prose" discipline skill_ingestion.py's own module docstring
    establishes. It participates in nothing structural (no matching logic
    reads it); it is carried forward only so a human or a later real
    parser can see it.
    """
    parsed = parse_skill_md(content, fallback_name=fallback_name)

    steps = [{"order": i, "goal": s} for i, s in enumerate(parsed.steps)]
    scope: dict[str, Any] = {}
    if parsed.applies_when:
        scope["applies_when_prose"] = parsed.applies_when

    result = store.capture_local_procedure(
        name=parsed.name,
        goal=parsed.description,
        steps=steps,
        scope=scope,
        provenance=provenance,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        embedding=embedding,
        invariants=invariants,
    )
    return {"status": "captured", "id": result["id"], "procedure_id": result["procedure_id"]}
