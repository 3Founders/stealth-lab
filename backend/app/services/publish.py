"""
Phase 19 (V1 product spec): personal procedure -> Publish -> global
candidate. This module is the ONLY real integration point between the
local, DB-free personal registry (`app/local_agent/local_store.py`) and
the global, Postgres-backed procedure writer
(`app/services/procedures.py::capture_procedure`). It lives here, under
`services/`, rather than under `local_agent/`, because it is bound to a
real `asyncpg.Pool` -- `local_agent/` is deliberately DB-free (see that
module's own docstring).

SPEC REQUIREMENTS THIS MODULE SATISFIES (V1 spec, Phase 19):
  - explicit user action        -> `published_by` is a required, real
    argument; nothing publishes itself.
  - provenance preserved        -> the original local procedure's own
    ProvenanceSource value is intentionally NOT reused verbatim (see
    PROVENANCE decision below); instead the publish event's real audit
    trail (who/when/from-what-local-row) is recorded structurally in
    `domain_payload`, which IS preserved forever on the row.
  - author/owner preserved      -> `created_by=published_by`, never a
    generic/anonymous constant.
  - private information scrubbed -> every free-text field
    (name/goal/steps) is routed through trace_redaction's real
    leaf-string scrubber before it ever reaches `capture_procedure`.
  - candidate state explicit    -> `capture_procedure` is called with no
    verification_stats override; the row lands `candidate`/`fresh`/
    `active` like every other fresh capture. See VERIFICATION STATS
    decision below -- this is deliberate, not an oversight.
  - independent validation required before strong trust -> satisfied by
    NOT copying the local row's own track record onto the new global
    row (see below).
  - later users contribute new evidence -> the published row is a real
    `procedures` row; `record_execution_outcome` (procedures.py) already
    accepts evidence against any row id, published ones included. No new
    machinery needed here.
  - contributor can see procedure standing -> the published row is
    fetchable through the existing real tools (`get_procedure`, the MCP
    `check_procedure` tool) exactly like any other procedure. No new
    surface is built in this pass (see module scope note below).
  - NOT a reputation economy -> no scoring, ranking, or trust-currency
    machinery is added here. Standing is just "read the row's real
    verification_state/verification_stats", the same as always.

PROVENANCE DECISION: `system_pending_review`, not `prior_library`.
`prior_library` (per ontology.py/v0_gate.py) names procedures pulled in
from some other existing, external library -- that is not what is
happening here. `system_pending_review` names exactly the situation
correctly: a locally-authored procedure that has not yet been
independently reviewed/validated by anyone but its own author, now
entering the shared commons as a candidate pending exactly that review.
This also matches the spec's own phrasing ("candidate state explicit...
review/validation required") more literally than `prior_library` would.

REDACTION PRIMITIVE DECISION: `redact_value`, not `redact_event`.
`redact_event()` (trace_redaction.py) is shaped specifically for a
parsed *trace event* dict with `tool_input`/`tool_output` keys and a
cross-field "sensitive path in tool_input excludes tool_output wholesale"
rule that only makes sense for a tool-call pair. A procedure's
name/goal/steps are not a tool-call event -- forcing them through
`redact_event` would mean inventing a fake `tool_input`/`tool_output`
shell around them just to satisfy a signature that doesn't fit, which
would silently skip whatever fields don't map onto that shell. The real,
correctly-shaped primitive `redact_event` itself is built on --
`redact_value(value, matched_patterns)` -- is exported from the same
module, walks any parsed JSON value (dict/list/str) recursively, and
substitutes the SAME `KNOWN_TOKEN_PATTERNS` in every string leaf it
finds. That is exactly the shape of `name`/`goal`/`steps` (a string, and
a JSON-shaped list of step objects), so this module calls that shared
primitive directly rather than writing a second, weaker scrub.

VERIFICATION STATS DECISION (deliberate, not an oversight): a local
procedure's own `verification_stats` (attempts/successes/distinct
contexts, accumulated entirely within one person's own local workspace)
is real evidence of *local* reliability, but it is not the same claim as
"independently validated by someone else" -- the spec's own words:
"independent validation required before strong trust". Carrying those
numbers over onto the freshly published global row would let a single
author's own repeated local use masquerade as multi-party verification.
So `publish_local_procedure` does not read or forward
`local_procedure["verification_stats"]` at all; the new global row starts
at `capture_procedure`'s own normal zero-evidence candidate/fresh/active
state, exactly like any other fresh capture. The local row's real track
record is still visible -- it just stays where it was earned, on the
local row, not laundered onto the global one.

GAP CLOSED THIS PASS (previously named above, now fixed): `local_store.py`
now carries a durable publish link on `local_procedures`
(`published_procedure_id` / `published_procedure_row_id` / `published_by`
/ `published_at`, added additively via `LocalProcedureStore`'s own
PRAGMA-table_info-then-ALTER migration -- see that module's
`_PUBLISH_LINK_COLUMNS` comment). This module now takes the local store
and row id directly (rather than a bare, already-fetched dict) so it can
both read that link BEFORE publishing (to refuse a silent duplicate) and
write it AFTER a successful global capture (so the local row durably
records what it became).

REPEATED-PUBLISH DECISION: refuse by default. If the local row's
`published_procedure_row_id` is already set, `publish_local_procedure`
raises `AlreadyPublishedError` instead of silently minting a second,
independent global candidate for the same local work -- an uncontrolled
duplicate is worse than a caller having to notice and decide. A caller
who genuinely wants to re-publish (e.g. the local procedure changed
materially since its first publish and the author wants a fresh
independent-review candidate for the new version) must pass
`force=True` explicitly -- same "explicit opt-in, never a silent
default" posture the rest of this module already uses for
`published_by`. A forced re-publish still creates a brand-new global
`procedures` candidate row (zero inherited evidence, same as any first
publish -- see VERIFICATION STATS DECISION above) and overwrites the
local row's publish link to point at that newest global row; it does not
touch or retire the previously-published global row, which is a real,
independent row now living its own life in the commons and out of this
module's authority to alter.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.local_agent.local_store import LocalProcedureNotFound, LocalProcedureStore
from app.services.procedures import capture_procedure
from app.services.trace_redaction import redact_value

PUBLISHED_PROVENANCE = "system_pending_review"


class AlreadyPublishedError(Exception):
    """Raised when `publish_local_procedure` is called against a local
    row that already has a durable publish link and the caller did not
    pass `force=True`. See module docstring's REPEATED-PUBLISH DECISION."""


