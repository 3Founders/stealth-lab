"""
Selective local -> global sync (workflow C): the missing "does the caller
want THIS local `.stealth/*.md` edit pushed back to canonical Postgres"
mechanism.

WHY THIS EXISTS (see `project_knowledge`/`close_exploration` for the two
existing sync-ADJACENT tools, neither of which does this): `.stealth/` is
a regenerated, disposable projection of Postgres -- nothing has ever read
`claims.md`/`procedures.md`/`goals.md` back as trusted input. But an agent
(or a human) legitimately hand-edits those files sometimes -- correcting a
claim's statement, adding a locally-noted claim that has no backend
counterpart yet, adjusting a procedure's step text -- and today there is
no way to get that edit into Postgres short of re-deriving it through an
entirely different tool (`submit_procedure`, `create_goal`, ...) by hand,
with no link back to what was actually edited. This module is the
preview/commit pair that closes that gap, WITHOUT inventing a second
object store: every synced item becomes a normal Claim/Procedure/Goal row
via the exact same service-layer writers every other submission path uses
(`capture_claim`, `capture_procedure`, `find_or_create_goal`), landing in
the same candidate/private lifecycle state those writers already default
to. `.stealth/` stays a projection; this only widens the one specific
gate (an explicit, per-item, human-or-agent SELECTED sync) through which a
local edit can become a real row -- never a bulk or implicit one.

GRAMMAR REUSED, NOT REINVENTED: parses the exact pipe grammar
`app.stealth.pipe_format` renders (`CLAIM|...`, `PROCEDURE|...`/`STEP|...`,
`GOAL|...`) -- this module is that grammar's one real reader, the
render/parse pair `goal_run.md` already has (`render_goal_run_md` /
`parse_goal_run_md`) but claims/procedures/goals never got. A line that
does not match the grammar is skipped, not fabricated into a row.

IDENTITY: `generator.py` confirms every backend-derived row already
carries its REAL canonical id as the pipe row's first field
(`CLAIM|<claim_id>|...`, `PROCEDURE|<procedure_id>|...`,
`GOAL|<goal_id>|...`) -- that id, when it parses as a UUID, is the
correctness-critical anchor this module diffs against. A row whose id
field does NOT parse as a UUID is, by construction, NOT something
`generate_projection` could have written (it only ever emits real ids) --
so it can only be a hand-added local addition with no backend counterpart
yet: NEW.

LOCAL_ONLY: the current `.stealth/*.md` grammar has no real `visibility`
field (claims.md renders `scope`, i.e. `scope_type` -- 'global'/'entity'/
..., never `private`; see `pipe_format.ClaimLine`/`_build_claims_page`).
There is therefore no EXISTING honest signal in the rendered projection
that says "this one is private/local, don't sync it" -- a real format gap,
not something this module can paper over by inventing a fact. The
convention adopted here (documented, not hidden): a `scope` field of
literally `local` or `private` -- values `v0_gate.SCOPE_TYPES` never
contains, so `generate_projection` itself can never produce one -- is
respected as an explicit hand-authored "do not sync" marker, refused by
`commit_local_sync_items` unless the caller passes `allow_local_only=True`.
This is a scoped-down stand-in for a real local-visibility field on the
projection grammar (see this feature's own OPEN QUESTIONS).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.stealth.legacy_context import STEALTH_DIRNAME

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

OBJECT_TYPES = ("claim", "procedure", "goal")

# `preview_local_sync`/`commit_local_sync_items` classifications.
NEW = "NEW"
CHANGED = "CHANGED"
ALREADY_SYNCED = "ALREADY_SYNCED"
LOCAL_ONLY = "LOCAL_ONLY"
CONFLICTING = "CONFLICTING"


def _looks_like_uuid(value: str) -> bool:
    return bool(_UUID_RE.match((value or "").strip()))


def _is_local_only_scope(scope: str) -> bool:
    return (scope or "").strip().lower() in ("local", "private")


def candidate_id(repo_path: str, object_type: str, local_identity: str) -> str:
    """Deterministic hash of `(repo_path, object_type, local_identity)` --
    the SAME id `preview_local_sync` returns and `commit_local_sync_items`
    re-derives, never a cached/trusted token (see module docstring's
    "never trust a stale preview" rule -- `commit_local_sync_items`
    recomputes this from the CURRENT local file, so a candidate id only
    ever resolves if the caller's selection still matches something real
    on disk right now)."""
    raw = f"{os.path.abspath(repo_path)}|{object_type}|{local_identity}".encode("utf-8")
    return "LS-" + hashlib.sha1(raw).hexdigest()[:16]


@dataclass
class LocalObject:
    object_type: str  # "claim" | "procedure" | "goal"
    local_id: str  # the id field as it appears in the local file (may not be a real uuid)
    status: str
    scope: str
    name_or_statement: str
    version: str = "-"
    topic: str = "-"
    extra: dict = field(default_factory=dict)

    @property
    def local_identity(self) -> str:
        # A real (uuid) id IS the identity (the thing being diffed). A
        # hand-added row with no real id has no stable identity except its
        # own content -- content-hashed so re-running preview against the
        # SAME unmodified hand-edit yields the SAME candidate id.
        if _looks_like_uuid(self.local_id):
            return self.local_id
        return hashlib.sha1(
            f"{self.object_type}:{self.name_or_statement}".encode("utf-8")
        ).hexdigest()[:20]


@dataclass
class LocalProjection:
    claims: list[LocalObject]
    procedures: list[LocalObject]
    procedure_steps: dict[str, list[dict]]  # procedure_id -> [{order, goal}]
    goals: list[LocalObject]
    meta: Optional[dict]


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _split_row(line: str) -> list[str]:
    return line.split("|")


def _kv_map(fields: list[str]) -> dict[str, str]:
    return dict(f.split("=", 1) for f in fields if "=" in f)


def _parse_claims_md(text: str) -> list[LocalObject]:
    out: list[LocalObject] = []
    for line in text.splitlines():
        if not line.startswith("CLAIM|"):
            continue
        parts = _split_row(line)
        if len(parts) < 6:
            continue
        kv = _kv_map(parts[6:])
        out.append(LocalObject(
            object_type="claim", local_id=parts[1], status=parts[2], scope=parts[4],
            name_or_statement=parts[5], version=kv.get("version", "1"), topic=parts[3],
            extra={"source": kv.get("source", "unknown")},
        ))
    return out


def _parse_procedures_md(text: str) -> tuple[list[LocalObject], dict[str, list[dict]]]:
    out: list[LocalObject] = []
    steps: dict[str, list[dict]] = {}
    for line in text.splitlines():
        if line.startswith("PROCEDURE|"):
            parts = _split_row(line)
            if len(parts) < 6:
                continue
            kv = _kv_map(parts[6:])
            out.append(LocalObject(
                object_type="procedure", local_id=parts[1], status=parts[2], scope=parts[4],
                name_or_statement=parts[5], version=kv.get("version", "1"), topic=parts[3],
            ))
        elif line.startswith("STEP|"):
            parts = _split_row(line)
            if len(parts) < 6:
                continue
            proc_id, order, goal_type, description = parts[1], parts[3], parts[4], parts[5]
            try:
                order_int = int(order)
            except ValueError:
                continue
            steps.setdefault(proc_id, []).append({"order": order_int, "goal": description, "action": description})
    for lst in steps.values():
        lst.sort(key=lambda s: s["order"])
    return out, steps


def _parse_goals_md(text: str) -> list[LocalObject]:
    out: list[LocalObject] = []
    for line in text.splitlines():
        if not line.startswith("GOAL|") or line.startswith("GOAL_DETAIL|"):
            continue
        parts = _split_row(line)
        if len(parts) < 5:
            continue
        kv = _kv_map(parts[5:])
        out.append(LocalObject(
            object_type="goal", local_id=parts[1], status=parts[2], scope=parts[3],
            name_or_statement=parts[4], version=kv.get("version", "1"),
        ))
    return out


def parse_local_projection(repo_path: str) -> LocalProjection:
    """Read-only. Parses whatever of `claims.md`/`procedures.md`/`goals.md`
    /`meta.json` currently exists under `repo_path/.stealth/` -- a missing
    file yields an empty list for that object type (no `.stealth/` at all,
    or a projection type this generator run never emitted, e.g. no
    `goals.md` written), never an error and never a fabricated row."""
    stealth_dir = os.path.join(repo_path, STEALTH_DIRNAME)
    claims_text = _read_text(os.path.join(stealth_dir, "claims.md")) or ""
    procedures_text = _read_text(os.path.join(stealth_dir, "procedures.md")) or ""
    goals_text = _read_text(os.path.join(stealth_dir, "goals.md")) or ""
    meta_text = _read_text(os.path.join(stealth_dir, "meta.json"))
    meta = None
    if meta_text:
        try:
            meta = json.loads(meta_text)
        except json.JSONDecodeError:
            meta = None

    procedures, steps = _parse_procedures_md(procedures_text)
    return LocalProjection(
        claims=_parse_claims_md(claims_text),
        procedures=procedures,
        procedure_steps=steps,
        goals=_parse_goals_md(goals_text),
        meta=meta,
    )


def _local_generated_at(meta: Optional[dict]) -> Optional[datetime]:
    if not meta or not meta.get("generated_at"):
        return None
    try:
        return datetime.fromisoformat(meta["generated_at"])
    except ValueError:
        return None


async def _fetch_claim(pool: asyncpg.Pool, claim_id: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT id, properties, scope_type, t_valid, t_invalid FROM knowledge_nodes "
        "WHERE id = $1::uuid AND node_type = 'claim'", claim_id,
    )
    if row is None:
        return None
    d = dict(row)
    props = d["properties"]
    if isinstance(props, str):
        props = json.loads(props)
    d["properties"] = props or {}
    return d


async def _superseded_after(pool: asyncpg.Pool, claim_id: str, since: datetime) -> bool:
    """True if some other claim now SUPERSEDES `claim_id` (the real
    `edges` row `relate_claims` writes -- `custom_edge_type='SUPERSEDES'`,
    `source_id`=the new claim, `target_id`=the superseded one, same table/
    columns `app.services.claims.relate_claims` itself writes to, reused
    here rather than a second mechanism) and that edge was created after
    `since`."""
    row = await pool.fetchrow(
        "SELECT 1 FROM edges "
        "WHERE target_id = $1::uuid AND target_table = 'knowledge_nodes' "
        "AND custom_edge_type = 'SUPERSEDES' AND t_created > $2",
        claim_id, since,
    )
    return row is not None


async def _fetch_procedure(pool: asyncpg.Pool, procedure_id: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM procedures WHERE procedure_id = $1::uuid AND t_invalid IS NULL "
        "ORDER BY version DESC LIMIT 1", procedure_id,
    )
    return dict(row) if row else None


async def _fetch_goal(pool: asyncpg.Pool, goal_id: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM goals WHERE id = $1::uuid AND t_invalid IS NULL", goal_id,
    )
    return dict(row) if row else None


@dataclass
class SyncCandidate:
    candidate_id: str
    object_type: str
    classification: str
    local_summary: str
    backend_id: Optional[str]
    scope: str
    status: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "object_type": self.object_type,
            "classification": self.classification, "local_summary": self.local_summary,
            "backend_id": self.backend_id, "scope": self.scope, "status": self.status,
            "reason": self.reason,
        }


async def _classify_claim(pool: asyncpg.Pool, obj: LocalObject, local_generated_at: Optional[datetime]) -> SyncCandidate:
    cid = candidate_id(_CID_REPO_PATH.get(), "claim", obj.local_identity)
    if _is_local_only_scope(obj.scope):
        return SyncCandidate(cid, "claim", LOCAL_ONLY, obj.name_or_statement[:160], None, obj.scope, obj.status,
                              reason="local scope marker -- not eligible for auto-sync")
    if not _looks_like_uuid(obj.local_id):
        return SyncCandidate(cid, "claim", NEW, obj.name_or_statement[:160], None, obj.scope, obj.status,
                              reason="no real backend id -- hand-added or exploration-only")
    backend = await _fetch_claim(pool, obj.local_id)
    if backend is None:
        return SyncCandidate(cid, "claim", CONFLICTING, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status,
                              reason="local file references a claim id that no longer exists in Postgres")
    backend_statement = str(backend["properties"].get("statement") or "")
    backend_status = str(backend["properties"].get("claim_status") or backend["properties"].get("status") or "UNKNOWN")
    if backend["t_invalid"] is not None:
        backend_status = "SUPERSEDED"
    if backend_statement == obj.name_or_statement and backend_status == obj.status:
        return SyncCandidate(cid, "claim", ALREADY_SYNCED, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status)

    since = local_generated_at
    changed_since_projection = since is not None and (
        (backend["t_invalid"] is not None and backend["t_invalid"] > since)
        or await _superseded_after(pool, obj.local_id, since)
    )
    if changed_since_projection:
        return SyncCandidate(cid, "claim", CONFLICTING, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status,
                              reason="backend claim changed after this local projection was generated")
    return SyncCandidate(cid, "claim", CHANGED, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status)


async def _classify_procedure(pool: asyncpg.Pool, obj: LocalObject, local_generated_at: Optional[datetime]) -> SyncCandidate:
    cid = candidate_id(_CID_REPO_PATH.get(), "procedure", obj.local_identity)
    if _is_local_only_scope(obj.scope):
        return SyncCandidate(cid, "procedure", LOCAL_ONLY, obj.name_or_statement[:160], None, obj.scope, obj.status,
                              reason="local scope marker -- not eligible for auto-sync")
    if not _looks_like_uuid(obj.local_id):
        return SyncCandidate(cid, "procedure", NEW, obj.name_or_statement[:160], None, obj.scope, obj.status,
                              reason="no real backend id -- hand-added locally")
    backend = await _fetch_procedure(pool, obj.local_id)
    if backend is None:
        return SyncCandidate(cid, "procedure", CONFLICTING, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status,
                              reason="local file references a procedure id that no longer exists (live) in Postgres")
    backend_name = str(backend.get("name") or "")
    backend_status = str(backend.get("verification_state") or "-")
    if backend_name == obj.name_or_statement and backend_status == obj.status and str(backend.get("version")) == obj.version:
        return SyncCandidate(cid, "procedure", ALREADY_SYNCED, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status)
    since = local_generated_at
    changed_since_projection = since is not None and backend.get("updated_at") and backend["updated_at"] > since
    if changed_since_projection:
        return SyncCandidate(cid, "procedure", CONFLICTING, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status,
                              reason="backend procedure changed after this local projection was generated")
    return SyncCandidate(cid, "procedure", CHANGED, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status)


async def _classify_goal(pool: asyncpg.Pool, obj: LocalObject, local_generated_at: Optional[datetime]) -> SyncCandidate:
    cid = candidate_id(_CID_REPO_PATH.get(), "goal", obj.local_identity)
    if _is_local_only_scope(obj.scope):
        return SyncCandidate(cid, "goal", LOCAL_ONLY, obj.name_or_statement[:160], None, obj.scope, obj.status,
                              reason="local scope marker -- not eligible for auto-sync")
    if not _looks_like_uuid(obj.local_id):
        return SyncCandidate(cid, "goal", NEW, obj.name_or_statement[:160], None, obj.scope, obj.status,
                              reason="no real backend id -- hand-added locally")
    backend = await _fetch_goal(pool, obj.local_id)
    if backend is None:
        return SyncCandidate(cid, "goal", CONFLICTING, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status,
                              reason="local file references a goal id that no longer exists (live) in Postgres")
    backend_name = str(backend.get("canonical_name") or "")
    backend_status = str(backend.get("status") or "-")
    if backend_name == obj.name_or_statement and backend_status == obj.status and str(backend.get("version")) == obj.version:
        return SyncCandidate(cid, "goal", ALREADY_SYNCED, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status)
    since = local_generated_at
    # goals has no `updated_at` column (bitemporal t_valid/t_invalid/
    # t_created only, unlike procedures) -- `t_created` of the CURRENT
    # live row is the closest honest proxy for "this version came into
    # existence after the local projection was generated".
    changed_since_projection = since is not None and backend.get("t_created") and backend["t_created"] > since
    if changed_since_projection:
        return SyncCandidate(cid, "goal", CONFLICTING, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status,
                              reason="backend goal changed after this local projection was generated")
    return SyncCandidate(cid, "goal", CHANGED, obj.name_or_statement[:160], obj.local_id, obj.scope, obj.status)


# `candidate_id()` needs `repo_path` but the classify_* helpers above are
# shared by both preview and commit and are the natural per-object unit --
# threading `repo_path` through every call site is noisier than this
# single-call-scoped contextvar-free stash (module-private, set once per
# `preview_local_sync`/`commit_local_sync_items` call, never left set
# across an await boundary that could race two concurrent calls onto the
# SAME event loop -- both callers set it synchronously before their first
# `await` and never touch it again after).
class _RepoPathHolder:
    def __init__(self) -> None:
        self._value = "."

    def set(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value


_CID_REPO_PATH = _RepoPathHolder()


async def preview_local_sync(
    pool: asyncpg.Pool, repo_path: str, object_types: Optional[list[str]] = None,
) -> list[dict]:
    """READ-ONLY. Parses the local `.stealth/*.md` projection and classifies
    every referenced object against canonical Postgres. Writes nothing --
    not even a cache; `candidate_id` is a deterministic hash, recomputed
    (not looked up) by `commit_local_sync_items`."""
    object_types = object_types or list(OBJECT_TYPES)
    _CID_REPO_PATH.set(repo_path)
    projection = parse_local_projection(repo_path)
    local_generated_at = _local_generated_at(projection.meta)

    out: list[SyncCandidate] = []
    if "claim" in object_types:
        for obj in projection.claims:
            out.append(await _classify_claim(pool, obj, local_generated_at))
    if "procedure" in object_types:
        for obj in projection.procedures:
            out.append(await _classify_procedure(pool, obj, local_generated_at))
    if "goal" in object_types:
        for obj in projection.goals:
            out.append(await _classify_goal(pool, obj, local_generated_at))
    return [c.to_dict() for c in out]


@dataclass
class CommitResult:
    candidate_id: str
    object_type: str
    outcome: str  # "committed" | "skipped" | "refused"
    reason: str = ""
    new_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"candidate_id": self.candidate_id, "object_type": self.object_type, "outcome": self.outcome}
        if self.reason:
            d["reason"] = self.reason
        if self.new_id:
            d["id"] = self.new_id
        return d


async def _commit_claim(
    pool: asyncpg.Pool, obj: LocalObject, sc: SyncCandidate, *, repo_path: str, created_by: str, owner_id: Optional[str],
) -> CommitResult:
    from app.services.claims import capture_claim, relate_claims
    from app.services.sources import register_source

    src = await register_source(
        pool, source_type="agent_execution", locator=f"stealth-local-sync:{repo_path}",
        provenance="company_ingested", created_by=created_by, visibility="public",
        owner_id=owner_id, scope_type="global",
    )
    new_id = await capture_claim(
        pool, statement=obj.name_or_statement, task_ids=[], source_ref=src["id"],
        claim_type=obj.topic if obj.topic != "-" else None,
        properties={"local_sync": {"repo_path": repo_path, "source_claim_id": sc.backend_id}},
        created_by=created_by, owner_id=owner_id, visibility="private", scope_type="global",
    )
    if new_id is None:
        return CommitResult(sc.candidate_id, "claim", "refused", reason="capture_claim declined the write (no anchor)")
    if sc.classification == CHANGED and sc.backend_id:
        await relate_claims(pool, from_claim_id=new_id, to_claim_id=sc.backend_id, relation="SUPERSEDES", created_by=created_by)
    return CommitResult(sc.candidate_id, "claim", "committed", new_id=new_id)


async def _commit_procedure(
    pool: asyncpg.Pool, obj: LocalObject, sc: SyncCandidate, projection: LocalProjection, *,
    repo_path: str, created_by: str, owner_id: Optional[str],
) -> CommitResult:
    from app.services.embeddings import Embedder
    from app.services.procedures import capture_procedure
    from app.services.retrieval_document import (
        RETRIEVAL_DOCUMENT_VERSION,
        build_procedure_retrieval_document,
        retrieval_document_sha256,
    )
    from app.services.v0_gate import V0Violation

    steps = projection.procedure_steps.get(obj.local_id, [])
    domain = obj.topic if obj.topic != "-" else None
    embedder = Embedder()
    retrieval_doc = build_procedure_retrieval_document(
        {"name": obj.name_or_statement, "goal": obj.name_or_statement, "steps": steps, "domain": domain}
    )
    doc_vec, meta = await embedder.embed_one_with_metadata(retrieval_doc, input_type="document")
    try:
        result = await capture_procedure(
            pool, name=obj.name_or_statement, goal=obj.name_or_statement, steps=steps,
            provenance="system_pending_review", domain=domain,
            scope_type="entity" if domain else "global",
            created_by=created_by, owner_id=owner_id, visibility="private",
            embedding=doc_vec, embedding_model_id=meta.model_id, embedding_provider=meta.provider,
            embedding_input_type=meta.input_type, embedding_text_hash=meta.text_sha256,
            retrieval_document=retrieval_doc, retrieval_document_version=RETRIEVAL_DOCUMENT_VERSION,
            retrieval_document_sha256=retrieval_document_sha256(retrieval_doc),
        )
    except V0Violation as exc:
        return CommitResult(sc.candidate_id, "procedure", "refused", reason=str(exc))
    return CommitResult(sc.candidate_id, "procedure", "committed", new_id=result["procedure_id"])


async def _commit_goal(
    pool: asyncpg.Pool, obj: LocalObject, sc: SyncCandidate, *, created_by: str, owner_id: Optional[str],
) -> CommitResult:
    from app.services.goals import GoalQualityRejected, find_or_create_goal
    from app.services.v0_gate import V0Violation

    try:
        result = await find_or_create_goal(
            pool, canonical_name=obj.name_or_statement, scope_type="global",
            provenance="system_pending_review", status="candidate",
            owner_id=owner_id, visibility="private", created_by=created_by,
        )
    except (V0Violation, GoalQualityRejected) as exc:
        return CommitResult(sc.candidate_id, "goal", "refused", reason=str(exc))
    return CommitResult(sc.candidate_id, "goal", "committed", new_id=result["id"])


async def commit_local_sync_items(
    pool: asyncpg.Pool, repo_path: str, selected_ids: list[str], *,
    created_by: str, owner_id: Optional[str] = None, allow_local_only: bool = False,
) -> list[dict]:
    """Re-derives classification for EVERY object currently in the local
    projection (never trusts a caller-supplied classification), then acts
    only on the `selected_ids` that still resolve to something real.
    `selected_ids` not found among the freshly recomputed candidates are
    reported as `refused` (a stale/incorrect id, not silently ignored).

    Never syncs anything not explicitly selected; never promotes a
    LOCAL_ONLY item unless `allow_local_only=True` is passed explicitly by
    the caller (see module docstring for why LOCAL_ONLY is detected via a
    scope-field convention rather than a real visibility field today)."""
    _CID_REPO_PATH.set(repo_path)
    projection = parse_local_projection(repo_path)
    local_generated_at = _local_generated_at(projection.meta)
    selected = set(selected_ids)

    results: list[CommitResult] = []
    seen: set[str] = set()

    async def _handle(obj: LocalObject, sc: SyncCandidate) -> None:
        if sc.candidate_id not in selected:
            return
        seen.add(sc.candidate_id)
        if sc.classification == LOCAL_ONLY and not allow_local_only:
            results.append(CommitResult(sc.candidate_id, sc.object_type, "refused",
                                         reason="LOCAL_ONLY item -- pass allow_local_only=True to sync it explicitly"))
            return
        if sc.classification == ALREADY_SYNCED:
            results.append(CommitResult(sc.candidate_id, sc.object_type, "skipped", reason="already synced"))
            return
        if sc.classification == CONFLICTING:
            results.append(CommitResult(sc.candidate_id, sc.object_type, "skipped",
                                         reason=sc.reason or "backend state changed since preview -- re-run preview_local_sync"))
            return
        if sc.object_type == "claim":
            results.append(await _commit_claim(pool, obj, sc, repo_path=repo_path, created_by=created_by, owner_id=owner_id))
        elif sc.object_type == "procedure":
            results.append(await _commit_procedure(pool, obj, sc, projection, repo_path=repo_path, created_by=created_by, owner_id=owner_id))
        elif sc.object_type == "goal":
            results.append(await _commit_goal(pool, obj, sc, created_by=created_by, owner_id=owner_id))

    for obj in projection.claims:
        await _handle(obj, await _classify_claim(pool, obj, local_generated_at))
    for obj in projection.procedures:
        await _handle(obj, await _classify_procedure(pool, obj, local_generated_at))
    for obj in projection.goals:
        await _handle(obj, await _classify_goal(pool, obj, local_generated_at))

    for missing in selected - seen:
        results.append(CommitResult(missing, "unknown", "refused",
                                     reason="candidate id does not match any object currently in the local projection"))
    return [r.to_dict() for r in results]
