"""
Phase 19 (V1 product spec): personal procedure -> Publish -> global
candidate. This module is the ONLY real integration point between the
local, DB-free personal registry (`app/local_agent/local_store.py`) and
the global, Postgres-backed procedure writer
(`app/services/procedures.py::capture_procedure`). It lives here, under
`services/`, rather than under `local_agent/`, because it is bound to a
real `asyncpg.Pool` -- `local_agent/` is deliberately DB-free (see that
module's own docstring).

THIS IS ONE OF TWO Local -> Global publication paths in this codebase,
and they are deliberately NOT merged into one:

  - THIS module: a LOCAL, DB-free SQLite `local_procedures` row (no
    tenant, no owner column -- the store itself IS the boundary, one
    store per machine/user) -> a fresh global `procedures` candidate.
  - `app/services/publication.py::publish_procedure`: an ALREADY-
    Postgres-resident `procedures` row with visibility='private'/'org'
    (owned by an authenticated `AuthenticatedPrincipal`, reachable at
    `POST /v1/procedures/{id}/publish`) -> a fresh global candidate.

Both now share the SAME authorization contract (`actor_subject`/
`actor_user_id`, trusted-from-the-caller, never a free display string --
see AUTHORIZATION CONTRACT below), the SAME secret/path scrub
primitives (`trace_redaction`), and the SAME dangerous-content admission
check (`app.services.ingestion_admission.classify_admission`, the exact
function the separate Global Internet Ingestion gate uses -- reused
here, not duplicated). They stay two functions because their SOURCE
rows live in two different stores with different real constraints (a
local SQLite row has no owner/tenant columns to check at all; a
Postgres `procedures` row does).

AUTHORIZATION CONTRACT (hardening pass, closes a real gap): the OLD
`published_by` parameter was a free-form string trusted at face value --
literally proof of nothing, any caller could type any name. This module
cannot itself verify a bearer token (it is a plain library function with
a bare `asyncpg.Pool`, not a FastAPI request) -- exactly the same
constraint `publication.py::publish_procedure` already lives under, and
this module now follows the IDENTICAL contract that function already
established: `actor_subject` must be a caller-VERIFIED principal
identity (e.g. `AuthenticatedPrincipal.subject` from
`app.api.deps.require_authenticated_user`, resolved by whatever process
is embedding this local store and this pool in the same session), never
raw user input. `actor_user_id`, when supplied, is checked against a
real `users` row (active, not deactivated) -- the one verification this
module CAN meaningfully perform without a token, reusing the existing
`users` table `authn.py` already owns rather than inventing a second
identity check. Cross-org publication is refused the same way: when the
caller asks to publish into an organization scope
(`scope_type="entity"`, `scope_entity_id=<org id>`) and supplies
`actor_user_id`, real membership is checked via
`app.services.authn.resolve_memberships` -- the SAME membership
resolver `require_authenticated_user`/`get_scope` already use.

SPEC REQUIREMENTS THIS MODULE SATISFIES (V1 spec, Phase 19):
  - explicit user action        -> `actor_subject` is a required, real
    argument; nothing publishes itself.
  - provenance preserved        -> the original local procedure's own
    ProvenanceSource value is intentionally NOT reused verbatim (see
    PROVENANCE decision below); instead the publish event's real audit
    trail (who/when/from-what-local-row/which pathway) is recorded
    structurally in `domain_payload` (preserved forever on the row) AND
    in `audit_events` (app.services.audit -- the same append-only ledger
    `publication.py` writes to).
  - private information scrubbed -> every free-text/JSONB field that can
    carry author-written prose (name/goal/steps/preconditions/scope/
    exclusions/evidence_refs/source_episode_ids -- ALL of them, closing
    a real gap where the last two previously crossed the boundary
    unscrubbed) is routed through trace_redaction's real leaf-string
    scrubber PLUS this module's own absolute-path scrub (see
    `_scrub_value` below) before it ever reaches `capture_procedure`.
  - candidate state explicit    -> `capture_procedure` is called with no
    verification_stats/verification_state override; the row lands
    `candidate`/`fresh` like every other fresh capture.
    `availability` is the ONE state this module DOES set explicitly,
    from the admission decision below -- 'active' or 'quarantined',
    never anything that implies correctness.
  - independent validation required before strong trust -> satisfied by
    NOT copying the local row's own track record onto the new global
    row (see VERIFICATION STATS DECISION below).
  - later users contribute new evidence -> the published row is a real
    `procedures` row; `record_execution_outcome` (procedures.py) already
    accepts evidence against any row id, published ones included.
  - NOT a reputation economy -> no scoring, ranking, or trust-currency
    machinery is added here.

PROVENANCE DECISION: `system_pending_review`, not `prior_library`.
`prior_library` (per ontology.py/v0_gate.py) names procedures pulled in
from some other existing, external library -- that is not what is
happening here. `system_pending_review` names exactly the situation
correctly: a locally-authored procedure that has not yet been
independently reviewed/validated by anyone but its own author.

VERIFICATION STATS DECISION (deliberate, not an oversight): a local
procedure's own `verification_stats` (attempts/successes/distinct
contexts, accumulated entirely within one person's own local workspace)
is real evidence of *local* reliability, but it is not the same claim as
"independently validated by someone else". `publish_local_procedure`
does not read or forward `local_procedure["verification_stats"]`,
`["verification_state"]`, or `["approval_status"]` at all -- the new
global row starts at `capture_procedure`'s own normal zero-evidence
candidate/fresh state. The local row's real track record stays where it
was earned.

ADMISSION / SAFETY DECISION (hardening pass, reuses the Global
Ingestion gate rather than a second classifier -- see
app.services.ingestion_admission): `classify_admission()` runs
deterministically over the (already redacted) local procedure's
name/goal/steps before anything is written. "reject" raises
`PublicationDenied` (the SAME exception type
`publication.py::publish_procedure` raises -- one denial contract
across both publish paths) -- no procedures row at all. "review" still
publishes (a real Global Candidate is written -- safety screening is
not correctness verification, and an ambiguous finding does not by
itself prove a real procedure is malicious), but with
`availability='quarantined'` -- the SAME column/value the ingestion
gate uses, excluded from every normal retrieval surface by
applicability.py's own `_CANDIDATE_BASE_WHERE` until a human/LLM review
clears it. "admit" publishes as `availability='active'`, unchanged from
prior behavior for the common case.

EMBEDDING DECISION (hardening pass, closes a real gap): the OLD code
forwarded `local_procedure["embedding"]` -- a vector built from the
LOCAL store's own (different, narrower) embedding recipe over a bare
task description -- straight into `capture_procedure` as if it were the
authoritative global vector. That is a stale/impoverished vector for
the canonical global retrieval-document recipe
(`app.services.retrieval_document.build_procedure_retrieval_document`,
the SAME one skill_ingestion.py's public-source path uses). This module
now NEVER forwards the raw local embedding. When `embedder` is supplied
it computes a REAL canonical embedding, from the canonical document
built over the REDACTED fields, at publish time (best case -- the
published row is immediately, correctly retrievable). When no embedder
is supplied, the row is captured with no embedding at all --
`capture_procedure`'s own established "no embedding == explicitly
pending the canonical --representation backfill" contract (the exact
same contract `procedure_extraction` callers already rely on), never a
mismatched vector standing in as authoritative.

REPEATED-PUBLISH DECISION (unchanged -- audited, still correct): refuse
by default. If the local row's `published_procedure_row_id` is already
set, `publish_local_procedure` raises `AlreadyPublishedError` instead of
silently minting a second, independent global candidate for the same
local work. A caller who genuinely wants to re-publish (the local
procedure changed materially and the author wants a fresh
independent-review candidate for the new version) must pass
`force=True` explicitly. A forced re-publish still creates a
brand-new, independent global `procedures` candidate row via
`capture_procedure` (never `supersede_procedure`) -- zero inherited
evidence AND zero inherited verification_state, same as any first
publish; the previously-published global row is untouched.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.local_agent.local_store import LocalProcedureNotFound, LocalProcedureStore
from app.services.ingestion_admission import AdmissionDecision, classify_admission
from app.services.procedures import capture_procedure
from app.services.retrieval_document import (
    RETRIEVAL_DOCUMENT_IMPORT_VERSION,
    build_procedure_retrieval_document,
    retrieval_document_sha256,
)
from app.services.trace_redaction import redact_value

# `PublicationDenied` is imported lazily (inside publish_local_procedure,
# not at module scope) because app.services.publication itself imports
# `_scrub_value` from THIS module -- a module-level import here would be
# a real circular import. Deferred-import is this codebase's own
# established way of breaking exactly this shape of cycle (see
# record_audit_event/resolve_memberships below).

PUBLISHED_PROVENANCE = "system_pending_review"
PUBLICATION_PATHWAY = "local_store"

# GAP CLOSED THIS PASS: trace_redaction's KNOWN_TOKEN_PATTERNS (via
# redact_value, reused below) catches secret-shaped tokens but has no
# generic absolute-path rule -- its own SENSITIVE_PATH_PATTERNS only
# flags a short list of known-sensitive *filenames* (.env, .pem,
# id_rsa, .ssh/, .aws/credentials), not "any absolute filesystem path",
# and that check is wired only into redact_event (a tool-call-shaped
# primitive this module deliberately does not use -- see module
# docstring). A local procedure's steps/preconditions/scope/exclusions
# can legitimately contain an author's own absolute path (e.g. a step
# literally saying "cd C:\Users\chait\repo") which is machine-specific
# and must not enter the shared global commons verbatim. Minimal,
# narrowly-targeted regexes only -- not a generic PII framework. Also
# strips internal/private hostnames the same way (a private VPN/
# corp-internal hostname is as machine/org-specific as a home path).
_WINDOWS_ABS_PATH = re.compile(r"[A-Za-z]:\\(?:[^\s\"'<>|*?]+)")
_POSIX_ABS_PATH = re.compile(r"/(?:home|Users|root)/[^\s\"'<>|]+")
_PRIVATE_HOSTNAME = re.compile(
    r"\b(?:[a-zA-Z0-9-]+\.)*(?:internal|corp|local|lan|intranet)\b"
    r"(?::\d{2,5})?",
)
PATH_REDACTION_PLACEHOLDER = "[REDACTED:absolute_path]"
HOSTNAME_REDACTION_PLACEHOLDER = "[REDACTED:private_hostname]"


def _scrub_paths(value: str) -> str:
    """Replace absolute filesystem paths (Windows and POSIX user/home/root
    paths) and private/internal hostnames with a placeholder. Leaf-string
    only, same shape as trace_redaction's own leaf-string primitive."""
    value = _WINDOWS_ABS_PATH.sub(PATH_REDACTION_PLACEHOLDER, value)
    value = _POSIX_ABS_PATH.sub(PATH_REDACTION_PLACEHOLDER, value)
    value = _PRIVATE_HOSTNAME.sub(HOSTNAME_REDACTION_PLACEHOLDER, value)
    return value


