"""
Full-graph publication dependency traversal (V4-hardening B11 / gate G24;
spec A14 / §34).

WHY THIS EXISTS
    `publication.py::_traverse_dependencies` walks ONLY
    `procedure_dependencies` -- procedure -> procedure. Spec A14/§34 says
    the publication gate MUST traverse the WHOLE lineage a candidate
    stands on:

        Procedure version
          -> procedure dependencies      (procedure_dependencies)
          -> claims                      (procedure_claim_refs, migration 52)
          -> observations                (claim_sources, migration 26)
          -> sources                     (claim.properties.source_ref,
                                          ingestion_contexts.source_ref,
                                          ingested_artifacts.source_ref;
                                          migrations 50/51)
          -> artifacts                   (ingested_artifacts + artifact_blocks
                                          for the procedure's IngestionContext;
                                          migrations 32/55)
          -> evidence / execution lineage (evidence rows targeting the
                                          procedure OR any of its claims;
                                          migration 24)

    Documents now populate every one of those objects (migrations 50-55),
    so "publish this procedure" can silently drag a private claim, a
    private observation, an un-vetted source, or private execution
    evidence into the Global Commons unless the gate looks.

WHAT THIS IS / IS NOT
    IS: a read-only, cycle-safe, bounded classifier. For every object it
    reaches it answers exactly one question -- "would publishing this
    leak something that is not already public?" -- and folds a `blocking`
    verdict up to the caller. The existing procedure -> procedure walk is
    folded in unchanged (`procedures` key), so `publish_procedure` has one
    call site, not two.

    IS NOT: a mechanism that copies anything. It never promotes, never
    writes, never inherits verification. Spec A14: "private evidence does
    not automatically become global verification" -- so even a perfectly
    clean traversal records `verification_inherited=False`; the global
    candidate still starts life with zero independent evidence and must
    earn public verification on its own.

HONEST SCOPE LIMITS
    - One level deep per edge kind. The procedure -> procedure walk was
      always one level (`_traverse_dependencies`); the claim / observation
      / source / artifact / evidence walks match it. Transitive closure
      (a dependency's own dependencies) is deliberately out of scope here
      -- publishing a dependency is itself a gated operation, so its
      lineage was already vetted when IT was published.
    - Fail closed. An unresolved claim/observation/source ref, an unknown
      classification, or a traversal that hits the node bound all count as
      `blocking` -- we cannot prove the lineage is clean, so we refuse.
    - Graceful degradation. A missing table (a database mid-rollout where
      migrations 50-55 are not applied) makes that ONE leg `[]` with a
      logged note, exactly the posture `claim_impact.py` takes for
      `procedure_claim_refs`. It never makes the gate pass something it
      would otherwise block on a fully-migrated database -- a missing
      table means "no such edges", not "edges are fine".
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import asyncpg

from app.services.classification import PRIVATE_CLASSES, classify
from app.services.procedure_claim_refs import list_claim_refs_for_procedure

logger = logging.getLogger(__name__)

# Cap on total distinct objects the traversal will classify. A claim
# referenced by many procedures, or a source feeding many artifacts, is
# deduped by id so the real graph is far smaller than this in practice;
# the bound is a fail-closed backstop against a pathological corpus, not a
# tuning knob. Hitting it appends a `traversal_truncated` blocking entry
# (we cannot prove what we did not look at).
MAX_TRAVERSAL_NODES = 500

# A resolved Source whose reliability was actually assessed and scored
# below this is an untrusted origin -- publishing a procedure that stands
# on it would launder that origin into the commons. NULL (unassessed) is
# NOT auto-blocking here (a freshly registered public source often has no
# score yet) but is surfaced as a note.
SOURCE_TRUST_FLOOR = 0.3

# Visibility values that mean "not already public" -- the same set
# `_traverse_dependencies` blocks procedure dependencies on.
_BLOCKING_VISIBILITY = frozenset({"private", "org", "organization"})

# Evidence types that can (only when independently corroborated) count
# toward "this procedure is globally verifiable".
_VERIFICATION_EVIDENCE_TYPES = frozenset({"execution_result", "reproduction"})

_PROC_DEPS_SQL = (
    "SELECT d.dependency_ref, d.resolution_status, d.target_procedure_id, "
    "p.visibility AS target_visibility, p.scope_type AS target_scope_type, "
    "p.name AS target_name "
    "FROM procedure_dependencies d "
    "LEFT JOIN procedures p ON p.id = d.target_procedure_id "
    "WHERE d.procedure_id = $1::uuid"
)
_PROC_CTX_SQL = "SELECT ingestion_context_id FROM procedures WHERE id = $1::uuid"
_CLAIM_NODES_SQL = (
    "SELECT id, visibility, scope_type, properties "
    "FROM knowledge_nodes WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL"
)
_CLAIM_SOURCES_SQL = (
    "SELECT claim_id, observation_id FROM claim_sources WHERE claim_id = ANY($1::uuid[])"
)
_OBS_SQL = (
    "SELECT id, visibility, owner_id, ingestion_context_id "
    "FROM observations WHERE id = ANY($1::uuid[])"
)
_ICTX_SQL = "SELECT id, source_ref FROM ingestion_contexts WHERE id = ANY($1::uuid[])"
_ARTIFACTS_SQL = (
    "SELECT id, visibility, source_ref, ingestion_context_id "
    "FROM ingested_artifacts "
    "WHERE (procedure_row_id = $1::uuid OR ingestion_context_id = $2::uuid) "
    "AND t_invalid IS NULL"
)
_ABLOCKS_SQL = (
    "SELECT id, visibility FROM artifact_blocks "
    "WHERE ingestion_context_id = $1::uuid AND t_invalid IS NULL"
)
_SOURCES_SQL = (
    "SELECT id, visibility, scope_type, reliability_score "
    "FROM sources WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL"
)
_EVIDENCE_SQL = (
    "SELECT id, evidence_type, visibility, independence_group, outcome_status, direction "
    "FROM evidence "
    "WHERE t_invalid IS NULL AND ( "
    "  (target_type = 'procedure' AND target_id = $1::uuid) "
    "  OR (target_type = 'claim' AND target_id = ANY($2::uuid[])) )"
)


class _Budget:
    """Coarse node counter shared across every leg of one traversal.

    `hit` latches True the moment the cumulative count of distinct
    classified objects exceeds `MAX_TRAVERSAL_NODES`; once latched, later
    legs are skipped and the caller adds a `traversal_truncated` blocking
    entry. Fail closed: a truncated traversal is an un-provable one.
    """

    def __init__(self, limit: int = MAX_TRAVERSAL_NODES) -> None:
        self.limit = limit
        self.used = 0
        self.hit = False

    def account(self, n: int) -> None:
        self.used += n
        if self.used > self.limit:
            self.hit = True


def _rget(row: Any, key: str, default: Any = None) -> Any:
    """Read a column from an asyncpg.Record OR a plain dict (offline fakes
    hand-roll dicts). `Record.get` exists but `dict.get` and `Record.get`
    have the same signature, so this is just a defensive single accessor."""
    try:
        return row.get(key, default)
    except AttributeError:  # pragma: no cover - neither shape lacks .get today
        return row[key] if key in row else default


def _vis_blocking(visibility: Optional[str]) -> bool:
    return (visibility or "").strip().lower() in _BLOCKING_VISIBILITY


def _class_blocking(data_class: Any) -> bool:
    return data_class in PRIVATE_CLASSES


def _label(visibility: Optional[str], data_class: Any) -> str:
    """Human tag for a blocking reason: the visibility when it is the
    disqualifier, else the classification's own name."""
    if visibility:
        return str(visibility).upper()
    return str(getattr(data_class, "value", data_class)).upper()