def _redact_text(value: str) -> str:
    """Redact known-secret-shaped tokens out of one free-text string,
    via trace_redaction's shared leaf-string primitive."""
    matched: list[str] = []
    return redact_value(value, matched)


def _redact_json(value: Any) -> Any:
    """Redact known-secret-shaped tokens out of an arbitrary parsed-JSON
    value (steps is a JSON array of step objects) -- same primitive,
    walked recursively by `redact_value` itself."""
    matched: list[str] = []
    return redact_value(value, matched)


async def publish_local_procedure(
    pool: asyncpg.Pool,
    *,
    local_store: LocalProcedureStore,
    local_row_id: str,
    published_by: str,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
    force: bool = False,
) -> dict:
    """
    Publishes one local procedure -- identified by `local_row_id` against
    the caller's real `local_store` -- into the global `procedures` table
    as a fresh candidate, via the real `capture_procedure()` write path.
    See the module docstring for the provenance, redaction-primitive,
    verification-stats, and repeated-publish decisions this function
    embodies.

    `published_by` is the real, explicit publishing user -- required,
    never defaulted to an anonymous constant, matching the spec's
    "author/owner preserved" requirement. It becomes both `created_by`
    and `owner_id` on the new row.

    Raises `LocalProcedureNotFound` if `local_row_id` doesn't resolve to
    a live local row, and `AlreadyPublishedError` if that row was already
    published and `force` is not True (see REPEATED-PUBLISH DECISION
    above).

    Returns the same `{"id": ..., "procedure_id": ...}` shape
    `capture_procedure()` returns, identifying the new GLOBAL row (not
    the local one) -- the local row's own durable publish link is
    updated as a side effect before this returns.
    """
    if not published_by:
        raise ValueError("publish_local_procedure requires an explicit published_by (no anonymous publish)")

    local_procedure = local_store.get_local_procedure(local_row_id)
    if local_procedure is None:
        raise LocalProcedureNotFound(local_row_id)

    if local_procedure.get("published_procedure_row_id") and not force:
        raise AlreadyPublishedError(
            f"local procedure {local_row_id!r} was already published as global "
            f"procedure {local_procedure['published_procedure_row_id']!r} "
            f"(at {local_procedure.get('published_at')!r}); pass force=True to "
            "publish it again as a new, independent global candidate."
        )

    redacted_name = _redact_text(local_procedure["name"])
    redacted_goal = _redact_text(local_procedure["goal"])
    redacted_steps = _redact_json(local_procedure.get("steps") or [])

    published_at = datetime.now(timezone.utc).isoformat()
    domain_payload = {
        "published_from_local_procedure_id": local_procedure["id"],
        "published_at": published_at,
        "published_by": published_by,
    }

    result = await capture_procedure(
        pool,
        name=redacted_name,
        goal=redacted_goal,
        steps=redacted_steps,
        preconditions=local_procedure.get("preconditions") or [],
        scope=local_procedure.get("scope") or {},
        invariants=local_procedure.get("invariants") or [],
        exclusions=local_procedure.get("exclusions") or [],
        evidence_refs=local_procedure.get("evidence_refs") or [],
        source_episode_ids=local_procedure.get("source_episode_ids") or [],
        provenance=PUBLISHED_PROVENANCE,
        domain_payload=domain_payload,
        created_by=published_by,
        owner_id=published_by,
        visibility="public",
        embedding=local_procedure.get("embedding"),
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        # verification_stats deliberately NOT forwarded -- see module
        # docstring's VERIFICATION STATS DECISION. capture_procedure has
        # no such parameter; the row gets the DB's own zero-evidence
        # default, same as every other fresh capture.
    )

    # Write the durable local-side link only AFTER the global write
    # succeeded (capture_procedure would have raised above on failure) --
    # a local row must never be marked published against a global row
    # that doesn't actually exist.
    local_store.mark_local_procedure_published(
        local_row_id,
        global_procedure_id=result["procedure_id"],
        global_procedure_row_id=result["id"],
        published_by=published_by,
    )

    return result
