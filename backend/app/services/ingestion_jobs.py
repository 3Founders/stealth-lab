"""
The job consumer half of the ingestion pipeline (ticket 16 completes
here). trace_worker.py's process_collector_file() already writes real
ingestion_jobs rows of type 'normalize_trace_event' for every real
(non-duplicate) trace_events insert -- that part was built. Nothing ever
read them: process_pending_jobs() below is the missing consumer, and
handle_normalize_trace_event() is the one handler currently registered.

Closes the second half of the gap independently confirmed by reading
source rather than assumed: extract_deterministic_observations()
(observations.py) and persist_observation() are both real, tested, pure/
near-pure functions with ZERO non-test callers before this module. This
file gives them a caller; it does not change their behavior.

SKIP LOCKED, not a status='processing' pre-scan: the standard Postgres
job-queue idiom, safe for the future multi-worker deployment
ingestion_jobs' own comment already anticipates ("SKIP LOCKED makes a
future multi-worker deployment safe without redesign, even though
milestone 1 runs exactly one in-process worker" -- 12_trace_ingestion_
pipeline.sql). One job = one transaction, so a crash mid-job leaves it
'processing' rather than lost -- see requeue_stuck_jobs() for the
recovery path, which is deliberately manual/explicit rather than a
silent timeout-based requeue (a job stuck because of a genuine bug
should not retry forever unattended).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Optional

from app import telemetry as _tel
import asyncpg

from app.ingestion.config import WorkerConfig
from app.services.access import AccessScope, TenantScope, tenant_predicate
from app.services.claim_evidence import record_claim_evidence
from app.services.goal_abstraction import (
    GoalRelationCycleError,
    GoalRelationDependencyError,
    GoalRelationRedundancyError,
    GoalRelationScopeError,
    GoalRelationSelfError,
    GoalRelationStatusConflict,
    GoalRelationVisibilityError,
    RELATION_AUTHORITY,
    RELATION_POLICY,
    RELATION_POLICY_VERSION,
    adjudicate_goal_relation,
    expand_goal_neighbors,
    persist_goal_relation,
)
from app.services.identity_resolution import (
    Candidate,
    GOAL_ABSTRACTION_AUDIT_JOB,
    GOAL_ABSTRACTION_AUDIT_REASONS,
    GOAL_ABSTRACTION_PLACEMENT_JOB,
    SAME_MIN_CONFIDENCE,
    _RELATION_TO_DECISION,
    canonical_identity_text,
    default_judge,
    enqueue_goal_abstraction_audit,
    generate_goal_candidates,
    goal_abstraction_placement_key,
    load_goal_identity_decision,
    record_decision,
    validate_identity_job_id,
)
from app.services.observations import (
    extract_deterministic_observations,
    extract_deterministic_observations_from_run_event,
    persist_observation,
    promote_observation_to_claim,
)
from app.services.semantic.errors import SemanticJudgmentUnavailable
from app.services.shards import ShardUnavailable, home_pool, pools_for

# --------------------------------------------------------------------------
# IngestionContext for the TRACE / execution-derived path (Gate G1, §A1).
#
# The document path (skill_ingestion.compile_skill_artifact) already opens
# one. The trace path did not, so every observation / claim / procedure it
# produced carried a NULL ingestion_context_id -- unanswerable "who / under
# what scope / by which extractor produced this". The unit is the SESSION:
# a trace's events arrive as many jobs, and episodes group by session, so
# one context spans a whole session's ingestion. It is resolved from the
# DB (not an in-process cache) so it survives worker restarts and stays
# idempotent -- never a fabricated duplicate.
# --------------------------------------------------------------------------
TRACE_INGESTION_EXTRACTOR = "trace_ingestion"
TRACE_INGESTION_EXTRACTOR_VERSION = "deterministic_v1"


async def resolve_trace_ingestion_context(
    pool: asyncpg.Pool,
    session_id: str,
    *,
    owner_id: Optional[str] = None,
    visibility: str = "public",
) -> Optional[str]:
    """The open IngestionContext for one trace session, opening one on first
    call. Returns None only if `session_id` is falsy. Idempotent: a second
    call for the same session returns the same row (looked up by
    source_uri), so concurrent workers converge instead of duplicating."""
    if not session_id:
        return None
    from app.services.ingestion_context import open_ingestion_context

    source_uri = f"session:{session_id}"
    existing = await pool.fetchval(
        "SELECT id FROM ingestion_contexts "
        "WHERE source_uri = $1 AND status = 'open' "
        "ORDER BY started_at DESC LIMIT 1",
        source_uri,
    )
    if existing is not None:
        return str(existing)
    return await open_ingestion_context(
        pool,
        source_type="trace",
        source_uri=source_uri,
        source_hash=None,
        extractor_id=TRACE_INGESTION_EXTRACTOR,
        extractor_version=TRACE_INGESTION_EXTRACTOR_VERSION,
        actor_id=owner_id or TRACE_INGESTION_EXTRACTOR,
        scope_type="session",
        scope_entity_id=str(session_id),
        classification="EXECUTION_DERIVED",
        visibility=visibility,
        owner_id=owner_id,
    )


class _RotatingCompletions:
    """Same shape as `openai.OpenAI().chat.completions` (`.create(**kwargs)`),
    but tries each configured key in turn before giving up -- the exact
    rotation rule `OpenAICompatProvider._complete` already uses for the
    semantic-judge chain (app/services/semantic/providers.py), reused here
    rather than reinvented. A bad/exhausted key rotates to the next one; a
    genuine transient failure (timeout, 5xx -- not quota/auth) raises
    immediately rather than burning every remaining key on an outage."""

    def __init__(self, clients: list):
        self._clients = clients

    def create(self, **kwargs):
        from app.services.semantic.errors import ErrorKind, classify_exception

        last: Optional[BaseException] = None
        for client in self._clients:
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 -- classified below, not swallowed
                last = exc
                rotatable = (
                    classify_exception(exc) is ErrorKind.PERMANENT
                    or getattr(exc, "status_code", None) == 429
                    or type(exc).__name__ == "RateLimitError"
                )
                if len(self._clients) > 1 and rotatable:
                    log.warning("general_compute: key failed (%s), rotating to next", type(exc).__name__)
                    continue
                raise
        assert last is not None
        raise last


class _RotatingOpenAIClient:
    """Drop-in for a single `openai.OpenAI` client -- every caller in this
    module (extraction, admission, goal dedup, claim classification) only
    ever touches `.chat.completions.create(...)`, so this is the entire
    surface that needs to fan out across keys."""

    def __init__(self, clients: list):
        self.chat = _SimpleNamespace(completions=_RotatingCompletions(clients))


class _SimpleNamespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _VertexOAuthCompletions:
    """Forwards to a real OpenAI client against Vertex AI's OpenAI-compatible
    endpoint, but always substitutes OUR configured model name -- the
    rotation list passes whatever `model=` the caller asked for (e.g.
    GENERAL_COMPUTE_JUDGE_MODEL's "gemini-3.8-flash"), which isn't
    published on Vertex's catalog for this project (confirmed live,
    2026-09-22: only "google/gemini-2.5-flash" is). Silently swapping the
    model here, rather than requiring every caller to know Vertex's naming,
    keeps this a drop-in rotation member."""

    def __init__(self, client: Any, model: str):
        self._client = client
        self._model = model

    def create(self, **kwargs):
        kwargs = dict(kwargs)
        kwargs["model"] = self._model
        return self._client.chat.completions.create(**kwargs)


class _VertexOAuthClient:
    def __init__(self, client: Any, model: str):
        self.chat = _SimpleNamespace(completions=_VertexOAuthCompletions(client, model))


def _vertex_oauth_client() -> Optional[Any]:
    """Vertex AI via OAuth2/ADC -- no API key, no per-key quota bucket.
    The Cloud Run job's own attached service account already has
    roles/editor (includes aiplatform.endpoints.predict), confirmed
    2026-09-22, no IAM grant needed. Real IAM-based project quota instead
    of a free-tier API-key cap shared across every key on the same GCP
    project -- the actual root cause of the sustained 429 storms the
    key-rotation tier kept hitting (verified live: every GENERAL_COMPUTE/
    GEMINI key failed simultaneously, because they all draw from one
    project-level bucket).

    Returns None (never fails the job) when VERTEX_PROJECT isn't set, or
    when google.auth.default() finds no ADC -- e.g. plain local dev
    without `gcloud auth application-default login`. Cloud Run itself
    always has ADC via the metadata server; this is a local-only gap."""
    from app.config import settings

    if not settings.vertex_project:
        return None
    try:
        import google.auth
        import google.auth.transport.requests
        from openai import OpenAI

        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(google.auth.transport.requests.Request())
    except Exception:  # noqa: BLE001 -- ADC unavailable/misconfigured is not a job failure
        log.warning("vertex: ADC unavailable, skipping the Vertex AI OAuth2 tier", exc_info=True)
        return None
    base_url = (
        f"https://{settings.vertex_region}-aiplatform.googleapis.com/v1/"
        f"projects/{settings.vertex_project}/locations/{settings.vertex_region}/endpoints/openapi"
    )
    client = OpenAI(api_key=credentials.token, base_url=base_url)
    return _VertexOAuthClient(client, settings.vertex_model)


def _general_compute_client() -> Optional[Any]:
    """One shared, general-purpose chat-completions client for the
    capability-abstraction / admission-escalation model calls this worker
    makes -- General Compute (`app.debate.panel.OpenAICompatAgent`'s same
    OpenAI-compatible construction) is this codebase's existing named
    tier for "some general-purpose hosted model," reused here rather than
    inventing a second provider concept. Returns None (never fails the
    ingestion job) if nothing at all is configured -- compile_skill_
    artifact's own client=None path already handles that by honestly
    abstaining from capability-statement generation, exactly as it did
    before this function existed.

    Rotation order: Vertex AI (OAuth2/ADC, real IAM quota) first when
    configured, then GENERAL_COMPUTE_API_KEY plus GENERAL_COMPUTE_API_KEYS
    (comma-separated, falling back to GEMINI_API_KEYS when unset -- same
    convention every deployment already uses, since General Compute's
    base URL points at Gemini's own endpoint). With a single effective
    client this returns it directly, identical to before Vertex/rotation
    existed; rotation only engages with >1."""
    from app.config import settings

    clients: list[Any] = []
    vertex = _vertex_oauth_client()
    if vertex is not None:
        clients.append(vertex)

    if settings.general_compute_api_key and settings.general_compute_judge_model:
        from openai import OpenAI

        keys = [settings.general_compute_api_key]
        extra_keys_csv = settings.general_compute_api_keys or settings.gemini_api_keys or ""
        for k in extra_keys_csv.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        clients.extend(OpenAI(api_key=k, base_url=settings.general_compute_base_url) for k in keys)

    if not clients:
        return None
    if len(clients) == 1:
        return clients[0]
    return _RotatingOpenAIClient(clients)


def _trusted_identity_job_id(payload: dict) -> Optional[int]:
    from app.services.identity_resolution import validate_identity_job_id

    job = payload.get("_job") if isinstance(payload, dict) else None
    if job is None:
        return None
    if not isinstance(job, dict):
        raise ValueError("ingestion payload _job context must be an object")
    return validate_identity_job_id(job.get("id"))


async def handle_ingest_skill_package(pool: asyncpg.Pool, payload: dict) -> None:
    """Ingest exactly one immutable skill package, retryably and idempotently."""
    from app.config import settings
    from app.services.embeddings import Embedder
    from app.services.ingestion_sources import GitHubSkillCorpusSource
    from app.services.ingestion_sources.base import SourceRef
    from app.services.ingestion_sources.manifest import CorpusSourceSpec
    from app.services.skill_ingestion import compile_skill_artifact

    identity_job_id = _trusted_identity_job_id(payload)
    commit = str(payload["commit"])
    path = str(payload["path"])

    spec = CorpusSourceSpec(
        id=str(payload["source_id"]),
        priority=int(payload.get("priority", 1)),
        type=payload.get("source_type", "github"),
        repo=str(payload["repo"]),
        path=payload.get("subtree"),
        expected_format=payload.get("expected_format", "skill_repository"),

        # IMPORTANT:
        # Reconstruct the worker adapter from the immutable commit
        # discovered and stored in the queued job, not from HEAD/main.
        ref=commit,
    )

    adapter = GitHubSkillCorpusSource(spec)
    uri = str(payload.get("uri") or f"https://github.com/{adapter.slug}/blob/{commit}/{path}")
    with _tel.span("ingestion.fetch", kind="TOOL", on_error=_tel.FailureCode.INGESTION_ERROR,
                   source_type=spec.type):
        artifact = adapter.fetch(SourceRef(
            uri=uri, repository=adapter.slug, path=path, commit=commit,
            source_id=spec.id,
        ))
    client = _general_compute_client()
    if client is None:
        # Founder directive (2026-09-15): skill/document ingestion is now
        # LLM-only -- parse_skill_md's deterministic fallback is gone from
        # this call path. A missing client here means every artifact this
        # job processes is refused (compile_skill_artifact's own
        # status="rejected"), not silently degraded -- logged loudly since
        # that is a real capacity/config problem for a production worker,
        # not an expected steady state.
        log.warning(
            "handle_ingest_skill_package: no LLM client configured "
            "(GENERAL_COMPUTE_API_KEY/GENERAL_COMPUTE_JUDGE_MODEL) -- "
            "this artifact will be refused, not deterministically captured",
        )
    # normalize -> dedup -> semantic extraction -> canonical persistence ->
    # embedding all happen inside compile_skill_artifact; the embedding call
    # has its own span (Embedder), the rest is one coarse `ingestion.compile`.
    with _tel.span("ingestion.compile", kind="CHAIN", on_error=_tel.FailureCode.INGESTION_ERROR,
                   items_attempted=1, source_type=spec.type) as sp:
        outcome = await compile_skill_artifact(
            pool, artifact, embedder=Embedder(rate_limit_pool=pool),
            created_by="structured_skill_ingestion_worker",
            client=client,
            admission_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
            extraction_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
            fallback_extraction_llm_model=settings.general_compute_fallback_model or None,
            claim_extraction_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
            identity_job_id=identity_job_id,
        )
        status = getattr(outcome, "status", None)
        _tel.set_attrs(
            sp, ingest_status=status,
            items_accepted=int(status in ("captured", "new_version")),
            items_duplicate=int(status in ("duplicate", "unchanged")),
            items_rejected=int(status == "rejected"),
            items_failed=int(status == "error"),
            procedure_id=getattr(outcome, "procedure_id", None),
            extraction_model=getattr(outcome, "extraction_model", None))
        if status == "error":
            _tel.fail(sp, _tel.FailureCode.INGESTION_ERROR)
        elif status == "rejected":
            _tel.fail(sp, _tel.FailureCode.INGESTION_PARSE_ERROR, force_keep=False)
        elif status in ("duplicate", "unchanged"):
            _tel.set_attrs(sp, failure_code=_tel.FailureCode.DUPLICATE_OBJECT)


async def handle_ingest_document(pool: asyncpg.Pool, payload: dict) -> None:
    """Ingest exactly one document (HTML/PDF/DOCX/Markdown/API -- any format a
    registered DocumentSourceAdapter recognizes) through the FULL semantic-
    extraction pipeline, retryably and idempotently. Mirrors
    handle_ingest_skill_package's shape exactly on purpose.

    Reuses compile_skill_artifact directly rather than building a second
    extraction path: the only source_type-gated branch anywhere in its call
    graph is _persist_script_procedures' bundled-script capture, which already
    no-ops for anything other than 'skill_package' -- everything else
    (LLM extraction, procedure/goal/claim capture, admission screening,
    artifact/block writing) is genuinely source-agnostic. Deliberately does
    NOT also call document_ingestion.ingest_canonical_document -- that would
    write a second, extractor_version-diverged ingested_artifacts row for the
    same document through a different path; compile_skill_artifact already
    owns the one real write here, the same way it does for skill packages.
    """
    from app.config import settings
    from app.services.document_ingestion import source_artifact_from_canonical_document
    from app.services.embeddings import Embedder
    from app.services.ingestion_sources.document_adapter import DocumentLocator
    from app.services.ingestion_sources.document_adapters import select_adapter
    from app.services.ingestion_sources.document_adapters.github_adapter import GitHubFileAdapter
    from app.services.skill_ingestion import compile_skill_artifact

    identity_job_id = _trusted_identity_job_id(payload)
    locator = DocumentLocator(
        local_path=payload.get("local_path"), uri=payload.get("uri"),
        raw_bytes=payload.get("raw_bytes"),
        filename=payload.get("filename"), content_type_hint=payload.get("content_type_hint"),
        repository=payload.get("repository"), path=payload.get("path"), commit=payload.get("commit"),
    )
    # GitHubFileAdapter is deliberately NOT in DEFAULT_DOCUMENT_ADAPTERS (it is a
    # transport wrapping the per-format adapters' own normalize(), not a peer
    # format) -- but it is the only adapter that can actually FETCH a document
    # over the network at all today; the format adapters (HtmlAdapter etc.) only
    # read local_path/raw_bytes. Tried first, ahead of select_adapter's format
    # list, whenever the payload names a real repo+path -- confirmed live
    # 2026-09-23: without this, a payload naming a real GitHub-hosted .html file
    # was silently matched to the format-only HtmlAdapter by uri suffix and then
    # failed at fetch() with AdapterNotApplicable, never reaching the network.
    from app.services.ingestion_sources.document_adapters.url_adapter import UrlFetchAdapter

    # Network transports first (GitHub file, then any http(s) URL), then local/raw format adapters.
    adapter = next((a for a in (GitHubFileAdapter(), UrlFetchAdapter()) if a.can_handle(locator)), None) \
        or select_adapter(locator)
    if adapter is None:
        # Refuse rather than fabricate: no registered format recognizes this
        # locator. A payload built by a real enqueue path should never hit
        # this -- it already ran select_adapter to decide the job was worth
        # creating -- so treat it as loud, not a silent drop.
        raise ValueError(f"handle_ingest_document: no DocumentSourceAdapter recognizes {locator!r}")

    with _tel.span("ingestion.fetch", kind="TOOL", on_error=_tel.FailureCode.INGESTION_ERROR,
                   source_type=adapter.source_type):
        doc = adapter.fetch_and_normalize(locator)
    artifact = source_artifact_from_canonical_document(doc)

    client = _general_compute_client()
    if client is None:
        log.warning(
            "handle_ingest_document: no LLM client configured "
            "(GENERAL_COMPUTE_API_KEY/GENERAL_COMPUTE_JUDGE_MODEL) -- "
            "this artifact will be refused, not deterministically captured",
        )
    with _tel.span("ingestion.compile", kind="CHAIN", on_error=_tel.FailureCode.INGESTION_ERROR,
                   items_attempted=1, source_type=adapter.source_type) as sp:
        outcome = await compile_skill_artifact(
            pool, artifact, embedder=Embedder(rate_limit_pool=pool),
            created_by="document_ingestion_worker",
            client=client,
            admission_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
            extraction_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
            fallback_extraction_llm_model=settings.general_compute_fallback_model or None,
            claim_extraction_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
            identity_job_id=identity_job_id,
        )
        status = getattr(outcome, "status", None)
        _tel.set_attrs(
            sp, ingest_status=status,
            items_accepted=int(status in ("captured", "new_version")),
            items_duplicate=int(status in ("duplicate", "unchanged")),
            items_rejected=int(status == "rejected"),
            items_failed=int(status == "error"),
            procedure_id=getattr(outcome, "procedure_id", None),
            extraction_model=getattr(outcome, "extraction_model", None))
        if status == "error":
            _tel.fail(sp, _tel.FailureCode.INGESTION_ERROR)
        elif status == "rejected":
            _tel.fail(sp, _tel.FailureCode.INGESTION_PARSE_ERROR, force_keep=False)
        elif status in ("duplicate", "unchanged"):
            _tel.set_attrs(sp, failure_code=_tel.FailureCode.DUPLICATE_OBJECT)


log = logging.getLogger(__name__)

# job_type registry -- deliberately a plain dict, not a class hierarchy;
# ingestion_jobs.job_type is TEXT, uncomstrained, per that column's own
# comment ("episode assembly (ticket 11) will add its own"). A handler
# takes (pool, payload) and does its own transaction(s); this module
# does not wrap handlers in a transaction itself because a handler like
# this one needs the trace_events read and the observation write to be
# separately committable (persist_observation manages its own
# transaction already).
JobHandler = Any


async def handle_normalize_trace_event(pool: asyncpg.Pool, payload: dict) -> None:
    """
    The one handler wired today. Loads the real trace_events row the
    job's payload points at, runs it through extract_deterministic_
    observations() (pure function, observations.py, already tested
    standalone), and persists whatever it finds via persist_observation()
    (also already tested standalone -- this function is pure wiring,
    not new extraction logic).

    Model-based extraction (extract_model_observation) is deliberately
    NOT called here -- that's an LLM call per event, a different cost/
    latency class from this deterministic pass; the per-episode semantic
    extraction pass (trajectory_semantics.py) is the real replacement for
    "promote raw structural observations straight into Claims" -- see the
    note on the removed auto-enqueue below.

    No-op, not an error, if the trace_events row is gone (deleted, or a
    stale job re-run after a real cleanup) -- nothing to extract from is
    a legitimate terminal state, not a failure.
    """
    trace_event_id = payload.get("trace_event_id")
    if not trace_event_id:
        raise ValueError(f"normalize_trace_event payload missing trace_event_id: {payload!r}")

    row = await pool.fetchrow(
        "SELECT id, session_id, event_type, tool_name, tool_input, tool_output, "
        "       canonical_event_type, raw_event, "
        "       success, owner_id, visibility::text AS visibility "
        "FROM trace_events WHERE id = $1",
        trace_event_id,
    )
    if row is None:
        log.info("normalize_trace_event: trace_event %s no longer exists, skipping", trace_event_id)
        return

    # G1: one IngestionContext per session; every observation this handler
    # persists is stamped with it, and the id rides the promote job so the
    # derived claim carries it too.
    ingestion_context_id = await resolve_trace_ingestion_context(
        pool, str(row["session_id"]) if row["session_id"] else "",
        owner_id=row["owner_id"], visibility=row["visibility"],
    )

    trace_event = dict(row)
    # tool_input comes back from asyncpg as a str (JSONB decoded to text
    # by default in this codebase's connection setup), already a dict
    # depending on codec registration, or -- the case this comment used
    # to miss -- a DOUBLE-encoded str, which decodes to a str again and
    # was throwing 'str' object has no attribute 'get' on 32 of 3313 real
    # jobs. extract_deterministic_observations() now routes all three
    # through _decode_json_field(), so no decoding is duplicated here.
    observations = extract_deterministic_observations(trace_event)
    for obs in observations:
        observation_id = await persist_observation(
            pool,
            observation_type=obs["observation_type"],
            label=obs["label"],
            extractor_kind="deterministic",
            event_ids=[str(trace_event_id)],
            properties=obs.get("properties"),
            owner_id=row["owner_id"],
            visibility=row["visibility"],
        )
        # persist_observation takes no ingestion_context_id kwarg -- stamp
        # it in a follow-up UPDATE, the same pattern skill_ingestion uses
        # for the document Observation.
        if ingestion_context_id is not None:
            await pool.execute(
                "UPDATE observations SET ingestion_context_id = $1::uuid WHERE id = $2::uuid",
                ingestion_context_id, observation_id,
            )
        # REMOVED (trajectory-ingestion-hardening task, §9/§1 of the
        # approved plan): this used to unconditionally enqueue
        # 'promote_observation_to_claim' for every deterministic
        # observation -- so raw structural telemetry ("Modified
        # src/api.ts") became a durable Claim automatically. That is
        # exactly the "telemetry label promoted into a reusable Claim"
        # anti-pattern the task's Claims section forbids. Deterministic
        # observations now stay observations: queryable structural
        # telemetry, not auto-promoted. A Claim is now produced either by
        # the per-episode semantic-extraction pass (trajectory_semantics.py
        # -- real propositions with correct epistemic_status/
        # generalization_level, cited to exact events) or by an explicit
        # caller (report_execution, or the opt-in
        # enqueue_pending_claim_promotions() recovery sweep below, both
        # unchanged by this removal).


async def resolve_justification_episode(pool: asyncpg.Pool, observation_id: str):
    """The episode that contains an observation's earliest event, or None.

    Option B's new hop: an observation is anchored to the episode its
    events fall inside, which is what lets a trace-derived claim exist at
    all without a task_node.

    NESTING -- the design call, and a CORRECTION to the kickoff's proposed
    SQL. Episodes nest (parent + child covering the same instant), and the
    intent is that the INNERMOST/most specific one wins. The kickoff
    proposed `ORDER BY parent_episode_id NULLS FIRST, start_ts DESC`, but
    that is inverted: a PARENT is exactly the row whose parent_episode_id
    IS NULL, so NULLS FIRST selects the OUTERMOST episode. Ordering here is
    therefore NULLS LAST -- children (non-null parent) sort ahead of
    parents -- with start_ts DESC as the tiebreaker among siblings, picking
    the latest-starting and thus tightest-fitting span. Proven by a test
    against a real nested fixture rather than trusted.

    Returns None when nothing contains the event -- e.g. the trace was
    ingested but episode assembly has not run for that session yet. That
    is a legitimate no-op (blocking question 2's stated default), not an
    error and not a retry trigger.
    """
    anchor = await pool.fetchrow(
        "SELECT te.session_id, te.timestamp "
        "FROM observation_events oe "
        "JOIN trace_events te ON te.id = oe.event_id "
        "WHERE oe.observation_id = $1::uuid "
        "ORDER BY te.timestamp ASC LIMIT 1",
        observation_id,
    )
    if anchor is None:
        return None

    return await pool.fetchval(
        "SELECT id FROM episodes "
        "WHERE session_id = $1 "
        "  AND start_ts <= $2 "
        "  AND (end_ts IS NULL OR $2 <= end_ts) "
        "  AND t_invalid IS NULL "
        "ORDER BY parent_episode_id NULLS LAST, start_ts DESC "
        "LIMIT 1",
        anchor["session_id"], anchor["timestamp"],
    )


async def handle_promote_observation_to_claim(pool: asyncpg.Pool, payload: dict) -> None:
    """
    The observation -> claim hop, previously the break in the founding
    loop. promote_observation_to_claim() (observations.py) was real and
    tested but had ZERO production callers -- and registering it alone
    would not have helped, because nothing ever created work for it
    either. This handler plus handle_normalize_trace_event's enqueue are
    the two halves; one without the other is inert.

    NAMING: no job_type for this existed anywhere -- the three test files
    that exercise promote_observation_to_claim() call the function
    directly and never enqueue it, and the only job_type string in the
    repo is 'normalize_trace_event'. So this name is new by necessity,
    chosen to mirror the established handler/job_type pairing exactly
    rather than to invent a scheme.

    WHY THE PRE-CHECK BEFORE CALLING PROMOTE: claims.py:179-180 computes
    an embedding (`embedder or Embedder()` -- a real Voyage call) BEFORE
    claims.py:184-189 checks that `task_ids` resolve to live task_nodes
    and returns None if they don't. So an unresolvable promotion spends
    one API call per observation to produce nothing. Checking here first
    keeps a task-less observation free rather than merely useless.

    HONEST LIMIT, stated plainly because it bounds what this closes:
    nothing currently maps a trace-derived observation to a task_node.
    The observations table carries no task, trace, or session column, and
    capture_claim() hard-requires at least one live `task_nodes.skill_ref`
    match. So on a substrate populated only by trace ingestion this
    handler correctly promotes nothing. `task_ids` therefore rides in the
    job payload: when a real observation->task mapping exists, only the
    ENQUEUE site changes, not this handler.
    """
    observation_id = payload.get("observation_id")
    if not observation_id:
        raise ValueError(
            f"promote_observation_to_claim payload missing observation_id: {payload!r}"
        )

    task_ids = payload.get("task_ids") or []
    justification_episode_id = payload.get("justification_episode_id")

    # Option B: EITHER anchor is sufficient. Bail only when there is nothing
    # to anchor the claim to at all -- capture_claim would return None in
    # that case anyway, but only AFTER computing an embedding (a real Voyage
    # call), so checking here keeps an unanchorable observation free rather
    # than merely useless.
    if not task_ids and not justification_episode_id:
        log.debug(
            "promote_observation_to_claim: observation %s has neither task_ids "
            "nor a justification episode; skipping before embedding spend",
            observation_id,
        )
        return

    # Only the task_node path needs pre-validating: an episode id came from
    # our own resolution query against a live episodes row, whereas task_ids
    # are caller-supplied skill_refs that may match nothing. When task_ids
    # resolve to nothing but an episode IS present, that is the ordinary
    # episode-justified case -- proceed with an empty task list rather than
    # skipping.
    if task_ids:
        live = await pool.fetch(
            "SELECT 1 FROM task_nodes WHERE skill_ref = ANY($1::text[]) AND t_invalid IS NULL",
            task_ids,
        )
        if not live:
            if not justification_episode_id:
                log.debug(
                    "promote_observation_to_claim: none of %r resolve to a live "
                    "task_node and no episode; skipping observation %s before "
                    "embedding spend", task_ids, observation_id,
                )
                return
            task_ids = []

    claim_id = await promote_observation_to_claim(
        pool,
        observation_id=str(observation_id),
        task_ids=list(task_ids),
        justification_episode_id=justification_episode_id,
    )
    if claim_id is None:
        log.info(
            "promote_observation_to_claim: observation %s produced no claim "
            "(missing or out of scope)", observation_id,
        )
        return

    # G1: carry the session's IngestionContext onto the derived claim. The
    # id rides the payload from handle_normalize_trace_event; a legacy job
    # without it leaves the column NULL rather than paying for a lookup
    # (the recovery sweep re-enqueues with a fresh payload).
    ingestion_context_id = payload.get("ingestion_context_id")
    if ingestion_context_id:
        await pool.execute(
            "UPDATE knowledge_nodes SET ingestion_context_id = $1::uuid "
            "WHERE id = $2::uuid AND ingestion_context_id IS NULL",
            ingestion_context_id, claim_id,
        )


def _placement_bounded_int(value: Any, default: int, field: str, maximum: int = 1000) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ValueError(f"{field} must be an integer between 1 and {maximum}")
    return value


def _placement_context(payload: Any) -> dict[str, Any]:
    from app.ingestion import queue as ingestion_queue

    if not isinstance(payload, dict):
        raise ValueError("goal_abstraction_placement payload must be an object")
    goal_id = payload.get("goal_id")
    if not isinstance(goal_id, str) or not goal_id.strip():
        raise ValueError("goal_abstraction_placement payload requires goal_id")
    raw_job = payload.get("_job") or {}
    if not isinstance(raw_job, dict):
        raise ValueError("goal_abstraction_placement job metadata must be an object")
    job_id = validate_identity_job_id(raw_job.get("id"))
    scope_type = str(raw_job.get("scope_type") or "global")
    scope_entity_id = raw_job.get("scope_entity_id")
    if scope_type == "global":
        scope_entity_id = None
    visibility = str(raw_job.get("visibility") or "public")
    owner_id = raw_job.get("owner_id")
    ingestion_queue.validate_scope(
        GOAL_ABSTRACTION_PLACEMENT_JOB,
        scope_type,
        visibility,
        owner_id,
    )
    placement_key = goal_abstraction_placement_key(goal_id)
    payload_key = payload.get("idempotency_key")
    job_key = raw_job.get("idempotency_key")
    if payload_key is not None and str(payload_key) != placement_key:
        raise ValueError("goal_abstraction_placement payload idempotency key is invalid")
    if job_key is not None and str(job_key) != placement_key:
        raise ValueError("goal_abstraction_placement job idempotency key is invalid")
    config = WorkerConfig()
    candidate_limit = _placement_bounded_int(
        payload.get("candidate_limit"),
        config.goal_abstraction_candidate_limit,
        "candidate_limit",
    )
    neighbor_seed_limit = _placement_bounded_int(
        payload.get("neighbor_seed_limit"),
        config.goal_abstraction_neighbor_seed_limit,
        "neighbor_seed_limit",
    )
    neighbor_limit = _placement_bounded_int(
        payload.get("neighbor_limit"),
        config.goal_abstraction_neighbor_limit,
        "neighbor_limit",
    )
    minimum_confidence = payload.get(
        "minimum_confidence", config.goal_abstraction_minimum_confidence
    )
    if (
        isinstance(minimum_confidence, bool)
        or not isinstance(minimum_confidence, (int, float))
        or not math.isfinite(float(minimum_confidence))
        or not 0.0 <= float(minimum_confidence) <= 1.0
    ):
        raise ValueError("minimum_confidence must be a finite number between 0 and 1")
    if visibility == "private":
        access_scope = AccessScope.for_user(str(owner_id))
    elif visibility == "org":
        access_scope = AccessScope.for_org_member(
            str(owner_id), [str(scope_entity_id)]
        )
    else:
        access_scope = AccessScope.anonymous()
    judge_mode = str(payload.get("judge_mode") or "model")
    if judge_mode not in ("model", "none"):
        raise ValueError("goal_abstraction_placement judge_mode must be model or none")
    return {
        "goal_id": goal_id,
        "goal_version": int(payload.get("goal_version", 1)),
        "identity_decision_id": payload.get("identity_decision_id"),
        "judge_mode": judge_mode,
        "candidate_limit": candidate_limit,
        "neighbor_seed_limit": neighbor_seed_limit,
        "neighbor_limit": neighbor_limit,
        "minimum_confidence": float(minimum_confidence),
        "idempotency_key": placement_key,
        "job_id": job_id,
        "scope_type": scope_type,
        "scope_entity_id": scope_entity_id,
        "visibility": visibility,
        "owner_id": owner_id,
        "access_scope": access_scope,
        "tenant_scope": (
            TenantScope.for_tenant(str(scope_entity_id))
            if visibility == "org" and scope_entity_id
            else TenantScope.commons()
        ),
    }


def _normalized_goal_scope(goal: Any) -> tuple[str, Optional[str]]:
    scope_type = str(goal.get("scope_type") or "global")
    scope_entity_id = goal.get("scope_entity_id")
    if scope_type == "global":
        scope_entity_id = None
    return scope_type, None if scope_entity_id in (None, "") else str(scope_entity_id)


def _goal_matches_job(goal: Any, context: dict[str, Any]) -> bool:
    if _normalized_goal_scope(goal) != (
        context["scope_type"],
        context["scope_entity_id"],
    ):
        return False
    if str(goal.get("visibility")) != context["visibility"]:
        return False
    if context["visibility"] == "private" and goal.get("owner_id") != context["owner_id"]:
        return False
    return True


def _goal_privacy(goal: Any) -> tuple[str, Optional[str]]:
    visibility = str(goal.get("visibility"))
    owner_id = str(goal.get("owner_id")) if goal.get("owner_id") is not None else None
    return visibility, owner_id if visibility == "private" else None


def _raise_partial(unavailable: Any, missing_ids: Any) -> None:
    if unavailable:
        shard_id, reason = next(iter(dict(unavailable).items()))
        raise ShardUnavailable(str(shard_id), str(reason))
    if missing_ids:
        raise GoalRelationDependencyError(
            {"goal_abstraction": "incomplete hydration"}, [str(item) for item in missing_ids]
        )


def _candidate_text(goal: Any) -> str:
    name = str(goal.get("canonical_name") or "")
    description = goal.get("description")
    return f"{name}: {description}" if description else name


def _candidate_from_goal(goal: Any) -> Candidate:
    return Candidate(
        id=str(goal["id"]),
        name=str(goal.get("canonical_name") or ""),
        text=_candidate_text(goal),
        home_shard_id=goal.get("home_shard_id"),
    )


def _deduplicate_candidates(
    candidates: list[Candidate], limit: int
) -> list[Candidate]:
    result: list[Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        result.append(candidate)
        if len(result) >= limit:
            break
    return result


async def _expand_placement_neighbors(
    pool: asyncpg.Pool,
    candidates: list[Candidate],
    *,
    context: dict[str, Any],
    seed_limit: int,
    neighbor_limit: int,
    pools: Any,
) -> list[Candidate]:
    expanded = list(candidates)
    for seed in candidates[:seed_limit]:
        result = await expand_goal_neighbors(
            pool,
            seed.id,
            access_scope=context["access_scope"],
            tenant_scope=context["tenant_scope"],
            direction="both",
            limit=neighbor_limit,
            pools=pools,
        )
        if result.get("partial"):
            _raise_partial(
                result.get("unavailable_shards"), result.get("missing_ids")
            )
        for item in [*(result.get("parents") or []), *(result.get("children") or [])]:
            expanded.append(_candidate_from_goal(item["goal"]))
    return _deduplicate_candidates(expanded, context["candidate_limit"])


async def _load_goal_embedding(
    pool: asyncpg.Pool, goal_id: str
) -> tuple[Optional[list[float]], Optional[str]]:
    owner = await home_pool(pool, "goal", goal_id)
    row = await owner.fetchrow(
        """
        SELECT embedding::text AS embedding, embedding_model_id
        FROM goals
        WHERE id = $1::uuid AND t_invalid IS NULL AND status <> 'merged'
        """,
        goal_id,
    )
    if row is None:
        raise GoalRelationDependencyError(
            {"goal_abstraction": "canonical Goal unavailable"}, [goal_id]
        )
    value = row.get("embedding")
    if value is None:
        return None, None
    if isinstance(value, str):
        stripped = value.strip().lstrip("[").rstrip("]")
        if not stripped:
            return None, None
        try:
            vector = [float(item) for item in stripped.split(",")]
        except (TypeError, ValueError) as exc:
            raise GoalRelationDependencyError(
                {"goal_abstraction": "stored Goal embedding is invalid"}, [goal_id]
            ) from exc
    elif isinstance(value, (list, tuple)):
        try:
            vector = [float(item) for item in value]
        except (TypeError, ValueError) as exc:
            raise GoalRelationDependencyError(
                {"goal_abstraction": "stored Goal embedding is invalid"}, [goal_id]
            ) from exc
    else:
        raise GoalRelationDependencyError(
            {"goal_abstraction": "stored Goal embedding is invalid"}, [goal_id]
        )
    if not vector or not all(math.isfinite(item) for item in vector):
        raise GoalRelationDependencyError(
            {"goal_abstraction": "stored Goal embedding is invalid"}, [goal_id]
        )
    model = row.get("embedding_model_id")
    if not model:
        raise GoalRelationDependencyError(
            {"goal_abstraction": "stored Goal embedding has no model"}, [goal_id]
        )
    return vector, str(model)


def _validate_placement_decision(
    decision: dict[str, Any], anchor: Any, context: dict[str, Any]
) -> None:
    decision_scope = (
        str(decision.get("scope_type") or "global"),
        decision.get("scope_entity_id"),
    )
    if decision_scope[0] == "global":
        decision_scope = ("global", None)
    if decision_scope != _normalized_goal_scope(anchor):
        raise ValueError("Goal identity decision scope does not match the placement job")
    if canonical_identity_text(str(decision.get("candidate_text") or "")) != canonical_identity_text(
        _candidate_text(anchor)
    ):
        raise ValueError("Goal identity decision text does not match the placement Goal")


async def _placement_candidate_decisions(
    pool: asyncpg.Pool,
    anchor: Any,
    candidates: list[Candidate],
    *,
    context: dict[str, Any],
    judge: Any,
) -> list[tuple[Candidate, Optional[str], Optional[str], Optional[str]]]:
    decisions: dict[str, Candidate] = {}
    decision_metadata: dict[str, tuple[Optional[str], Optional[str], Optional[str]]] = {}
    if context.get("judge_mode") == "none":
        return []
    decision_id = context.get("identity_decision_id")
    if decision_id:
        prior = await load_goal_identity_decision(pool, str(decision_id))
        if prior is None:
            raise ValueError("Goal identity decision for placement was not found")
        _validate_placement_decision(prior, anchor, context)
        prior_candidates = prior.get("candidates") or []
        prior_by_id = {str(candidate.id): candidate for candidate in prior_candidates}
        for candidate in candidates:
            remembered = prior_by_id.get(candidate.id)
            if remembered is None:
                continue
            candidate.relation = remembered.relation
            candidate.confidence = remembered.confidence
            decisions[candidate.id] = candidate
            decision_metadata[candidate.id] = (
                str(prior["id"]),
                prior.get("judge_provider"),
                prior.get("judge_model"),
            )
    missing = [candidate for candidate in candidates if candidate.id not in decisions]
    if not missing:
        return [
            (
                decisions[candidate.id],
                *decision_metadata[candidate.id],
            )
            for candidate in candidates
            if candidate.id in decisions
        ]
    if judge is None:
        judge = default_judge()
    result = await judge.judge_identity_batch(
        "goal", _candidate_text(anchor), [candidate.text for candidate in missing]
    )
    if not bool(getattr(result, "ok", False)):
        raise SemanticJudgmentUnavailable(
            f"goal abstraction judgment unavailable ({getattr(result, 'reason', 'no provider result')})",
            attempts=getattr(result, "attempts", None),
        )
    verdicts = getattr(result, "value", None)
    if not isinstance(verdicts, list) or len(verdicts) != len(missing):
        raise SemanticJudgmentUnavailable(
            "goal abstraction judgment returned an invalid verdict count"
        )
    for candidate, verdict in zip(missing, verdicts):
        if not isinstance(verdict, dict):
            raise SemanticJudgmentUnavailable("goal abstraction judgment returned a malformed verdict")
        relation = verdict.get("relation")
        confidence = verdict.get("confidence")
        if relation not in {
            "same", "specializes", "generalizes", "related", "contradicts", "distinct"
        }:
            raise SemanticJudgmentUnavailable("goal abstraction judgment returned an unsupported relation")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise SemanticJudgmentUnavailable("goal abstraction judgment returned invalid confidence")
        if relation == "same" and float(confidence) < SAME_MIN_CONFIDENCE:
            relation = "related"
        candidate.relation = relation
        candidate.confidence = float(confidence)
    same = next(
        (
            (candidate, float(candidate.confidence))
            for candidate in missing
            if candidate.relation == "same"
        ),
        None,
    )
    if same is not None:
        final_decision = "same"
        resolved_id = same[0].id
    else:
        first = missing[0].relation or "distinct"
        final_decision = _RELATION_TO_DECISION.get(first, "related")
        resolved_id = None
    remembered = await record_decision(
        pool,
        object_type="goal",
        candidate_text=_candidate_text(anchor),
        scope_type=context["scope_type"],
        scope_entity_id=context["scope_entity_id"],
        decision=final_decision,
        resolved_id=resolved_id,
        candidates=missing,
        judge=judge,
        provider=getattr(result, "provider", None),
        model=getattr(result, "model", None),
        fts_n=0,
        vec_n=0,
        job_id=context.get("job_id"),
        idempotency_key=(
            f"{context['idempotency_key']}:recall"
            if context.get("job_id") is not None
            else None
        ),
        detail={
            "operation": "goal_abstraction_placement",
            "source_decision_id": decision_id,
        },
    )
    remembered_id = str(remembered["id"]) if remembered is not None else None
    remembered_candidates: dict[str, Candidate] = {}
    remembered_providers: dict[str, tuple[Optional[str], Optional[str]]] = {}
    if remembered_id:
        stored = await load_goal_identity_decision(pool, remembered_id)
        if stored is not None:
            remembered_candidates = {
                str(candidate.id): candidate for candidate in stored.get("candidates") or []
            }
            remembered_providers = {
                str(candidate.id): (
                    stored.get("judge_provider"),
                    stored.get("judge_model"),
                )
                for candidate in stored.get("candidates") or []
            }
    for candidate in missing:
        stable = remembered_candidates.get(candidate.id)
        if stable is not None:
            candidate.relation = stable.relation
            candidate.confidence = stable.confidence
        provider, model = remembered_providers.get(
            candidate.id,
            (getattr(result, "provider", None), getattr(result, "model", None)),
        )
        decisions[candidate.id] = candidate
        decision_metadata[candidate.id] = (remembered_id, provider, model)
    return [
        (
            decisions[candidate.id],
            *decision_metadata[candidate.id],
        )
        for candidate in candidates
        if candidate.id in decisions
    ]


async def _goal_relation_state(
    pool: asyncpg.Pool,
    specific: str,
    abstract: str,
    tenant_scope: TenantScope,
) -> Optional[str]:
    tenant_sql, tenant_params = tenant_predicate(
        tenant_scope, alias="r", param_index=3
    )
    row = await pool.fetchrow(
        f"""
        SELECT r.status
        FROM goal_relations r
        WHERE r.specific_goal_id = $1::uuid
          AND r.abstract_goal_id = $2::uuid
          AND r.relation_type = 'SPECIALIZES'
          AND {tenant_sql}
        """,
        specific,
        abstract,
        *tenant_params,
    )
    return None if row is None else str(row["status"])


async def _enqueue_placement_audit(
    pool: asyncpg.Pool,
    goal_id: str,
    reason: str,
    context: dict[str, Any],
) -> None:
    await enqueue_goal_abstraction_audit(
        pool,
        goal_id,
        reason=reason,
        scope_type=context["scope_type"],
        scope_entity_id=context["scope_entity_id"],
        owner_id=context["owner_id"],
        visibility=context["visibility"],
    )


async def handle_goal_abstraction_placement(
    pool: asyncpg.Pool,
    payload: dict,
    *,
    judge: Any = None,
) -> dict[str, Any]:
    context = _placement_context(payload)
    goal_id = context["goal_id"]
    pools = pools_for(pool)
    anchor_result = await expand_goal_neighbors(
        pool,
        goal_id,
        access_scope=context["access_scope"],
        tenant_scope=context["tenant_scope"],
        direction="both",
        limit=context["neighbor_limit"],
        pools=pools,
    )
    if anchor_result.get("partial"):
        _raise_partial(
            anchor_result.get("unavailable_shards"), anchor_result.get("missing_ids")
        )
    anchor = anchor_result.get("goal")
    if anchor is None:
        raise GoalRelationDependencyError(
            {"goal_abstraction": "Goal projection is pending"}, [goal_id]
        )
    if not _goal_matches_job(anchor, context):
        raise ValueError("placement job scope does not match the canonical Goal")
    local_candidates = [
        _candidate_from_goal(item["goal"])
        for item in [
            *(anchor_result.get("parents") or []),
            *(anchor_result.get("children") or []),
        ]
    ]
    embedding, embedding_model = await _load_goal_embedding(pool, goal_id)
    recalled, fts_count, vector_count = await generate_goal_candidates(
        pool,
        _candidate_text(anchor),
        scope_type=context["scope_type"],
        scope_entity_id=context["scope_entity_id"],
        embedding=embedding,
        embedding_model=embedding_model,
        fts_k=context["candidate_limit"],
        vector_k=context["candidate_limit"],
        top_n=context["candidate_limit"],
        exclude_id=goal_id,
    )
    candidates = _deduplicate_candidates(
        [*local_candidates, *recalled], context["candidate_limit"]
    )
    candidates = await _expand_placement_neighbors(
        pool,
        candidates,
        context=context,
        seed_limit=context["neighbor_seed_limit"],
        neighbor_limit=context["neighbor_limit"],
        pools=pools,
    )
    rejected_count = 0
    privacy_safe_candidates: list[Candidate] = []
    for candidate in candidates:
        try:
            privacy_check = await adjudicate_goal_relation(
                pool,
                goal_id,
                candidate.id,
                access_scope=context["access_scope"],
                tenant_scope=context["tenant_scope"],
                pools=pools,
            )
        except (GoalRelationScopeError, GoalRelationVisibilityError, GoalRelationSelfError):
            rejected_count += 1
            continue
        if _goal_privacy(privacy_check["specific_goal"]) != _goal_privacy(
            privacy_check["abstract_goal"]
        ):
            rejected_count += 1
            continue
        privacy_safe_candidates.append(candidate)
    decided = await _placement_candidate_decisions(
        pool, anchor, privacy_safe_candidates, context=context, judge=judge
    )
    accepted_count = len(anchor_result.get("parents") or []) + len(
        anchor_result.get("children") or []
    )
    proposed_count = 0
    uncertain = False
    for candidate, decision_id, provider, model in decided:
        relation = candidate.relation
        if relation == "same":
            uncertain = True
            continue
        if relation not in ("specializes", "generalizes"):
            continue
        specific, abstract = (
            (goal_id, candidate.id)
            if relation == "specializes"
            else (candidate.id, goal_id)
        )
        try:
            adjudication = await adjudicate_goal_relation(
                pool,
                specific,
                abstract,
                access_scope=context["access_scope"],
                tenant_scope=context["tenant_scope"],
                pools=pools,
            )
        except (GoalRelationScopeError, GoalRelationVisibilityError, GoalRelationSelfError):
            rejected_count += 1
            continue
        if _goal_privacy(adjudication["specific_goal"]) != _goal_privacy(
            adjudication["abstract_goal"]
        ):
            rejected_count += 1
            continue
        current_status = await _goal_relation_state(
            pool, specific, abstract, context["tenant_scope"]
        )
        if current_status == "rejected":
            uncertain = True
            rejected_count += 1
            continue
        if current_status == "accepted":
            accepted_count += 1
            continue
        confidence = float(candidate.confidence or 0.0)
        high_confidence = confidence >= context["minimum_confidence"]
        try:
            if high_confidence:
                await persist_goal_relation(
                    pool,
                    specific,
                    abstract,
                    status="accepted",
                    provenance="identity_resolution",
                    access_scope=context["access_scope"],
                    tenant_scope=context["tenant_scope"],
                    confidence=confidence,
                    decision_id=decision_id,
                    decision_metadata={
                        "operation": "goal_abstraction_placement",
                        "policy": RELATION_POLICY,
                        "policy_version": RELATION_POLICY_VERSION,
                        "authority": RELATION_AUTHORITY,
                        "relation": relation,
                        "judge_provider": provider,
                        "judge_model": model,
                    },
                    decided_by=None if decision_id else RELATION_AUTHORITY,
                    expected_status=current_status,
                    pools=pools,
                )
                accepted_count += 1
            else:
                uncertain = True
                if current_status is None:
                    await persist_goal_relation(
                        pool,
                        specific,
                        abstract,
                        status="proposed",
                        provenance="identity_resolution",
                        access_scope=context["access_scope"],
                        tenant_scope=context["tenant_scope"],
                        confidence=confidence,
                        decision_id=decision_id,
                        decision_metadata={
                            "operation": "goal_abstraction_placement",
                            "policy": RELATION_POLICY,
                            "policy_version": RELATION_POLICY_VERSION,
                            "authority": RELATION_AUTHORITY,
                            "relation": relation,
                        },
                        decided_by=None if decision_id else RELATION_AUTHORITY,
                        expected_status=current_status,
                        pools=pools,
                    )
                    proposed_count += 1
        except (
            GoalRelationCycleError,
            GoalRelationRedundancyError,
            GoalRelationScopeError,
            GoalRelationStatusConflict,
            GoalRelationVisibilityError,
            GoalRelationSelfError,
        ):
            rejected_count += 1
            uncertain = True
    if uncertain:
        await _enqueue_placement_audit(pool, goal_id, "uncertain", context)
    if accepted_count == 0:
        await _enqueue_placement_audit(pool, goal_id, "orphan", context)
    return {
        "goal_id": goal_id,
        "candidates": len(candidates),
        "fts_candidates": fts_count,
        "vector_candidates": vector_count,
        "accepted_edges": accepted_count,
        "proposed_edges": proposed_count,
        "rejected_edges": rejected_count,
        "uncertain": uncertain,
    }


async def handle_goal_abstraction_audit(
    pool: asyncpg.Pool, payload: dict
) -> dict[str, Any]:
    del pool
    if not isinstance(payload, dict):
        raise ValueError("goal_abstraction_audit payload must be an object")
    goal_id = payload.get("goal_id")
    reason = payload.get("reason")
    if not isinstance(goal_id, str) or not goal_id.strip():
        raise ValueError("goal_abstraction_audit payload requires goal_id")
    if reason not in GOAL_ABSTRACTION_AUDIT_REASONS:
        raise ValueError("goal_abstraction_audit payload has an invalid reason")
    return {"goal_id": goal_id, "reason": reason, "audited": True}


JOB_HANDLERS: dict[str, JobHandler] = {
    "normalize_trace_event": handle_normalize_trace_event,
    "promote_observation_to_claim": handle_promote_observation_to_claim,
    "ingest_skill_package": handle_ingest_skill_package,
    "ingest_document": handle_ingest_document,
    GOAL_ABSTRACTION_PLACEMENT_JOB: handle_goal_abstraction_placement,
    GOAL_ABSTRACTION_AUDIT_JOB: handle_goal_abstraction_audit,
    # 'extract_procedure_from_episode' is registered further down, right
    # after its handler is defined -- that handler sits below the sweep it
    # belongs with, and a forward reference here would be a NameError at
    # import time. Registration is asserted by a test either way.
}


async def enqueue_skill_package_jobs(
    pool: asyncpg.Pool, *, source_spec: dict, refs: list[dict],
) -> int:
    """Queue one deduplicated job per package for distributed workers."""
    queued = 0
    for ref in refs:
        payload = {**source_spec, **ref}
        # Older workers wrote json.dumps(payload) through asyncpg's JSONB
        # codec, producing a JSON *string* rather than an object. Decode
        # that legacy shape while checking idempotency, then write the new
        # object shape below. Without this compatibility expression every
        # rerun misses the existing job and floods the shared queue.
        exists = await pool.fetchval(
            "SELECT 1 FROM ingestion_jobs WHERE job_type='ingest_skill_package' "
            "AND (CASE WHEN jsonb_typeof(payload)='string' "
            "THEN (payload #>> '{}')::jsonb ELSE payload END)->>'source_id'=$1 "
            "AND (CASE WHEN jsonb_typeof(payload)='string' "
            "THEN (payload #>> '{}')::jsonb ELSE payload END)->>'commit'=$2 "
            "AND (CASE WHEN jsonb_typeof(payload)='string' "
            "THEN (payload #>> '{}')::jsonb ELSE payload END)->>'path'=$3 "
            "AND status IN ('pending','processing','done') LIMIT 1",
            str(payload["source_id"]), str(payload["commit"]), str(payload["path"]),
        )
        if exists:
            continue
        await pool.execute(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2::jsonb)",
            # create_pool() registers a JSONB encoder, so pass the object
            # itself. json.dumps(payload) would be encoded a second time.
            "ingest_skill_package", payload,
        )
        queued += 1
    return queued


async def resume_failed_skill_jobs(
    pool: asyncpg.Pool, *, include_embedding_failures: bool = False,
) -> int:
    """Requeue fixable skill failures without creating an API-quota storm.

    Provider-exhaustion rows stay failed by default until an operator has
    restored quota or deliberately selected a provider. Source/fetch/parser
    failures can be retried through the normal --resume path.
    """
    result = await pool.execute(
        "UPDATE ingestion_jobs SET status='pending', claimed_at=NULL, completed_at=NULL "
        "WHERE job_type='ingest_skill_package' AND status='failed' "
        "AND ($1 OR last_error IS NULL OR last_error NOT LIKE 'EmbeddingError(%')",
        include_embedding_failures,
    )
    tail = result.rsplit(" ", 1)[-1]
    return int(tail) if tail.isdigit() else 0


async def resume_failed_extraction_jobs(
    pool: asyncpg.Pool, *, min_age_seconds: int = 300,
) -> int:
    """Requeue failed `extract_procedure_from_episode` jobs -- the real
    retry path for ExtractionTransientFailure (schema.py): since that
    failure means no procedure row was written, requeuing the job is
    exactly what lets the episode get a real extraction attempt on the
    next pass, same idea as resume_failed_skill_jobs() above but for the
    other extraction pipeline.

    Rate-limited failures (`last_error` naming a 429/rate-limit, per
    strategies.py's `_looks_like_rate_limit`) only requeue once
    `completed_at` is older than `min_age_seconds` -- a real backoff, so
    calling this on every ingestion pass does not immediately re-hit the
    same exhausted quota. Non-rate-limit transient failures (a malformed
    LLM response, a transport error) requeue immediately: those are more
    likely genuine model flakiness than a sustained outage.
    """
    result = await pool.execute(
        "UPDATE ingestion_jobs SET status='pending', claimed_at=NULL, completed_at=NULL "
        "WHERE job_type='extract_procedure_from_episode' AND status='failed' "
        "AND ("
        "  ((last_error ILIKE '%rate_limit%' OR last_error ILIKE '%429%') "
        "   AND completed_at < now() - ($1 || ' seconds')::interval)"
        "  OR last_error IS NULL"
        "  OR (last_error NOT ILIKE '%rate_limit%' AND last_error NOT ILIKE '%429%')"
        ")",
        str(min_age_seconds),
    )
    tail = result.rsplit(" ", 1)[-1]
    return int(tail) if tail.isdigit() else 0


# The episode-arrived-late recovery path. Ordering matters here and there
# is no way around it: resolve_justification_episode() runs at ENQUEUE
# time, so an observation whose session has not been through episode
# assembly yet resolves None, the job completes as 'done' having correctly
# done nothing, and nothing ever revisits it.
#
# Measured on real dogfooding data 2026-08-29: ingestion ran before
# assembly, so all 3,106 promotion jobs resolved a NULL episode and the
# substrate ended with ONE claim instead of ~3,106. The handler was right,
# the queue was right, and the loop still did not run -- the gap was
# purely that nothing re-offers work once the missing anchor appears.
#
# Deliberately a re-ENQUEUE rather than a retry: the original jobs are
# genuinely 'done' (they did the correct thing with the information that
# existed), and rewriting terminal job rows would destroy the record of
# what actually happened. A new job for new information is the honest
# shape, and it keeps ingestion_jobs append-only in spirit with the rest
# of the substrate.
_PENDING_PROMOTION_SQL = """
WITH anchor AS (
    -- The observation's EARLIEST event, matching
    -- resolve_justification_episode()'s own anchor choice exactly.
    SELECT DISTINCT ON (oe.observation_id)
           oe.observation_id, oe.event_id, te.session_id, te."timestamp"
    FROM observation_events oe
    JOIN trace_events te ON te.id = oe.event_id
    ORDER BY oe.observation_id, te."timestamp" ASC
)
, candidate AS (
    SELECT a.observation_id, a.event_id, a."timestamp",
           (SELECT ep.id FROM episodes ep
             WHERE ep.session_id = a.session_id
               AND ep.start_ts <= a."timestamp"
               AND (ep.end_ts IS NULL OR a."timestamp" <= ep.end_ts)
               AND ep.t_invalid IS NULL
             -- Same NULLS LAST / start_ts DESC innermost-wins ordering as
             -- resolve_justification_episode(). If one changes, both must.
             ORDER BY ep.parent_episode_id NULLS LAST, ep.start_ts DESC
             LIMIT 1) AS episode_id
    FROM anchor a
    WHERE NOT EXISTS (
            -- already produced a claim: nothing owed
            SELECT 1 FROM claim_sources cs WHERE cs.observation_id = a.observation_id)
      AND NOT EXISTS (
            -- a promotion is already queued for it: never double-enqueue,
            -- because each promotion costs one real embedding call
            SELECT 1 FROM ingestion_jobs j
             WHERE j.job_type = 'promote_observation_to_claim'
               AND j.status IN ('pending', 'processing')
               AND j.payload->>'observation_id' = a.observation_id::text)
)
SELECT observation_id, event_id, episode_id
FROM candidate
-- ANCHORED ROWS FIRST, and this ordering is load-bearing, not cosmetic.
-- Found by running the first cut against the real corpus: ordering purely
-- by newest-first spent the entire budget on the live session's own tail
-- (examined 25, enqueued 0, still_unanchored 25) because the newest
-- observations are exactly the ones episode assembly has not reached yet.
-- A bounded sweep must spend its limit on work it can actually complete;
-- the unanchored frontier is still counted and reported, just not
-- allowed to crowd out the backlog.
ORDER BY (episode_id IS NULL), "timestamp" DESC
LIMIT $1
"""


async def enqueue_pending_claim_promotions(
    pool: asyncpg.Pool, *, limit: int = 100
) -> dict:
    """Re-offer observations whose justifying episode arrived after their
    original promotion job already completed.

    Returns real counts: {"examined", "enqueued", "still_unanchored"}.

    BOUNDED AND OPT-IN ON PURPOSE. Every job this creates ends in
    capture_claim(), which computes an embedding -- a real, paid Voyage
    call, one per observation. An unbounded sweep over a dogfooding
    corpus is thousands of calls nobody asked for, so the caller must
    choose a limit and `run_ingestion.py` only calls this behind an
    explicit flag. `limit` caps rows examined AND enqueued together;
    newest observations first, since those are the ones a user is most
    likely to be waiting on.

    Idempotent: an observation with a claim, or with a promotion already
    pending/processing, is skipped. Running it twice in a row enqueues
    nothing the second time.
    """
    if limit <= 0:
        # A true no-op, not an empty result: the default path must not
        # even pay for the sweep query.
        return {"examined": 0, "enqueued": 0, "still_unanchored": 0}

    rows = await pool.fetch(_PENDING_PROMOTION_SQL, limit)
    enqueued = 0
    unanchored = 0
    for r in rows:
        if r["episode_id"] is None:
            # Assembly still has not covered this session. Correct no-op,
            # counted rather than hidden so the caller can see the real
            # size of the remaining backlog.
            unanchored += 1
            continue
        await pool.execute(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
            "promote_observation_to_claim",
            json.dumps({
                "observation_id": str(r["observation_id"]),
                "trace_event_id": str(r["event_id"]),
                "task_ids": [],
                "justification_episode_id": str(r["episode_id"]),
                # Distinguishes a recovery enqueue from the original
                # inline one when reading the job table by hand later.
                "requeued_after_episode_assembly": True,
            }),
        )
        enqueued += 1
    return {
        "examined": len(rows),
        "enqueued": enqueued,
        "still_unanchored": unanchored,
    }


# ---------------------------------------------------------------------
# claim -> procedure candidate. The last manual hop in the founding loop.
#
# THE QUALITY GATE, and why it is this and not a round number.
#
# Measured over all 230 episodes on the real corpus that contain at least
# one observation (2026-08-29):
#     n_obs        p25=4  median=8  p75=20  max=517
#     >=2 distinct observation_types :  79
#     containing a test_run          :  15
#     containing a commit_made       :   3
#     completion signal (either)     :  17
#     this gate, all three clauses   :  16   (7% of episodes)
#
# 1. EXPLICIT GOAL + OUTCOME REQUIRED. A test *command* or a commit is
#    not a successful outcome. The row must carry a declared goal (episode
#    metadata or agent_traces.intent) and explicitly passing test_run
#    observations, with no failed or ungraded test. Both values are copied into the job
#    payload and revalidated by the worker. This is a correctness
#    requirement: extract_procedure() refuses anything whose
#    evidence.outcome != "success" (V5_evidence_sufficiency). Inferring
#    either field here would manufacture the evidence V5 is meant to
#    require, so episodes lacking either stay unextracted.
#
# 2. n_obs >= 5. p25 is 4, so this drops the bottom quartile. Below five
#    observations there is not enough tool sequence for
#    derive_step_skeleton() to produce a step list worth reviewing.
#
# 3. n_types >= 2. THE FILTER THAT KILLS THE GARBAGE. A single-type
#    episode is "edited six files" or "ran six commands" -- no task shape
#    at all. This is what excludes the "Modified check3.py"-shaped claims
#    the corpus audit flagged as near-worthless: those live in
#    file_touched-only episodes and would burn a real LLM call to produce
#    a candidate nobody would approve.
#
# The V4 validator is a deliberate SECOND line of defence behind this
# gate, not a replacement for it: if grounded_hybrid_v1 degrades to
# deterministic_v1 (no client, API failure, ABSTAIN), capability_statement
# becomes goal_text verbatim, V4 sees the evidence token in it and
# refuses, and no procedures row is written. Garbage in therefore costs at
# most one call and still cannot produce a garbage row -- but the gate is
# what stops us making the call at all.
#
# ONE EXTRACTION PER EPISODE, not per claim: the episode is the unit of
# work extract_procedure() actually consumes (SessionEvidenceSource reads
# the whole session window). Several claims sharing an episode would
# otherwise each pay for the same extraction.
_PENDING_EXTRACTION_SQL = """
WITH claim_episode AS (
    -- claims that have a justifying episode and no procedure yet
    SELECT DISTINCT el.episode_id
    FROM episode_links el
    JOIN knowledge_nodes k ON k.id = el.target_id
     AND k.node_type = 'claim' AND k.t_invalid IS NULL
    WHERE el.target_table = 'knowledge_nodes'
),
profile AS (
    SELECT ep.id AS episode_id, ep.session_id,
           count(DISTINCT o.id) AS n_obs,
           count(DISTINCT o.observation_type) AS n_types,
           count(DISTINCT o.id) FILTER (
               WHERE o.observation_type = 'test_run'
                 AND o.properties->>'passed' = 'true'
           ) AS passing_tests,
           count(DISTINCT o.id) FILTER (
               WHERE o.observation_type = 'test_run'
                 AND o.properties->>'passed' = 'false'
           ) AS failing_tests,
           count(DISTINCT o.id) FILTER (
               WHERE o.observation_type = 'test_run'
                 AND (o.properties->>'passed') IS DISTINCT FROM 'true'
                 AND (o.properties->>'passed') IS DISTINCT FROM 'false'
           ) AS unknown_tests,
           COALESCE(
               NULLIF(BTRIM(ep.metadata->>'declared_goal'), ''),
               NULLIF(BTRIM(ep.metadata->>'goal'), ''),
               NULLIF(BTRIM(ep.metadata->>'intent'), ''),
               NULLIF(BTRIM(ep.metadata->>'user_goal'), ''),
               (SELECT NULLIF(BTRIM(at.intent), '')
                  FROM agent_traces at
                 WHERE at.session_id = ep.session_id
                   AND at.intent IS NOT NULL
                 ORDER BY at.started_at ASC
                 LIMIT 1)
           ) AS goal_text
    FROM episodes ep
    JOIN trace_events te ON te.session_id = ep.session_id
         AND te."timestamp" >= ep.start_ts
         AND (ep.end_ts IS NULL OR te."timestamp" <= ep.end_ts)
    JOIN observation_events oe ON oe.event_id = te.id
    JOIN observations o ON o.id = oe.observation_id
    WHERE ep.t_invalid IS NULL
      AND ep.id IN (SELECT episode_id FROM claim_episode)
    GROUP BY ep.id, ep.session_id
)
SELECT p.episode_id, p.session_id, p.n_obs, p.n_types,
       p.passing_tests, p.failing_tests, p.unknown_tests, p.goal_text
FROM profile p
WHERE p.goal_text IS NOT NULL   -- clause 1: exact source-supplied goal
  AND p.passing_tests > 0       -- clause 2: explicit success evidence
  AND p.failing_tests = 0       -- no known failed test may be called success
  AND p.unknown_tests = 0       -- no ungraded test may be called success
  AND p.n_obs   >= $2           -- clause 3: enough sequence to derive from
  AND p.n_types >= $3           -- clause 4: an actual task shape
  AND NOT EXISTS (
        -- idempotency: this episode already produced a procedure
        SELECT 1 FROM procedures pr
         WHERE pr.t_invalid IS NULL
           AND pr.source_episode_ids @> ARRAY[p.episode_id])
  AND NOT EXISTS (
        -- never double-enqueue: each job is a real paid LLM call
        SELECT 1 FROM ingestion_jobs j
         WHERE j.job_type = 'extract_procedure_from_episode'
           AND j.status IN ('pending', 'processing')
           AND j.payload->>'episode_id' = p.episode_id::text)
ORDER BY p.n_obs DESC
LIMIT $1
"""

MIN_OBSERVATIONS_TO_EXTRACT = 5
MIN_OBSERVATION_TYPES_TO_EXTRACT = 2


async def enqueue_pending_procedure_extractions(
    pool: asyncpg.Pool, *, limit: int = 10,
) -> dict:
    """Enqueue procedure extraction for claim-justifying episodes that
    clear the quality gate above. Returns {"examined", "enqueued"}.

    BOUNDED AND OPT-IN, same shape as enqueue_pending_claim_promotions:
    every job this creates ends in one real grounded_hybrid_v1 LLM call,
    so the caller must choose a limit and run_ingestion.py only reaches
    this behind an explicit --extract-limit flag. Richest episodes first
    (n_obs DESC): if the budget is small, spend it where there is most to
    extract from.

    Idempotent: an episode that already produced a live procedure, or that
    already has an extraction pending/processing, is skipped.
    """
    if limit <= 0:
        return {"examined": 0, "enqueued": 0}

    rows = await pool.fetch(
        _PENDING_EXTRACTION_SQL, limit,
        MIN_OBSERVATIONS_TO_EXTRACT, MIN_OBSERVATION_TYPES_TO_EXTRACT,
    )
    from app.services.shards import all_pools, multi_shard
    if rows and await multi_shard(pool):
        # the NOT EXISTS above only sees procedures on this database; an episode may already have produced one on a shard
        done: set[str] = set()
        eps = [r["episode_id"] for r in rows]
        for _sid, spool in await all_pools(pool, strict=True):
            if spool is pool:
                continue
            for x in await spool.fetch(
                "SELECT unnest(source_episode_ids) AS e FROM procedures WHERE t_invalid IS NULL AND source_episode_ids && $1::uuid[]", eps):
                done.add(str(x["e"]))
        rows = [r for r in rows if str(r["episode_id"]) not in done]
    for r in rows:
        await pool.execute(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
            "extract_procedure_from_episode",
            json.dumps({
                "episode_id": str(r["episode_id"]),
                "session_id": r["session_id"],
                # Source facts selected by _PENDING_EXTRACTION_SQL, never
                # worker defaults. Durable jobs are revalidated below.
                "goal_text": r["goal_text"],
                "outcome": "success",
                # Carried for the audit trail: which facts let this episode
                # through the gate at enqueue time.
                "gate": {
                    "n_obs": r["n_obs"],
                    "n_types": r["n_types"],
                    "passing_tests": r["passing_tests"],
                    "failing_tests": r["failing_tests"],
                    "unknown_tests": r["unknown_tests"],
                },
            }),
        )
    return {"examined": len(rows), "enqueued": len(rows)}


async def build_episode_evidence_source(
    pool: asyncpg.Pool, episode_id: str, *, goal_text: str, outcome: str = "success",
):
    """EPISODE-WINDOWED evidence assembly for extract_procedure() -- the
    real evidence-reading half of handle_extract_procedure_from_episode,
    factored out so the admin re-extraction entry point (admin.py's
    `POST /v1/admin/procedures/{id}/reextract`) can rebuild the SAME real
    evidence a background job would, instead of duplicating this query.

    NOT session-wide (SessionEvidenceSource would be the obvious choice
    and is the WRONG one here: it reads every observation/tool call for
    the whole session_id and treats episode_id as a label only -- proved
    by running it: three different gated episodes from one session
    produced three byte-identical procedures because all three saw
    exactly the same session-wide evidence). AgentRunEvidenceSource takes
    the evidence in memory, which lets the window be applied here.

    Returns None if the episode is gone (soft-deleted since it was
    gated) -- an honest "nothing to extract from", not an error.
    """
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

    ep = await pool.fetchrow(
        "SELECT session_id, start_ts, end_ts, project_id, owner_id FROM episodes "
        "WHERE id = $1::uuid AND t_invalid IS NULL",
        str(episode_id),
    )
    if ep is None:
        return None

    window = (
        'te.session_id = $1 AND te."timestamp" >= $2 '
        'AND ($3::timestamptz IS NULL OR te."timestamp" <= $3)'
    )
    obs_rows = await pool.fetch(
        "SELECT DISTINCT o.id, o.observation_type, o.label, o.properties "
        "FROM observations o "
        "JOIN observation_events oe ON oe.observation_id = o.id "
        "JOIN trace_events te ON te.id = oe.event_id "
        f"WHERE {window} ORDER BY o.id",
        ep["session_id"], ep["start_ts"], ep["end_ts"],
    )
    tool_rows = await pool.fetch(
        "SELECT te.tool_name FROM trace_events te "
        f"WHERE {window} AND te.tool_name IS NOT NULL ORDER BY te.sequence ASC",
        ep["session_id"], ep["start_ts"], ep["end_ts"],
    )

    observations = [
        {"observation_type": r["observation_type"], "label": r["label"],
         "properties": dict(r["properties"] or {})}
        for r in obs_rows
    ]
    tool_sequence = [r["tool_name"] for r in tool_rows]

    return AgentRunEvidenceSource(
        goal_text=goal_text.strip(), outcome=outcome, observations=observations,
        tool_sequence=tool_sequence, started_at=ep["start_ts"],
        project_id=ep["project_id"], episode_id=str(episode_id),
        session_id=ep["session_id"], steps_used=len(tool_sequence),
    ), ep


async def handle_extract_procedure_from_episode(
    pool: asyncpg.Pool, payload: dict,
) -> None:
    """Run the real extract_procedure() over a gated episode.

    The worker accepts only a source-derived `goal_text` and explicit
    `outcome="success"` payload produced by _PENDING_EXTRACTION_SQL. It
    never manufactures either value: old/manual jobs missing those facts
    fail before an extraction call or a persisted candidate.
    """
    episode_id = payload.get("episode_id")
    session_id = payload.get("session_id")
    goal_text = payload.get("goal_text")
    outcome = payload.get("outcome")
    if not episode_id or not session_id or not isinstance(goal_text, str) or not goal_text.strip():
        raise ValueError(
            "extract_procedure_from_episode payload missing source-derived ids or goal_text"
        )
    if outcome != "success":
        raise ValueError(
            "extract_procedure_from_episode requires an explicit successful outcome"
        )

    from app.services.procedure_extraction import extract_procedure

    built = await build_episode_evidence_source(
        pool, str(episode_id), goal_text=goal_text, outcome=outcome,
    )
    if built is None:
        log.info("extract_procedure_from_episode: episode %s is gone; skipping",
                 episode_id)
        return
    source, ep = built
    # B19: "Private execution remains: scope = USER_PRIVATE... Publishing
    # is explicit." This IS the execution-derived local-learning path
    # (A11) -- a candidate extracted from one real episode/session's own
    # recorded trace, the SAME conceptual path find_best_way's tier-2 ad-
    # hoc capture and report_execution's B18 learning loop already fixed
    # this session. This worker was the one real caller still missing
    # that fix: it never passed visibility/owner_id at all, so every
    # episode processed here silently defaulted to extract_procedure()'s
    # own `visibility="public"` -- landing directly in global scope with
    # no publish step, exactly the rule B19 forbids. `episodes.owner_id`
    # (already a real, populated column) is the episode's actual owner;
    # a `None` owner_id (a legacy/system episode with no real owner) is
    # honestly left private-with-no-owner rather than silently promoted
    # to public for lack of one to attribute it to.
    # ExtractionTransientFailure (an LLM-strategy infra failure -- see
    # schema.py's own docstring) is deliberately NOT caught here: letting
    # it propagate out of this handler is what makes process_pending_jobs
    # (this module, further down) mark the job `status='failed'` with the
    # real error captured in `last_error`, rather than this function
    # silently absorbing the failure and the job reporting 'done' with
    # nothing written. resume_failed_extraction_jobs() is the requeue
    # path back to a real retry.
    result = await extract_procedure(
        pool, source, client=_extraction_client(),
        visibility="private", owner_id=ep["owner_id"],
    )

    if result.validation_failures:
        # Not an error: the validators refusing a weak candidate is the
        # system working. Logged so a sweep's real yield is visible.
        log.info(
            "extract_procedure_from_episode: episode %s refused by validators: %s",
            episode_id, result.validation_failures,
        )
        return
    if result.abstained:
        # The selected strategy's own explicit, final answer that this
        # episode has no real procedure in it -- extract_procedure()
        # returns this WITHOUT writing a procedure row at all now (no
        # more write-then-retire heuristic), so there is nothing left to
        # do here but log it.
        log.info(
            "extract_procedure_from_episode: episode %s ABSTAINED "
            "(strategy %s found no real procedure); nothing written",
            episode_id, result.extracted_by,
        )
        return

    # G1: stamp the session's IngestionContext onto the new procedure
    # version row and any procedure-targeted evidence extract_procedure
    # wrote for it. Follow-up UPDATEs -- extract_procedure()/capture_procedure()
    # take no ingestion_context_id kwarg and this lane does not own them.
    ingestion_context_id = await resolve_trace_ingestion_context(
        pool, str(ep["session_id"]),
    )
    if ingestion_context_id is not None and result.version_row_id is not None:
        from app.services.shards import home_pool
        await (await home_pool(pool, "procedure", str(result.version_row_id), by_row_id=True)).execute(
            "UPDATE procedures SET ingestion_context_id = $1::uuid "
            "WHERE id = $2::uuid AND ingestion_context_id IS NULL",
            ingestion_context_id, str(result.version_row_id),
        )
        await pool.execute(
            "UPDATE evidence SET ingestion_context_id = $1::uuid "
            "WHERE target_type = 'procedure' AND target_id = $2::uuid "
            "AND ingestion_context_id IS NULL",
            ingestion_context_id, str(result.version_row_id),
        )

    log.info(
        "extract_procedure_from_episode: episode %s -> procedure %s (by %s)",
        episode_id, result.procedure_id, result.extracted_by,
    )

    await _maybe_auto_synthesize(
        pool, episode_id=str(episode_id),
        scope_type="project" if ep["project_id"] else "global",
        scope_entity_id=ep["project_id"],
        owner_id=ep["owner_id"],
    )


# ---------------------------------------------------------------------
# Local MCP-execution learning: closes the real gap found by reading
# durable_run.py/observations.py/claims.py directly (not from the audit
# docs, which claim this chain already exists end to end -- it did not).
# A local execution_run's Episode (app.execution.episode) is closed and
# THIS job enqueued from inside the SAME transaction as the run's
# terminal status write (durable_run._close_episode_and_enqueue_
# consolidation) -- everything from here on runs OUT of that transaction,
# so an LLM outage can never block a run from reaching a terminal state.
#
# Reuses the SAME canonical Observation/Claim/Evidence/Procedure
# machinery the global (ingested-transcript) pipeline above uses --
# persist_observation(), claim_extraction.persist_claim_candidate()
# (which already does dedup/equivalence DETECTION via claim_equivalence.py,
# never auto-merge -- exactly the ADD/REFINE/CONTRADICT/SUPERSEDE
# distinction the local-learning spec asks for, with no second
# implementation of it), record_claim_evidence(), and extract_procedure()
# unchanged. The only genuinely new code is the LOCAL-side assembly (which
# events/artifacts/text feed the one bounded LLM pass) -- never a second
# Claim/Procedure engine.
# ---------------------------------------------------------------------
CONSOLIDATION_EXTRACTOR = "local_execution_consolidation@1"

# execution_run_events.event_type values worth turning into deterministic
# Observations -- see extract_deterministic_observations_from_run_event()
# for the per-type shape. Deliberately excludes pure state-machine
# bookkeeping (run_created/run_claimed/node_claimed/...) that carries no
# durable knowledge on its own.
_MEANINGFUL_RUN_EVENT_TYPES = frozenset({
    "node_succeeded", "node_failed", "artifact_recorded",
    "verification_completed", "tool_result",
})


def _render_local_episode_text(
    *, goal_text: str, outcome: str, observation_dicts: list[dict],
) -> str:
    """Deterministic, non-fabricated Markdown rendering of what actually
    happened in one local Episode -- fed to `normalize_markdown()` so the
    SAME structural chunking/grounding machinery `claim_extraction.py`
    already uses for documents applies here too, unchanged. Every line
    traces to a real persisted Observation; nothing here is invented or
    summarized by a model."""
    lines = ["# Local execution episode", "", f"Goal: {goal_text}", f"Outcome: {outcome}", ""]
    for obs in observation_dicts:
        props = obs.get("properties") or {}
        prop_str = ", ".join(f"{k}={v}" for k, v in props.items() if v is not None)
        lines.append(f"- [{obs['observation_type']}] {obs['label']}" + (f" ({prop_str})" if prop_str else ""))
    return "\n".join(lines)


async def handle_consolidate_local_episode(pool: asyncpg.Pool, payload: dict) -> None:
    """One bounded semantic-consolidation pass for one closed local
    Episode: deterministic Observations (cheap, always run), then ONE
    LLM-backed Claim-candidate extraction pass over a real rendering of
    those Observations, then (only on a real success outcome, only when
    extract_procedure()'s own evidence-sufficiency gate agrees) a
    Procedure candidate -- mirroring handle_extract_procedure_from_episode
    above, but assembled from execution_run_events instead of trace_events
    (see migration 79's observation_events.execution_run_event_id seam).

    Idempotent: guarded by `episodes.metadata.consolidated_at`, checked
    first and set last -- a re-enqueue of an already-consolidated episode
    (duplicate finalize, requeued job) is a real no-op, not a duplicate
    Observation/Claim/Procedure write. An LLM failure mid-pass leaves the
    deterministic Observations already committed untouched and the
    episode UNMARKED (consolidated_at stays unset) -- the job is left
    'failed' by process_pending_jobs()'s own existing semantics, safe to
    re-enqueue later; nothing here fabricates a Claim/Procedure to paper
    over the failure.
    """
    episode_id = payload.get("episode_id")
    execution_run_id = payload.get("execution_run_id")
    if not episode_id or not execution_run_id:
        raise ValueError("consolidate_local_episode payload missing episode_id/execution_run_id")

    ep = await pool.fetchrow(
        "SELECT id, metadata, owner_id, visibility, scope_type, scope_entity_id "
        "FROM episodes WHERE id = $1::uuid AND execution_run_id = $2::uuid",
        str(episode_id), str(execution_run_id),
    )
    if ep is None:
        log.info(
            "consolidate_local_episode: episode %s/run %s not found; skipping",
            episode_id, execution_run_id,
        )
        return
    metadata = dict(ep["metadata"] or {})
    if metadata.get("consolidated_at"):
        log.info("consolidate_local_episode: episode %s already consolidated; skipping", episode_id)
        return

    run = await pool.fetchrow(
        "SELECT id, procedure_id, procedure_version, created_by, final_outcome, status "
        "FROM execution_runs WHERE id = $1::uuid", str(execution_run_id),
    )
    if run is None:
        log.info("consolidate_local_episode: execution_run %s gone; skipping", execution_run_id)
        return
    if run["final_outcome"] is None:
        raise ValueError(
            f"consolidate_local_episode: execution_run {execution_run_id} has no final_outcome "
            "-- episode was closed before the run reached a real terminal state"
        )

    owner_id = ep["owner_id"]
    visibility = ep["visibility"]
    scope_type = ep["scope_type"]
    scope_entity_id = ep["scope_entity_id"]
    outcome = run["final_outcome"]

    events = await pool.fetch(
        "SELECT id, node_order, event_type, payload FROM execution_run_events "
        "WHERE execution_run_id = $1::uuid AND event_type = ANY($2::text[]) ORDER BY seq ASC",
        str(execution_run_id), list(_MEANINGFUL_RUN_EVENT_TYPES),
    )

    # 1. Deterministic per-event Observations -- cheap, no LLM, run
    # unconditionally (schema.md: "do NOT make an LLM call after every
    # Event" -- this is the cheap half of that split).
    observation_dicts: list[dict] = []
    for ev in events:
        derived = extract_deterministic_observations_from_run_event(dict(ev))
        if derived is None:
            continue
        obs_id = await persist_observation(
            pool, observation_type=derived["observation_type"], label=derived["label"],
            extractor_kind="deterministic", execution_run_event_ids=[str(ev["id"])],
            properties=derived.get("properties"), owner_id=owner_id, visibility=visibility,
        )
        observation_dicts.append({
            "id": obs_id, "observation_type": derived["observation_type"],
            "label": derived["label"], "properties": derived.get("properties") or {},
        })

    if not observation_dicts:
        # Nothing meaningful happened (e.g. a run with no artifacts/
        # verification/failed nodes at all) -- mark consolidated so this
        # is not retried forever, but fabricate nothing.
        await pool.execute(
            "UPDATE episodes SET metadata = metadata || jsonb_build_object('consolidated_at', now()::text) "
            "WHERE id = $1::uuid", str(episode_id),
        )
        return

    # execution_runs.procedure_id is the STABLE family id (procedures.
    # procedure_id), not the versioned row id (procedures.id) -- the
    # same distinction app.execution.procedure_graph.fetch_procedure_version's
    # own docstring draws; a plain `WHERE id = ...` would silently match
    # nothing (or worse, a coincidentally-existing unrelated row).
    goal_text = None
    if run["procedure_id"] is not None:
        from app.services.shards import home_pool
        goal_text = await (await home_pool(pool, "procedure", str(run["procedure_id"]))).fetchval(
            "SELECT goal FROM procedures WHERE procedure_id = $1::uuid AND version = $2",
            run["procedure_id"], run["procedure_version"],
        )
    goal_text = (goal_text or "").strip() or f"local execution run {execution_run_id}"

    # 2. Evidence: the run's own real terminal outcome, evidence_type
    # 'execution_result' (matches procedures.py::record_execution_outcome's
    # own choice for the same kind of fact) -- recorded once per Claim
    # this episode produces, in step 3 below, not once per Observation.
    #
    # failure_class is deliberately left unset here: FAILURE_CLASSES
    # (app/execution/failures.py) is real and wired for the
    # report_execution/named-procedure path via classify_and_route(), but
    # that function's own routing table (failure_routes) is keyed to a
    # PROCEDURE's evidence, not a bare durable-run's. execution_run_nodes.
    # error_class uses a DIFFERENT vocabulary entirely (timeout/network/
    # validation/... -- durable_run.classify_error()) -- conflating the
    # two would mislabel evidence with a value from the wrong enum. An
    # unclassified failure_class is honest (NULL), not a gap papered over.

    # 3. One bounded LLM pass: real Claim-candidate extraction over a
    # real, non-fabricated rendering of what this episode's Observations
    # established -- reuses claim_extraction.py's existing chunking/
    # grounding/quality-gate machinery unchanged.
    from app.services.artifact_blocks import normalize_markdown
    from app.services.claim_extraction import extract_claim_candidates_cached, persist_claim_candidate

    episode_text = _render_local_episode_text(
        goal_text=goal_text, outcome=outcome, observation_dicts=observation_dicts,
    )
    blocks = normalize_markdown(episode_text)
    content_hash = hashlib.sha256(episode_text.encode()).hexdigest()

    candidates = await extract_claim_candidates_cached(
        pool, _extraction_client(), blocks, content_hash=content_hash,
        document_hints={"goal": goal_text, "outcome": outcome},
    )

    # capture_claim()'s B7 anchoring rule refuses to write a Claim with no
    # anchor at all (no live task_ids, no justification_episode_id, and no
    # source_ref/ingestion_context_id/observation_id) -- silently, by
    # design, to stop unanchored claims accumulating. persist_claim_
    # candidate() only exposes source_ref/ingestion_context_id/
    # observation_id of those three; observation_dicts[0] is a REAL,
    # already-persisted Observation this exact episode produced (step 1
    # above always runs first and this branch is unreachable when it's
    # empty), so anchoring every candidate to it is honest, not synthetic.
    anchor_observation_id = observation_dicts[0]["id"]

    persisted_claim_ids: list[str] = []
    for candidate in candidates:
        claim_id = await persist_claim_candidate(
            pool, candidate,
            observation_id=anchor_observation_id,
            created_by=CONSOLIDATION_EXTRACTOR,
            owner_id=owner_id, visibility=visibility,
            scope_type=scope_type, scope_entity_id=scope_entity_id,
        )
        if claim_id is not None:
            persisted_claim_ids.append(claim_id)
            await pool.execute(
                "INSERT INTO episode_links (episode_id, target_id, target_table) "
                "VALUES ($1::uuid, $2::uuid, 'knowledge_nodes') ON CONFLICT DO NOTHING",
                str(episode_id), claim_id,
            )

    # Invariant #13 (app/execution/evidence.py::_check_success_criteria):
    # a 'success' outcome requires a real predicate/metrics -- a bare fact
    # dict is not a criterion. The SAME invariant refuses success_criteria
    # on any non-success outcome (V-EVD: "success_criteria belong to
    # successes -- failures classify via failure_class instead"), so this
    # is built once here, correctly, for whichever branch `outcome` is.
    evidence_success_criteria = (
        {
            "predicate": (
                f"execution_run {execution_run_id} reached terminal "
                f"status '{run['status']}' with final_outcome '{outcome}'"
            ),
            "metrics": {"execution_run_id": str(execution_run_id), "terminal_status": run["status"]},
        }
        if outcome == "success" else None
    )
    for claim_id in persisted_claim_ids:
        await record_claim_evidence(
            pool, claim_id=claim_id, evidence_type="execution_result",
            outcome_status=outcome,
            success_criteria=evidence_success_criteria,
            created_by=CONSOLIDATION_EXTRACTOR, owner_id=owner_id, visibility=visibility,
        )

    # 4. Procedure candidate, only on a real success and only when
    # extract_procedure()'s own evidence-sufficiency gate agrees --
    # SAME function tier-2/report_execution/handle_extract_procedure_
    # from_episode all already use, unchanged.
    if outcome == "success":
        from app.services.procedure_extraction import extract_procedure
        from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

        tool_sequence = [str(ev["event_type"]) for ev in events]
        source = AgentRunEvidenceSource(
            goal_text=goal_text, outcome="success", observations=observation_dicts,
            tool_sequence=tool_sequence, project_id=scope_entity_id if scope_type in ("project", "repository") else None,
            episode_id=str(episode_id), session_id=str(execution_run_id),
            steps_used=len(tool_sequence),
        )
        result = await extract_procedure(
            pool, source, client=_extraction_client(),
            visibility=visibility, owner_id=owner_id,
        )
        if result.validation_failures:
            log.info(
                "consolidate_local_episode: episode %s procedure candidate refused by validators: %s",
                episode_id, result.validation_failures,
            )
        elif result.abstained:
            # The selected strategy's own explicit, final answer that no
            # real procedure exists in this episode -- extract_procedure()
            # writes nothing in this case, so there is no row to retire.
            log.info(
                "consolidate_local_episode: episode %s ABSTAINED "
                "(strategy %s found no real procedure); nothing written",
                episode_id, result.extracted_by,
            )
        elif result.procedure_id is not None:
            log.info(
                "consolidate_local_episode: episode %s -> procedure %s (by %s)",
                episode_id, result.procedure_id, result.extracted_by,
            )

    await pool.execute(
        "UPDATE episodes SET metadata = metadata || jsonb_build_object('consolidated_at', now()::text) "
        "WHERE id = $1::uuid", str(episode_id),
    )


JOB_HANDLERS["consolidate_local_episode"] = handle_consolidate_local_episode


# ---------------------------------------------------------------------
# Multi-episode generalization (L2), auto-discovered -- closes the real
# gap synthesis.py's own module docstring names and does not fill: that
# module's `synthesize_procedure()` had ZERO production callers before
# this pass (grepped this session), reachable only from tests supplying a
# hand-picked episode_ids list. This is the ONE natural trigger point a
# normal ingestion run already has after a real, successful single-
# episode extraction: the episode this job just turned into a procedure
# is exactly the "successful episode" synthesis.py's own docstring
# describes as its input.
#
# RETRIEVAL, NOT A MANUAL ID LIST. synthesis.py's own "What this does NOT
# do" section is explicit that candidate discovery is the CALLER's job --
# this function is that caller. No embedding column is ever populated for
# `procedures` today (grepped: extract_procedure()/capture_procedure()
# never pass one), so a real vector search has nothing to query; the
# honest substitute is a real SQL query over already-captured evidence:
# other LIVE procedures that are themselves still single-episode
# extractions (source_episode_ids length 1 -- i.e. not already folded
# into a synthesis result) sharing this episode's own real project scope.
# Coarse on purpose, per synthesis.py's own division of labor: its three
# real compatibility gates (structural tool-sequence alignment, verification
# agreement, predicate contradiction) are what actually decide merge-or-
# refuse, not this query -- a related-but-incompatible candidate found
# here is expected to come back refused, not silently merged.
MAX_SYNTHESIS_CANDIDATES = 4  # + this episode = 5, inside synthesis.py's own
# stated single-digit-N scale ("no support for more than a small number of
# episodes in one call" -- its module docstring, "What this does NOT do").


async def _discover_synthesis_candidates(
    pool: asyncpg.Pool, *, episode_id: str, scope_type: str,
    scope_entity_id: Optional[str], limit: int = MAX_SYNTHESIS_CANDIDATES,
) -> list[str]:
    from app.services.shards import fanout_fetch
    rows = await fanout_fetch(
        pool,
        "SELECT source_episode_ids[1] AS episode_id, t_created FROM procedures "
        "WHERE t_invalid IS NULL AND array_length(source_episode_ids, 1) = 1 "
        "AND source_episode_ids[1] != $1::uuid "
        "AND scope_type = $2 AND scope_entity_id IS NOT DISTINCT FROM $3 "
        "ORDER BY t_created DESC LIMIT $4",
        episode_id, scope_type, scope_entity_id, limit,
    )
    rows = sorted(rows, key=lambda r: r["t_created"], reverse=True)[:limit]
    return [str(r["episode_id"]) for r in rows]


async def _maybe_auto_synthesize(
    pool: asyncpg.Pool, *, episode_id: str, scope_type: str,
    scope_entity_id: Optional[str], owner_id: Optional[str] = None,
) -> None:
    """Synchronous invocation at the natural trigger point -- no new
    scheduler/queue, reuses `synthesize_procedure()` completely unchanged.
    A refusal (structural mismatch, verification disagreement, or a
    contradictory predicate) is the system working exactly as designed:
    the candidate batch is logged and left as distinct, un-blended
    single-episode procedures, never forced into one falsely-universal
    result.

    B19: same execution-derived-learning rule as `handle_extract_
    procedure_from_episode` (its own caller) -- `synthesize_procedure()`
    defaults `visibility="public"` too, and this call never overrode it,
    so an auto-discovered generalization across several episodes landed
    in public/global scope with no explicit publish step. `owner_id`
    (the TRIGGERING episode's real owner -- other episodes in the batch
    may differ, but attributing the batch to the episode that actually
    triggered synthesis is honest, not arbitrary) makes this private,
    matching the single-episode path."""
    from app.services.procedure_extraction.synthesis import (
        MIN_CANDIDATE_EPISODES,
        synthesize_procedure,
    )

    candidates = await _discover_synthesis_candidates(
        pool, episode_id=episode_id, scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )
    batch = [episode_id] + candidates
    if len(batch) < MIN_CANDIDATE_EPISODES:
        return

    synth = await synthesize_procedure(pool, batch, owner_id=owner_id, visibility="private")
    if synth.synthesized:
        log.info(
            "extract_procedure_from_episode: auto-discovered synthesis over %s -> "
            "generalized procedure %s (level %s)",
            synth.contributing_episode_ids, synth.procedure_id, synth.generalization_level,
        )
    else:
        log.info(
            "extract_procedure_from_episode: auto-discovered synthesis batch %s refused: %s",
            batch, synth.refusal_reason,
        )


def _extraction_client():
    """The same OpenAI-compatible client mcp_server/server.py builds for
    its own extract_procedure() call. Returns None when no key is
    configured, which makes GroundedHybridExtractor degrade to
    deterministic_v1 rather than fail -- and V4 then refuses the weak
    candidate, so a missing key costs nothing and writes nothing.
    """
    try:
        from openai import OpenAI

        from app.config import settings

        key = settings.general_compute_api_key
        if not key:
            return None
        return OpenAI(
            max_retries=0, api_key=key,
            base_url=settings.general_compute_base_url,
        )
    except Exception as exc:  # noqa: BLE001 -- never block ingestion on this
        log.warning("extraction client unavailable (%s); degrading to deterministic", exc)
        return None


# Registered here rather than in the literal above: the handler is defined
# below that dict, beside the sweep that feeds it.
JOB_HANDLERS["extract_procedure_from_episode"] = handle_extract_procedure_from_episode

# Semantic-judgment requeue + compaction retry (app/services/semantic/jobs.py).
# Imported lazily-safe: that module never imports this one at import time.
from app.services.semantic import jobs as _semantic_jobs  # noqa: E402

JOB_HANDLERS.update(_semantic_jobs.HANDLERS)


async def claim_jobs(
    pool: asyncpg.Pool, *, limit: int, job_types: Optional[list[str]] = None,
    worker_id: Optional[str] = None,
) -> list[dict]:
    """
    Real SKIP LOCKED claim: marks up to `limit` pending jobs 'processing'
    and returns them, atomically, safe under concurrent workers even
    though only one runs today. asyncpg.Record -> dict so callers don't
    hold the connection/row open past this function's own transaction.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, job_type, payload, attempts, scope_type, scope_entity_id, owner_id, visibility, "
                "idempotency_key, source_id, config_version FROM ingestion_jobs "
                "WHERE status = 'pending' "
                "AND (run_after IS NULL OR run_after <= now()) "
                "AND ($1::text[] IS NULL OR job_type = ANY($1::text[])) "
                "ORDER BY id "
                "LIMIT $2 "
                "FOR UPDATE SKIP LOCKED",
                job_types, limit,
            )
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE ingestion_jobs SET status = 'processing', claimed_at = now(), "
                "claimed_by = $2 WHERE id = ANY($1::bigint[])",
                ids, worker_id,
            )
    return [dict(r) for r in rows]


async def process_pending_jobs(
    pool: asyncpg.Pool, *, limit: int = 500, job_types: Optional[list[str]] = None,
    worker_id: Optional[str] = None,
) -> dict:
    """
    Real entry point: claim up to `limit` pending jobs, run each through
    its registered handler, mark done/failed individually. One job's
    failure (unknown job_type, bad payload, handler exception) does not
    stop the batch -- A4's lesson applied here too: a single malformed
    job must not permanently stall every job after it in the same run.

    Returns real counts, not estimates, same discipline as
    process_collector_file()'s own return value.
    """
    from app.services import benchmark_transfer

    benchmark_transfer.register_handler()
    with _tel.span("ingestion.batch", kind="CHAIN", on_error=_tel.FailureCode.INGESTION_ERROR,
                   worker=worker_id) as sp:
        totals = await _process_pending_jobs(
            pool, limit=limit, job_types=job_types, worker_id=worker_id)
        _tel.set_attrs(sp, items_attempted=totals["claimed"], items_accepted=totals["done"],
                       items_failed=totals["failed"], items_rejected=totals["unknown_type"])
        if totals["failed"]:
            _tel.fail(sp, _tel.FailureCode.INGESTION_ERROR)
        return totals


async def _process_pending_jobs(
    pool: asyncpg.Pool, *, limit: int, job_types: Optional[list[str]], worker_id: Optional[str],
) -> dict:
    jobs = await claim_jobs(pool, limit=limit, job_types=job_types, worker_id=worker_id)
    done = 0
    failed = 0
    unknown_type = 0

    from app.ingestion import queue as ingestion_queue

    for job in jobs:
        job_id = job["id"]
        job_type = job["job_type"]
        try:
            ingestion_queue.validate_scope(
                job_type, job.get("scope_type"), job.get("visibility"), job.get("owner_id"))
        except ingestion_queue.ScopeError as exc:
            failed += 1
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'failed', attempts = attempts + 1, "
                "last_error = $2, completed_at = now() WHERE id = $1",
                job_id, f"scope refused: {exc}",
            )
            continue
        payload = job["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = dict(payload or {})
        try:
            payload["_job"] = ingestion_queue.trusted_job_metadata(job, attempt_field="attempts")
        except ValueError as exc:
            failed += 1
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'failed', attempts = attempts + 1, "
                "last_error = $2, completed_at = now() WHERE id = $1",
                job_id, repr(exc),
            )
            continue

        handler = JOB_HANDLERS.get(job_type)
        if handler is None:
            unknown_type += 1
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'failed', "
                "attempts = attempts + 1, last_error = $2, completed_at = now() "
                "WHERE id = $1",
                job_id, f"no handler registered for job_type={job_type!r}",
            )
            continue

        try:
            with _tel.span("ingestion.job", kind="CHAIN", on_error=_tel.FailureCode.INGESTION_ERROR,
                           job_type=job_type, attempt=job.get("attempts")):
                await handler(pool, payload)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: one
            # job's handler raising must not crash the batch loop; the
            # real error is preserved in last_error for later inspection.
            failed += 1
            log.warning("ingestion job %s (%s) failed: %s", job_id, job_type, exc)
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'failed', "
                "attempts = attempts + 1, last_error = $2, completed_at = now() "
                "WHERE id = $1",
                job_id, repr(exc),
            )
        else:
            done += 1
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'done', "
                "attempts = attempts + 1, completed_at = now() "
                "WHERE id = $1",
                job_id,
            )

    return {
        "claimed": len(jobs),
        "done": done,
        "failed": failed,
        "unknown_type": unknown_type,
        "worker_id": worker_id,
    }