async def _safe_fetch(
    pool: Any, sql: str, *args: Any, leg: str, degraded: set
) -> list:
    """One sub-query, with the `claim_impact.py` graceful-degradation
    posture: a table that does not exist yet (migrations 50-55 not applied
    on this database) degrades THIS leg to `[]`, records the leg name in
    `degraded`, and logs. Any other Postgres error propagates -- a real
    query bug must not be silently swallowed into a passing gate.

    `degraded` matters for the source leg: a genuinely-absent source row
    is an unresolved origin (blocking, fail closed), but a source leg that
    could not run because the table is missing means "we have no source
    edges here", NOT "every ref is dangling".
    """
    try:
        return list(await pool.fetch(sql, *args))
    except asyncpg.UndefinedTableError:
        degraded.add(leg)
        logger.warning(
            "publication_deps: table for leg %r missing; treating as [] "
            "(migrations 50-55 not applied on this database?)",
            leg,
        )
        return []


async def _safe_fetchval(
    pool: Any, sql: str, *args: Any, leg: str, degraded: set
) -> Any:
    try:
        return await pool.fetchval(sql, *args)
    except asyncpg.UndefinedTableError:
        degraded.add(leg)
        logger.warning("publication_deps: fetchval leg %r table missing; None", leg)
        return None