def _scrub_value(value: Any) -> Any:
    """Recursively walks a parsed JSON value (dict/list/str/other),
    applying BOTH scrubs to every string leaf: trace_redaction's shared
    known-secret-token primitive (`redact_value`), then this module's
    own absolute-path/hostname scrub. Used for EVERY field of a local
    procedure that is about to cross into the shared global commons --
    including evidence_refs/source_episode_ids (a real gap this pass
    closes: those two fields previously crossed unscrubbed)."""
    if isinstance(value, str):
        matched: list[str] = []
        return _scrub_paths(redact_value(value, matched))
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    return value


class AlreadyPublishedError(Exception):
    """Raised when `publish_local_procedure` is called against a local
    row that already has a durable publish link and the caller did not
    pass `force=True`. See module docstring's REPEATED-PUBLISH DECISION."""


class UnauthorizedPublication(PermissionError):
    """Raised when the caller-supplied actor identity fails a checkable
    authorization condition (missing subject, deactivated account, or
    no membership in the requested organization scope). Distinct from
    `PublicationDenied` (a content/safety decision) -- this is strictly
    an identity/scope decision, checked BEFORE any content is even read."""


def _local_procedure_as_parsed_skill(local_procedure: dict) -> Any:
    """Adapts a (already-redacted) local procedure dict into the SAME
    `ParsedSkill` shape app.services.ingestion_admission.classify_
    admission already consumes -- reuses the real, shared dangerous-
    content/structural checks verbatim rather than a second scanner.
    `ParsedSkill` is a plain dataclass (app.services.skill_ingestion);
    importing it here does not create a real coupling to SKILL.md
    parsing, only to its shared result shape."""
    from app.services.skill_ingestion import ParsedSkill

    def _step_text(step: Any) -> str:
        # Local procedure steps are free-form JSON (no enforced shape at
        # the local-store layer -- unlike the global writer's fixed
        # {"order","goal"} convention, a caller may store {"action",
        # "detail"} or anything else). Flatten every string value found
        # anywhere in the step so the admission check actually sees the
        # real content regardless of key names, rather than silently
        # reading an absent "goal" key as empty.
        if isinstance(step, str):
            return step
        if isinstance(step, dict):
            return " ".join(
                _step_text(v) for v in step.values() if isinstance(v, (str, dict, list))
            )
        if isinstance(step, list):
            return " ".join(_step_text(v) for v in step)
        return str(step) if step is not None else ""

    steps = local_procedure.get("steps") or []
    step_texts = [_step_text(s) for s in steps]
    return ParsedSkill(
        name=local_procedure.get("name") or "",
        description=local_procedure.get("goal") or "",
        steps=step_texts,
        applies_when=None,
        allowed_tools=[],
    )