async def requeue_stuck_jobs(pool: asyncpg.Pool, *, older_than_minutes: int = 30) -> int:
    """
    Manual/explicit recovery for jobs left 'processing' by a worker that
    crashed mid-job (the one gap SKIP LOCKED itself doesn't close -- it
    protects against two workers claiming the SAME row, not against a
    claimed row never being finished). Deliberately not run
    automatically inside process_pending_jobs() -- a job stuck because of
    a genuine handler bug should surface via last_error and be looked
    at, not silently retry forever on every run.
    """
    result = await pool.execute(
        "UPDATE ingestion_jobs SET status = 'pending', claimed_at = NULL "
        "WHERE status = 'processing' "
        "AND claimed_at < now() - ($1 || ' minutes')::interval",
        str(older_than_minutes),
    )
    # asyncpg execute() returns a string like "UPDATE 3"
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def handle_ingest_repo(pool: asyncpg.Pool, payload: dict) -> None:
    """One GitHub repo -> a Goal + one-step Procedure per reusable file (app.services.repo_ingestion).
    Payload: {"repository": "owner/repo", "commit"?: sha, "domains"?: [...], "per_domain"?: int}."""
    from app.config import settings
    from app.services.embeddings import Embedder
    from app.services.repo_ingestion import ingest_repo

    client = _general_compute_client()
    if client is None:
        raise RuntimeError("handle_ingest_repo: no LLM client configured (GENERAL_COMPUTE_*)")
    summary = await ingest_repo(
        pool, payload["repository"], commit=payload.get("commit"), client=client,
        model=settings.general_compute_judge_model or "gemma-4-31B-it",
        embedder=Embedder(rate_limit_pool=pool), per_domain=int(payload.get("per_domain") or 10),
        domains=payload.get("domains"), job_id=_trusted_identity_job_id(payload),
    )
    log.info("ingest_repo %s@%s: %s captured, skipped=%s", summary["repository"], str(summary["commit"])[:10],
             len(summary["captured"]), summary["skipped"])


JOB_HANDLERS["ingest_repo"] = handle_ingest_repo