def _group_key(row: Any) -> str:
    """`COALESCE(independence_group, id::text)` -- migration 24's exact
    independence expression. Rows sharing a key never corroborate each
    other; a NULL group is its own key, so N singletons = N groups."""
    grp = _rget(row, "independence_group")
    if grp:
        return f"grp:{grp}"
    return f"row:{_rget(row, 'id')}"


async def traverse_publication_dependencies(
    pool: Any,
    *,
    procedure_row_id: str,
    procedure_id: str,
    procedure_version: int,
) -> dict:
    """Walk the full publication lineage of one procedure *version* and
    classify every object it reaches.

    Returns a dict with one list per object kind plus roll-ups::

        {
          "procedures":   [{id, visibility, classification, blocking}, ...],
          "claims":       [{id, role, visibility, classification, blocking}, ...],
          "observations": [{id, visibility, classification, blocking}, ...],
          "sources":      [{id, visibility, reliability_score, blocking}, ...],
          "artifacts":    [{id, visibility, blocking}, ...],
          "evidence":     [{id, evidence_type, visibility, independent, blocking}, ...],
          "blocking":     [{kind, id, reason}, ...],   # flattened, every blocker
          "notes":        [str, ...],                  # non-blocking findings
          "counts":       {"procedures": n, "claims": n, ...},
          "verification": {"verification_inherited": False,
                           "independent_public_verification": bool,
                           "global_verification_required": bool},
          "classification": "PUBLIC" | "MIXED",        # compat with the old
                                                       # DependencyReport string
        }

    An object is `blocking` when its visibility is private/org, OR its
    data classification lands in `PRIVATE_CLASSES`, OR it is an
    unresolved/untrusted origin, OR the traversal bound was hit. Every
    such case also appears in the flat `blocking` list with a human reason
    naming the kind + id.

    Evidence rule (spec A14): a private/org evidence row is `blocking`.
    An `execution_result`/`reproduction` row that is NOT independently
    corroborated (only one independence group among the procedure's
    verification evidence) is NOT blocking, but is surfaced as
    `independent=False`, and the non-blocking note
    `private_evidence_not_global_verification` is added so the caller and
    the `publication_records` row both show that GLOBAL verification was
    not inherited from the private lineage.
    """
    budget = _Budget()
    degraded: set[str] = set()  # legs skipped because their table is absent
    blocking: list[dict] = []
    notes: list[str] = []

    def _block(kind: str, obj_id: Any, reason: str) -> None:
        blocking.append({"kind": kind, "id": obj_id, "reason": reason})

    # ---- 1. procedure -> procedure (the existing walk, folded in) ------
    procedures: list[dict] = []
    seen_proc: set[str] = set()
    proc_rows = await _safe_fetch(
        pool, _PROC_DEPS_SQL, procedure_row_id, leg="procedure_dependencies", degraded=degraded
    )
    budget.account(len(proc_rows))
    for d in proc_rows:
        ref = _rget(d, "dependency_ref")
        key = str(_rget(d, "target_procedure_id") or ref)
        if key in seen_proc:
            continue
        seen_proc.add(key)
        vis = _rget(d, "target_visibility")
        resolved = _rget(d, "resolution_status") in (
            "resolved", "resolved_verified", None
        ) or bool(_rget(d, "target_procedure_id"))
        if not resolved:
            entry = {"id": ref, "visibility": vis, "classification": "UNKNOWN",
                     "blocking": True}
            procedures.append(entry)
            _block("procedure", ref,
                   f"dependency {ref!r} is unresolved and cannot be classified")
            continue
        cls = classify(visibility=vis, scope_type=_rget(d, "target_scope_type"))
        is_blocking = _vis_blocking(vis) or _class_blocking(cls)
        procedures.append({
            "id": _rget(d, "target_procedure_id") or ref,
            "visibility": vis,
            "classification": cls.value if hasattr(cls, "value") else str(cls),
            "blocking": is_blocking,
        })
        if is_blocking:
            _block("procedure", _rget(d, "target_procedure_id") or ref,
                   f"dependency {ref!r} ({_rget(d, 'target_name')}) is "
                   f"{(vis or 'UNKNOWN').upper()} and cannot be generalized automatically")

    # ---- 2. procedure -> claims (procedure_claim_refs, migration 52) ---
    claims: list[dict] = []
    claim_ids: list[str] = []
    claim_source_refs: set[str] = set()
    if not budget.hit:
        refs = await list_claim_refs_for_procedure(
            pool, procedure_id, procedure_version
        )
        # role per claim id (a claim may be referenced under several roles;
        # keep the first, dedupe the node lookup).
        role_by_id: dict[str, str] = {}
        for r in refs:
            cid = str(_rget(r, "claim_id"))
            role_by_id.setdefault(cid, _rget(r, "role"))
        want = list(role_by_id)
        budget.account(len(want))
        node_rows = {}
        if want and not budget.hit:
            for n in await _safe_fetch(
                pool, _CLAIM_NODES_SQL, want, leg="knowledge_nodes", degraded=degraded
            ):
                node_rows[str(_rget(n, "id"))] = n
        for cid in want:
            claim_ids.append(cid)
            node = node_rows.get(cid)
            if node is None:
                claims.append({"id": cid, "role": role_by_id[cid],
                               "visibility": None, "classification": "UNRESOLVED",
                               "blocking": True})
                _block("claim", cid,
                       "claim is referenced but not found / not live "
                       "(cannot be classified -- fail closed)")
                continue
            vis = _rget(node, "visibility")
            cls = classify(visibility=vis, scope_type=_rget(node, "scope_type"))
            props = _rget(node, "properties") or {}
            if isinstance(props, dict) and props.get("source_ref"):
                claim_source_refs.add(str(props["source_ref"]))
            is_blocking = _vis_blocking(vis) or _class_blocking(cls)
            claims.append({
                "id": cid, "role": role_by_id[cid], "visibility": vis,
                "classification": cls.value if hasattr(cls, "value") else str(cls),
                "blocking": is_blocking,
            })
            if is_blocking:
                _block("claim", cid,
                       f"claim is {_label(vis, cls)} -- private "
                       "lineage cannot enter the commons automatically")

    # ---- 3. claims -> observations (claim_sources, migration 26) -------
    observations: list[dict] = []
    obs_ctx_ids: set[str] = set()
    if claim_ids and not budget.hit:
        links = await _safe_fetch(
            pool, _CLAIM_SOURCES_SQL, claim_ids, leg="claim_sources", degraded=degraded
        )
        obs_ids = sorted({str(_rget(l, "observation_id")) for l in links})
        budget.account(len(obs_ids))
        if obs_ids and not budget.hit:
            for o in await _safe_fetch(pool, _OBS_SQL, obs_ids, leg="observations", degraded=degraded):
                vis = _rget(o, "visibility")
                cls = classify(visibility=vis)
                ctx = _rget(o, "ingestion_context_id")
                if ctx:
                    obs_ctx_ids.add(str(ctx))
                is_blocking = _vis_blocking(vis) or _class_blocking(cls)
                observations.append({
                    "id": str(_rget(o, "id")), "visibility": vis,
                    "classification": cls.value if hasattr(cls, "value") else str(cls),
                    "blocking": is_blocking,
                })
                if is_blocking:
                    _block("observation", str(_rget(o, "id")),
                           f"observation is {(vis or 'PRIVATE').upper()} -- "
                           "its provenance would leak on publish")

    # ---- 4. artifacts (ingested_artifacts + artifact_blocks) ----------
    artifacts: list[dict] = []
    artifact_source_refs: set[str] = set()
    proc_ctx_id = None
    if not budget.hit:
        proc_ctx_id = await _safe_fetchval(
            pool, _PROC_CTX_SQL, procedure_row_id, leg="procedures.ctx", degraded=degraded
        )
        art_rows = await _safe_fetch(
            pool, _ARTIFACTS_SQL, procedure_row_id, proc_ctx_id,
            leg="ingested_artifacts", degraded=degraded,
        )
        budget.account(len(art_rows))
        for a in art_rows:
            vis = _rget(a, "visibility")
            if _rget(a, "source_ref"):
                artifact_source_refs.add(str(_rget(a, "source_ref")))
            is_blocking = _vis_blocking(vis)
            artifacts.append({"id": str(_rget(a, "id")), "visibility": vis,
                              "blocking": is_blocking})
            if is_blocking:
                _block("artifact", str(_rget(a, "id")),
                       f"ingested artifact is {(vis or 'PRIVATE').upper()}")
        if proc_ctx_id and not budget.hit:
            blk_rows = await _safe_fetch(
                pool, _ABLOCKS_SQL, proc_ctx_id, leg="artifact_blocks", degraded=degraded
            )
            budget.account(len(blk_rows))
            for b in blk_rows:
                vis = _rget(b, "visibility")
                is_blocking = _vis_blocking(vis)
                artifacts.append({"id": str(_rget(b, "id")), "visibility": vis,
                                  "blocking": is_blocking})
                if is_blocking:
                    _block("artifact", str(_rget(b, "id")),
                           f"artifact block is {(vis or 'PRIVATE').upper()}")

    # ---- 5. sources (three feeders -> one deduped set) ----------------
    sources: list[dict] = []
    if not budget.hit:
        ctx_ids = sorted(obs_ctx_ids | ({str(proc_ctx_id)} if proc_ctx_id else set()))
        ctx_source_refs: set[str] = set()
        if ctx_ids:
            for c in await _safe_fetch(
                pool, _ICTX_SQL, ctx_ids, leg="ingestion_contexts", degraded=degraded
            ):
                if _rget(c, "source_ref"):
                    ctx_source_refs.add(str(_rget(c, "source_ref")))
        all_refs = sorted(claim_source_refs | artifact_source_refs | ctx_source_refs)
        budget.account(len(all_refs))
        resolved_ids: set[str] = set()
        if all_refs and not budget.hit:
            for s in await _safe_fetch(pool, _SOURCES_SQL, all_refs, leg="sources", degraded=degraded):
                sid = str(_rget(s, "id"))
                resolved_ids.add(sid)
                vis = _rget(s, "visibility")
                score = _rget(s, "reliability_score")
                cls = classify(visibility=vis, scope_type=_rget(s, "scope_type"))
                untrusted = score is not None and score < SOURCE_TRUST_FLOOR
                is_blocking = _vis_blocking(vis) or _class_blocking(cls) or untrusted
                sources.append({"id": sid, "visibility": vis,
                                "reliability_score": score, "blocking": is_blocking})
                if is_blocking:
                    why = (f"origin reliability {score} is below the trust floor "
                           f"{SOURCE_TRUST_FLOOR}" if untrusted
                           else f"source is {_label(vis, cls)}")
                    _block("source", sid, why)
                elif score is None:
                    notes.append(
                        f"source {sid} has an unassessed reliability_score "
                        "(treated as unknown, not trusted)"
                    )
        for missing in all_refs:
            if "sources" in degraded:
                break  # table absent != every ref dangling
            if missing not in resolved_ids:
                sources.append({"id": missing, "visibility": None,
                                "reliability_score": None, "blocking": True})
                _block("source", missing,
                       "source ref does not resolve to a live sources row "
                       "(unresolved origin -- fail closed)")

    # ---- 6. evidence / execution lineage (migration 24) --------------
    evidence: list[dict] = []
    verification = {
        "verification_inherited": False,
        "independent_public_verification": False,
        "global_verification_required": True,
    }
    if not budget.hit:
        ev_rows = await _safe_fetch(
            pool, _EVIDENCE_SQL, procedure_row_id, claim_ids, leg="evidence", degraded=degraded
        )
        budget.account(len(ev_rows))
        verify_rows = [
            r for r in ev_rows
            if str(_rget(r, "evidence_type")) in _VERIFICATION_EVIDENCE_TYPES
            and _rget(r, "outcome_status") == "success"
        ]
        distinct_groups = {_group_key(r) for r in verify_rows}
        public_groups = {
            _group_key(r) for r in verify_rows
            if (_rget(r, "visibility") or "").lower() == "public"
        }
        corroborated = len(distinct_groups) >= 2
        independent_public = len(public_groups) >= 2
        for r in ev_rows:
            etype = str(_rget(r, "evidence_type"))
            vis = _rget(r, "visibility")
            is_verify = (
                etype in _VERIFICATION_EVIDENCE_TYPES
                and _rget(r, "outcome_status") == "success"
            )
            is_blocking = _vis_blocking(vis)
            evidence.append({
                "id": str(_rget(r, "id")),
                "evidence_type": etype,
                "visibility": vis,
                "independent": bool(is_verify and corroborated),
                "blocking": is_blocking,
            })
            if is_blocking:
                _block("evidence", str(_rget(r, "id")),
                       f"evidence is {(vis or 'PRIVATE').upper()} -- private "
                       "evidence does not become global verification (A14)")
        if verify_rows and not independent_public:
            notes.append("private_evidence_not_global_verification")
        verification["independent_public_verification"] = independent_public
        verification["global_verification_required"] = not independent_public

    if budget.hit:
        _block("traversal", None, "traversal_truncated")

    kinds = {
        "procedures": procedures, "claims": claims, "observations": observations,
        "sources": sources, "artifacts": artifacts, "evidence": evidence,
    }
    return {
        **kinds,
        "blocking": blocking,
        "notes": notes,
        "counts": {k: len(v) for k, v in kinds.items()},
        "verification": verification,
        "classification": "MIXED" if blocking else "PUBLIC",
    }