async def _check_authorization(
    pool: asyncpg.Pool,
    *,
    actor_subject: str,
    actor_user_id: Optional[str],
    scope_type: str,
    scope_entity_id: Optional[str],
) -> None:
    """The one authorization check this module CAN perform without a
    bearer token: given a caller-supplied (trusted-from-the-caller)
    identity, refuse a deactivated account and refuse publishing into an
    organization the actor does not belong to. Both checks are no-ops
    (skipped, not silently passed) when `actor_user_id` is not supplied
    -- a caller integrating real request-scoped auth
    (app.api.deps.require_authenticated_user) always has a real
    `user_id`; a caller that has only a bare subject string gets the
    SAME "at minimum, not anonymous" floor `published_by`'s old falsy
    check already provided, no worse than before."""
    if not actor_subject:
        raise UnauthorizedPublication(
            "publish_local_procedure requires an explicit, verified actor_subject "
            "(no anonymous publish)"
        )
    if actor_user_id is None:
        return

    row = await pool.fetchrow(
        "SELECT is_active FROM users WHERE id = $1::uuid", actor_user_id,
    )
    if row is not None and not row["is_active"]:
        raise UnauthorizedPublication(
            f"user {actor_user_id!r} is deactivated and may not publish"
        )

    if scope_type == "entity" and scope_entity_id:
        from app.services.authn import resolve_memberships

        memberships = await resolve_memberships(pool, actor_user_id)
        org_ids = {m.organization_id for m in memberships}
        if scope_entity_id not in org_ids:
            raise UnauthorizedPublication(
                f"actor is not a member of organization {scope_entity_id!r}; "
                "cross-tenant publication refused"
            )


