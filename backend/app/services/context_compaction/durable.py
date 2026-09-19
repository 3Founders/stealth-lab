"""
Bridge between compaction and DURABLE Stealth state (run collaboration
records, Claims). Reuses existing abstractions only:

  * blockers / questions / handoffs / notes  -> execution.run_collaboration
    (same "closed-by-reference" rule pipe_format._collab_summary renders)
  * relevant Claims                           -> services.relevant_claims
  * handoff record                            -> run_collaboration.record_run_update

Handoff lifecycle:  agent A context -> durable state already persisted ->
working context compacted -> HANDOFF record (+ optional run.md refresh) ->
agent B calls resume_context() and continues from durable state + the compact
view, never A's full transcript.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.services.context_compaction.engine import compact_context, load_last_valid_view
from app.services.context_compaction.models import CompactionResult, ContextItem, StealthState

MAX_HANDOFF_VIEW_CHARS = 6000


def open_records(collab: list[dict]) -> dict[str, list[dict]]:
    """Open blockers / questions / pending handoffs, derived exactly like
    app.stealth.pipe_format._collab_summary (a record is closed when another
    record's answers_id references it)."""
    closed = {r.get("answers_id") for r in collab if r.get("answers_id")}
    def open_of(kind):
        return [r for r in collab if r["kind"] == kind and str(r["id"]) not in {str(c) for c in closed}]
    return {"blockers": open_of("BLOCKER"), "questions": open_of("QUESTION"), "handoffs": open_of("HANDOFF")}


async def build_stealth_state(
    pool: Any, *, goal: str, run_id: Optional[str] = None, node_id: Optional[str] = None,
    node_instructions: str = "", access_scope: Any = None, claim_top_k: int = 20,
    constraints: list[str] = (), file_hashes: Optional[dict] = None, artifacts: list[str] = (),
    evidence: list[str] = (), implementations: list[str] = (), goal_version: str = "", node_version: str = "",
    list_fn: Optional[Callable[..., Awaitable[list]]] = None, claims_fn: Optional[Callable[..., Awaitable[list]]] = None,
) -> StealthState:
    """Retrieve RELEVANT durable state only (bounded Claims, open records) --
    never the whole database."""
    if list_fn is None:
        from app.execution.run_collaboration import list_run_collaboration as list_fn
    if claims_fn is None:
        from app.services.relevant_claims import get_relevant_claims as claims_fn
    collab = await list_fn(pool, run_id) if run_id else []
    opened = open_records(collab)
    claims = await claims_fn(pool, goal=goal, top_k=claim_top_k, access_scope=access_scope)
    return StealthState(
        goal=goal, goal_version=goal_version, run_id=run_id, node_id=node_id, node_version=node_version,
        node_instructions=node_instructions,
        blockers=[r["body"] for r in opened["blockers"]],
        open_questions=[r["body"] for r in opened["questions"]],
        decisions=[r["body"] for r in collab if r["kind"] == "NOTE" and r["body"].upper().startswith("DECISION")][-10:],
        claims=[{"claim_id": c.get("claim_id") or c.get("id"), "statement": c.get("statement")} for c in claims],
        artifacts=list(artifacts), evidence=list(evidence), implementations=list(implementations),
        constraints=list(constraints), file_hashes=dict(file_hashes or {}),
        handoff=(opened["handoffs"][-1]["body"] if opened["handoffs"] else None),
    )


@dataclass
class HandoffPackage:
    compaction: CompactionResult
    view_text: str
    handoff_record: Optional[dict]
    run_records: list[dict] = field(default_factory=list)


async def prepare_handoff(
    pool: Any, *, execution_run_id: str, actor_agent_id: str, target_agent_id: Optional[str],
    items: list[ContextItem], state: StealthState, judge: Any, session_id: str, note: str,
    record_fn: Optional[Callable[..., Awaitable[dict]]] = None,
    list_fn: Optional[Callable[..., Awaitable[list]]] = None,
    refresh_run_md: Optional[Callable[[], Awaitable[None]]] = None,
) -> HandoffPackage:
    """Compact (trigger=before_handoff) -> record a HANDOFF that carries the
    compact working context -> refresh run.md. If compaction had to be
    skipped, the handoff still records the durable note and says so; nothing
    is dropped."""
    if record_fn is None:
        from app.execution.run_collaboration import record_run_update as record_fn
    if list_fn is None:
        from app.execution.run_collaboration import list_run_collaboration as list_fn
    result = await compact_context(items, state, judge, pool=pool, session_id=session_id,
                                   execution_run_id=execution_run_id, trigger="before_handoff", force=True)
    view_text = result.render()
    if result.status == "compacted":
        body = f"{note}\n\nCOMPACT_CONTEXT (derived; raw trajectory retained):\n{view_text[:MAX_HANDOFF_VIEW_CHARS]}"
    else:
        body = (f"{note}\n\nCOMPACT_CONTEXT unavailable ({result.reason}); context retained uncompacted, "
                f"retry queued (job {result.retry_job_id}). Rely on durable run state.")
    record = await record_fn(pool, execution_run_id=execution_run_id, kind="HANDOFF", body=body,
                             actor_agent_id=actor_agent_id, target_agent_id=target_agent_id)
    if refresh_run_md is not None:
        await refresh_run_md()
    return HandoffPackage(result, view_text, record, await list_fn(pool, execution_run_id))


async def resume_context(
    pool: Any, *, execution_run_id: str, session_id: str,
    list_fn: Optional[Callable[..., Awaitable[list]]] = None,
) -> dict:
    """What agent B starts from: durable run state + the last valid compact
    view. No transcript of agent A is needed."""
    if list_fn is None:
        from app.execution.run_collaboration import list_run_collaboration as list_fn
    collab = await list_fn(pool, execution_run_id)
    opened = open_records(collab)
    view = await load_last_valid_view(pool, session_id)
    return {
        "open_blockers": [r["body"] for r in opened["blockers"]],
        "open_questions": [r["body"] for r in opened["questions"]],
        "pending_handoffs": [{"id": str(r["id"]), "from": r.get("actor_agent_id"), "to": r.get("target_agent_id"),
                              "body": r["body"]} for r in opened["handoffs"]],
        "compact_context": "\n".join(e["content"] for e in view["entries"]) if view else "",
    }