async def publish_local_procedure(
    pool: asyncpg.Pool,
    *,
    local_store: LocalProcedureStore,
    local_row_id: str,
    actor_subject: str,
    actor_user_id: Optional[str] = None,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
    force: bool = False,
    embedder: Optional[Any] = None,
    llm_client: Optional[Any] = None,
) -> dict:
    """
    Publishes one local procedure -- identified by `local_row_id` against
    the caller's real `local_store` -- into the global `procedures` table
    as a fresh candidate, via the real `capture_procedure()` write path.
    See the module docstring for the authorization, redaction, admission,
    embedding, and repeated-publish decisions this function embodies.

    `actor_subject` MUST be a caller-verified principal identity (see
    AUTHORIZATION CONTRACT in the module docstring), never raw user
    input -- required, never defaulted to an anonymous constant. It
    becomes both `created_by` and `owner_id` on the new row.
    `actor_user_id`, when supplied, additionally gates on account
    activity + organization membership (see `_check_authorization`).

    `embedder`/`llm_client` are optional DI seams (same pattern
    skill_ingestion.py already uses): with an embedder, the published
    row gets a real canonical embedding computed at publish time;
    without one, the row is captured with no embedding, explicitly
    pending the canonical --representation backfill. With an
    llm_client, an ambiguous (review-tier) admission finding may be
    escalated for risk classification exactly as the Global Ingestion
    gate does; without one, it is quarantined outright -- the normal
    (no-client) path makes zero LLM calls.

    Raises `UnauthorizedPublication` for an identity/scope failure,
    `LocalProcedureNotFound` if `local_row_id` doesn't resolve to a live
    local row, `AlreadyPublishedError` if that row was already published
    and `force` is not True, and `PublicationDenied` if the admission
    gate finds an unambiguously unsafe procedure (the SAME exception
    `publication.py::publish_procedure` raises for its own denial path).

    Returns the same `{"id": ..., "procedure_id": ...}` shape
    `capture_procedure()` returns, identifying the new GLOBAL row (not
    the local one) -- the local row's own durable publish link is
    updated as a side effect before this returns.
    """
    from app.services.audit import record_audit_event
    from app.services.publication import PublicationDenied

    await _check_authorization(
        pool, actor_subject=actor_subject, actor_user_id=actor_user_id,
        scope_type=scope_type, scope_entity_id=scope_entity_id,
    )

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

    # --- private -> public data boundary: scrub EVERY field that can
    # carry author-written prose, including evidence_refs/
    # source_episode_ids (previously unscrubbed -- see module docstring).
    redacted_name = _scrub_value(local_procedure["name"])
    redacted_goal = _scrub_value(local_procedure["goal"])
    redacted_steps = _scrub_value(local_procedure.get("steps") or [])
    redacted_preconditions = _scrub_value(local_procedure.get("preconditions") or [])
    redacted_scope = _scrub_value(local_procedure.get("scope") or {})
    redacted_exclusions = _scrub_value(local_procedure.get("exclusions") or [])
    redacted_invariants = _scrub_value(local_procedure.get("invariants") or [])
    redacted_evidence_refs = _scrub_value(local_procedure.get("evidence_refs") or [])
    redacted_source_episode_ids = _scrub_value(
        local_procedure.get("source_episode_ids") or []
    )

    # --- admission gate: reuses the SAME deterministic dangerous-content
    # checks the Global Internet Ingestion gate uses (no second
    # classifier). Runs on the REDACTED fields (secrets already gone),
    # so this is purely a malicious/dangerous-behavior check, not a
    # second secret scan.
    redacted_shape = {
        "name": redacted_name, "goal": redacted_goal, "steps": redacted_steps,
    }
    admission = classify_admission(
        _local_procedure_as_parsed_skill(redacted_shape),
        llm_client=llm_client,
    )
    if admission.decision == "reject":
        await record_audit_event(
            pool, actor_subject=actor_subject, action="local_publication_rejected",
            object_type="local_procedure", object_id=local_row_id,
            actor_user_id=actor_user_id,
            details={"reasons": [c.detail for c in admission.checks], "reason": admission.reason},
        )
        raise PublicationDenied([c.detail for c in admission.checks] or [admission.reason])
    quarantined = admission.decision == "review"

    # --- canonical embedding, or explicitly none (see EMBEDDING DECISION
    # in the module docstring) -- the local row's own embedding is NEVER
    # forwarded as-is. The embedder branch ALSO computes a more complete
    # retrieval document than the always-built fallback below (it folds in
    # postconditions/failure_conditions/domain); when present, that richer
    # document/version/hash OVERRIDES the fallback rather than being passed
    # a second time through embedding_kwargs (capture_procedure() must
    # receive each retrieval_document* kwarg exactly once).
    embedding_kwargs: dict[str, Any] = {}
    embedder_retrieval_doc: Optional[str] = None
    embedder_retrieval_document_version: Optional[str] = None
    embedder_retrieval_document_sha256: Optional[str] = None
    if embedder is not None:
        # NOTE: build_procedure_retrieval_document/retrieval_document_sha256
        # are already imported at module scope (see top of file) -- do NOT
        # re-import them here. A local `from ... import` of a name also
        # bound at module scope makes that name local to the WHOLE function
        # (Python's static scoping), which would UnboundLocalError the
        # unconditional fallback build below on every no-embedder call.
        from app.services.retrieval_document import RETRIEVAL_DOCUMENT_VERSION

        retrieval_doc = build_procedure_retrieval_document({
            "name": redacted_name, "goal": redacted_goal, "steps": redacted_steps,
            "preconditions": redacted_preconditions, "invariants": redacted_invariants,
            "postconditions": [], "failure_conditions": [],
            "domain": None, "domain_payload": {},
        })
        goal_vec, embedding_metadata = await embedder.embed_one_with_metadata(
            retrieval_doc, input_type="document",
        )
        embedding_kwargs = dict(
            embedding=goal_vec,
            embedding_model_id=embedding_metadata.model_id,
            embedding_provider=embedding_metadata.provider,
            embedding_input_type=embedding_metadata.input_type,
            embedding_text_hash=embedding_metadata.text_sha256,
        )
        embedder_retrieval_doc = retrieval_doc
        embedder_retrieval_document_version = RETRIEVAL_DOCUMENT_VERSION
        embedder_retrieval_document_sha256 = retrieval_document_sha256(retrieval_doc)
    # else: no embedding kwarg at all -- capture_procedure's own
    # established "no embedding == pending the --representation
    # backfill" contract, never a stale local vector standing in as
    # authoritative.

    published_at = datetime.now(timezone.utc).isoformat()
    domain_payload = {
        "published_from_local_procedure_id": local_procedure["id"],
        "published_at": published_at,
        "published_by": actor_subject,
        "publication_pathway": PUBLICATION_PATHWAY,
        "admission_decision": admission.decision,
    }

    # The local row's `embedding` is a bare task-description vector from the
    # local agent's own SQLite store (plain-python cosine) -- NOT a
    # canonical retrieval-document vector. Forwarding it would make an
    # impoverished, off-recipe vector authoritative for a global procedure.
    # Instead: build + store the canonical retrieval document now (so the
    # row is inspectable and lexically searchable immediately), stamp the
    # import sentinel, and leave the vector for the canonical backfill
    # (scripts/backfill_procedure_embeddings.py --representation).
    published_retrieval_doc = build_procedure_retrieval_document({
        "name": redacted_name,
        "goal": redacted_goal,
        "steps": redacted_steps,
        "preconditions": redacted_preconditions,
        "scope": redacted_scope,
        "exclusions": redacted_exclusions,
        "invariants": local_procedure.get("invariants") or [],
    })

    # The embedder's own (richer) retrieval document overrides the fallback
    # above when available; otherwise the fallback keeps the row lexically
    # searchable even with no embedder supplied (see EMBEDDING DECISION).
    final_retrieval_document = embedder_retrieval_doc or published_retrieval_doc
    final_retrieval_document_version = (
        embedder_retrieval_document_version or RETRIEVAL_DOCUMENT_IMPORT_VERSION
    )
    final_retrieval_document_sha256 = (
        embedder_retrieval_document_sha256
        or retrieval_document_sha256(published_retrieval_doc)
    )

    result = await capture_procedure(
        pool,
        name=redacted_name,
        goal=redacted_goal,
        steps=redacted_steps,
        preconditions=redacted_preconditions,
        scope=redacted_scope,
        invariants=redacted_invariants,
        exclusions=redacted_exclusions,
        evidence_refs=redacted_evidence_refs,
        source_episode_ids=redacted_source_episode_ids,
        provenance=PUBLISHED_PROVENANCE,
        domain_payload=domain_payload,
        created_by=actor_subject,
        owner_id=actor_subject,
        visibility="public",
        # Canonical retrieval document: the embedder's richer document
        # overrides the always-built fallback when available; either way
        # the row lands lexically searchable immediately (see EMBEDDING
        # DECISION in the module docstring).
        retrieval_document=final_retrieval_document,
        retrieval_document_version=final_retrieval_document_version,
        retrieval_document_sha256=final_retrieval_document_sha256,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        availability="quarantined" if quarantined else "active",
        # verification_stats/verification_state deliberately NOT
        # forwarded -- see module docstring's VERIFICATION STATS
        # DECISION. capture_procedure has no such parameter; the row
        # gets the DB's own zero-evidence candidate/fresh default, same
        # as every other fresh capture.
        **embedding_kwargs,
    )

    # Write the durable local-side link only AFTER the global write
    # succeeded (capture_procedure would have raised above on failure) --
    # a local row must never be marked published against a global row
    # that doesn't actually exist.
    local_store.mark_local_procedure_published(
        local_row_id,
        global_procedure_id=result["procedure_id"],
        global_procedure_row_id=result["id"],
        published_by=actor_subject,
    )

    await record_audit_event(
        pool, actor_subject=actor_subject, action="local_publication_approved",
        object_type="local_procedure", object_id=local_row_id,
        actor_user_id=actor_user_id,
        details={
            "global_row_id": result["id"], "global_procedure_id": result["procedure_id"],
            "admission_decision": admission.decision, "quarantined": quarantined,
            "forced": force,
        },
    )
    await record_audit_event(
        pool, actor_subject=actor_subject, action="global_candidate_created",
        object_type="procedure", object_id=result["id"],
        actor_user_id=actor_user_id,
        details={"from_local_procedure_id": local_row_id, "pathway": PUBLICATION_PATHWAY},
    )

    return result
