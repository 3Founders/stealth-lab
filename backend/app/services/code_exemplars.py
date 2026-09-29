"""Reference code: the non-trivial spans the code cascade keeps, and how a small model is shown them (migration 128).

Recorded on the PROVENANCE model, like verified solutions (`verified_solutions.py`, migration 124), with one deliberate
difference. A verified solution with a durable location keeps no bytes -- the agent opens the locator itself. A reference-code
span ALWAYS keeps its text, because the reader is a small model that cannot follow a link:

  ingested_artifacts   role 'reference_code'; uri = the commit-pinned blob URL with a line anchor; content_hash = sha256 of the
                       span; content_ref = {sha256, size, inline | locator, exemplar: {...}} -- the bytes, plus the metadata a
                       reader needs (capability, why it is non-trivial, pitfalls, license, attribution)
  procedures           source_locator (granularity 'span', commit, line_start/line_end) and source_artifacts
                       [{artifact_id, role: 'reference_code', execution_allowed: false}] on a ONE-STEP procedure whose goal is
                       the generalizable capability statement -- so it is found by the same retrieval as everything else

A reference is never executable (`ingested_artifacts_exec_role_chk`), the text passes through the known-token redaction before
it is hashed or stored, and only files whose license the policy ALLOWS ever reach this module (the cascade decides that).

`render_for_slm` is the other half of "store it, show it later": a compact, attributed block sized to a token budget, so the
exemplar can be put in front of a small model without eating its context.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

ROLE = "reference_code"
SOURCE_TYPE = "reference_code"
INLINE_MAX = 64 * 1024
MAX_CODE_CHARS = 60_000                 # a span is 15-150 lines; anything larger is not an exemplar
MAX_CAPABILITY_CHARS = 240
CHARS_PER_TOKEN = 3.3                   # code tokenises denser than prose; deliberately pessimistic so blocks fit the budget
# Nearest-neighbour search always returns something, so an off-topic task ("train a neural network") would be handed the closest
# HTTP router. Measured 2026-09-29 on gemini-embedding-2 with 17 exemplars from chi, click and express: the best relevant hit
# scored 0.68-0.73 and the best hit for an off-topic task 0.54, so 0.60 sits in the gap. It is a property of the embedding model
# and the corpus, not a constant of nature: re-measure when either changes (the score travels in `extra["similarity"]`).
DEFAULT_MIN_SIMILARITY = 0.60


@dataclass(frozen=True)
class Exemplar:
    capability: str
    code: str
    language: str
    repository: str
    path: str
    commit: str
    line_start: int
    line_end: int
    license_spdx: str
    why_nontrivial: str = ""
    prerequisites: tuple[str, ...] = ()
    pitfalls: tuple[str, ...] = ()
    difficulty: int = 3
    tags: tuple[str, ...] = ()
    uri: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blob_url(self) -> str:
        return self.uri or blob_url(self.repository, self.commit, self.path, self.line_start, self.line_end)


def blob_url(repository: str, commit: str, path: str, line_start: Optional[int] = None, line_end: Optional[int] = None) -> str:
    anchor = f"#L{line_start}-L{line_end}" if line_start and line_end else ""
    return f"https://github.com/{repository}/blob/{commit}/{path}{anchor}"


def license_notice(license_spdx: str, repository: str) -> str:
    """The attribution a permissive license asks us to keep. MIT/BSD/Apache-2.0 all require the copyright and license notice to
    travel with copies of the code, so the notice is part of every stored exemplar and every rendered block."""
    return (f"From {repository}, licensed {license_spdx}. Keep the upstream copyright and license notice if you reuse or "
            f"adapt this code.")


def _redacted(text: str) -> str:
    from app.services.trace_redaction import _redact_string

    return _redact_string(text)[0]


def exemplar_metadata(ex: Exemplar) -> dict:
    return {
        "capability": ex.capability[:MAX_CAPABILITY_CHARS], "why_nontrivial": ex.why_nontrivial[:600],
        "prerequisites": list(ex.prerequisites)[:5], "pitfalls": list(ex.pitfalls)[:5], "difficulty": int(ex.difficulty),
        "tags": list(ex.tags)[:6], "license": {"spdx": ex.license_spdx, "notice": license_notice(ex.license_spdx, ex.repository)},
        "line_start": ex.line_start, "line_end": ex.line_end, **({"cascade": dict(ex.extra)} if ex.extra else {}),
    }


async def preserve_span(pool: Any, *, procedure_row_id: str, ex: Exemplar, ingestion_context_id: Optional[str] = None,
                        owner_id: Optional[str] = None, visibility: str = "public") -> Optional[dict]:
    """Store the span behind `procedure_row_id` and link it. Returns the source_artifacts ref, or None when there is no code.
    Idempotent on (uri, content_hash). Never overwrites an existing source_locator."""
    from app.services.object_storage import get_store, store_blob
    from app.services.shards import home_pool
    from app.services.source_locators import validate_locator, validate_source_artifacts

    code = _redacted((ex.code or "")[:MAX_CODE_CHARS])
    if not code.strip():
        return None
    data = code.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    content_ref: dict[str, Any]
    store = get_store()
    if store is not None:
        content_ref = dict(await store_blob(pool, store, data, content_type="text/plain"))
    elif len(data) <= INLINE_MAX:
        content_ref = {"sha256": sha, "size": len(data), "inline": code}
    else:
        return None                                   # no store and too large to inline: refuse rather than keep only a hash
    content_ref["exemplar"] = exemplar_metadata(ex)
    uri = ex.blob_url
    row = await pool.fetchrow(
        "INSERT INTO ingested_artifacts (id, source_type, uri, repository, path, \"commit\", content_hash, role, mime_type, "
        " language, byte_size, content_ref, extraction_status, execution_allowed, visibility, owner_id, procedure_row_id, "
        " ingestion_context_id) "
        "VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, 'text/plain', $8, $9, $10::jsonb, 'stored', false, "
        " $11::visibility_level, $12, $13::uuid, $14::uuid) "
        "ON CONFLICT (source_type, uri, content_hash) WHERE role IS NOT NULL DO UPDATE SET last_seen = now() RETURNING id",
        SOURCE_TYPE, uri, ex.repository, ex.path, ex.commit, sha, ROLE, (ex.language or "")[:40] or None, len(data),
        content_ref, visibility, owner_id, str(procedure_row_id), ingestion_context_id)
    ref = validate_source_artifacts([{"artifact_id": str(row["id"]), "path": ex.path, "role": ROLE, "execution_allowed": False,
                                      "note": ex.capability[:MAX_CAPABILITY_CHARS]}])[0]
    locator = validate_locator({"source_id": ex.repository, "uri": uri, "path": ex.path, "commit": ex.commit,
                                "content_hash": sha, "line_start": ex.line_start, "line_end": ex.line_end,
                                "granularity": "span"})
    hp = await home_pool(pool, "procedure", str(procedure_row_id), by_row_id=True)
    await hp.execute(
        "UPDATE procedures SET source_artifacts = COALESCE(source_artifacts, '[]'::jsonb) || $2::jsonb, "
        "source_locator = COALESCE(source_locator, $3::jsonb) WHERE id = $1::uuid",
        str(procedure_row_id), [ref], locator)           # the pools' jsonb codec encodes; never pass pre-encoded JSON
    return ref


def exemplar_ref(source_artifacts: Any) -> Optional[dict]:
    if isinstance(source_artifacts, str):
        try:
            source_artifacts = json.loads(source_artifacts)
        except ValueError:
            return None
    for ref in source_artifacts or []:
        if isinstance(ref, Mapping) and ref.get("role") == ROLE and ref.get("artifact_id"):
            return dict(ref)
    return None


async def resolve(pool: Any, source_artifacts: Any) -> Optional[Exemplar]:
    """The exemplar behind a Procedure's `source_artifacts`, or None. Never fatal: an unreadable store returns None."""
    from app.services.object_storage import get_store

    ref = exemplar_ref(source_artifacts)
    if ref is None:
        return None
    art = await pool.fetchrow(
        "SELECT uri, repository, path, \"commit\", content_hash, language, content_ref FROM ingested_artifacts "
        "WHERE id = $1::uuid AND role = $2 AND t_invalid IS NULL", ref["artifact_id"], ROLE)
    if art is None:
        return None
    content_ref = art["content_ref"]
    if isinstance(content_ref, str):
        content_ref = json.loads(content_ref)
    content_ref = content_ref or {}
    code = content_ref.get("inline")
    if code is None and content_ref.get("locator"):
        store = get_store()
        if store is not None:
            try:
                data = await store.get(content_ref["locator"])
                if hashlib.sha256(data).hexdigest() == art["content_hash"]:
                    code = data.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 -- an unreadable store must not fail the answer
                code = None
    if code is None:
        return None
    meta = content_ref.get("exemplar") or {}
    lic = meta.get("license") or {}
    return Exemplar(
        capability=str(meta.get("capability") or ref.get("note") or ""), code=code, language=art["language"] or "",
        repository=art["repository"] or "", path=art["path"] or "", commit=art["commit"] or "",
        line_start=int(meta.get("line_start") or 0), line_end=int(meta.get("line_end") or 0),
        license_spdx=str(lic.get("spdx") or ""), why_nontrivial=str(meta.get("why_nontrivial") or ""),
        prerequisites=tuple(meta.get("prerequisites") or ()), pitfalls=tuple(meta.get("pitfalls") or ()),
        difficulty=int(meta.get("difficulty") or 3), tags=tuple(meta.get("tags") or ()), uri=art["uri"] or "",
        extra=dict(meta.get("cascade") or {}))


# ------------------------------------------------------------------------------------------------ showing it to a model

def _fence_for(code: str) -> str:
    longest = 0
    run = 0
    for ch in code:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _block(ex: Exemplar, index: int) -> str:
    fence = _fence_for(ex.code)
    lines = [f"### Reference {index}: {ex.capability}",
             f"Source: {ex.repository} @ {ex.commit[:8]} -- {ex.path}:{ex.line_start}-{ex.line_end} ({ex.license_spdx})"]
    if ex.why_nontrivial:
        lines.append(f"Why it is worth studying: {ex.why_nontrivial}")
    if ex.prerequisites:
        lines.append("Assumes: " + "; ".join(ex.prerequisites))
    if ex.pitfalls:
        lines.append("Watch out for: " + "; ".join(ex.pitfalls))
    lines += [f"{fence}{ex.language}", ex.code.rstrip("\n"), fence, license_notice(ex.license_spdx, ex.repository), ""]
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def render_for_slm(exemplars: Sequence[Exemplar], *, token_budget: int = 3000, max_items: int = 3,
                   language: Optional[str] = None) -> str:
    """A prompt-ready block of the best-fitting exemplars, in the given order, within `token_budget`.

    Whole exemplars only: a span is a coherent unit, and half a function is worse than none. An exemplar that does not fit
    the remaining budget is skipped (a shorter one later may still fit). `language` keeps only that language. Returns "" when
    nothing fits, so the caller can omit the section entirely."""
    header = ("The following reference implementations come from well-regarded open-source projects. Study the technique, "
              "adapt it to the task, and do not copy it verbatim; the source and license of each are given.\n\n")
    used = estimate_tokens(header)
    blocks: list[str] = []
    for ex in exemplars:
        if len(blocks) >= max_items:
            break
        if language and ex.language and ex.language.lower() != language.lower():
            continue
        block = _block(ex, len(blocks) + 1)
        cost = estimate_tokens(block)
        if used + cost > token_budget:
            continue
        blocks.append(block)
        used += cost
    return (header + "\n".join(blocks)) if blocks else ""


# --------------------------------------------------------------------------------------------- finding them for a task

async def find_exemplars(pool: Any, embedder: Any, goal_text: str, *, language: Optional[str] = None, limit: int = 3,
                         min_similarity: float = DEFAULT_MIN_SIMILARITY, access_scope: Any = None) -> list[Exemplar]:
    """The reference implementations most relevant to `goal_text`, best first. Never fatal: a broken lookup returns [].

    Same retrieval as everything else: the goal is embedded as a QUERY and compared with the stored procedures' vectors (only
    those in the SAME embedding space -- a vector from another model is not comparable), across shards, under the caller's
    VISIBILITY scope -- the predicate every procedure candidate query uses (`applicability._CANDIDATE_BASE_WHERE`). No tenant
    term: procedures are not tenant-stamped the way knowledge nodes are (a global procedure has tenant_id NULL, so a tenant
    equality would exclude all of them); the tenant boundary for them is the row-level-security backstop. Only procedures
    that carry a `reference_code` artifact are considered. `language` filters after resolution, so a Go exemplar is never
    shown for a Python task."""
    from app.services.access import AccessScope, visibility_predicate
    from app.services.embeddings import to_pgvector
    from app.services.shards import fanout_fetch

    if not (goal_text or "").strip():
        return []
    try:
        vector = await embedder.embed_one(goal_text, input_type="query")
        access = access_scope or AccessScope.anonymous()
        fragment, scope_params = visibility_predicate(access, alias="p", param_index=4)
        rows = await fanout_fetch(
            pool,
            "SELECT p.id, p.source_artifacts, 1 - (p.embedding <=> $1::vector) AS similarity FROM procedures p "
            "WHERE p.t_invalid IS NULL AND p.availability = 'active' AND p.embedding IS NOT NULL "
            "AND p.embedding_model_id = $2 AND p.source_artifacts @> '[{\"role\": \"reference_code\"}]'::jsonb "
            f"AND {fragment} ORDER BY p.embedding <=> $1::vector ASC LIMIT $3",
            to_pgvector(vector), embedder.embedding_model_id(), max(1, limit) * 4, *scope_params)
        found: list[tuple[float, Exemplar]] = []
        for row in sorted(rows, key=lambda r: r["similarity"], reverse=True):
            if row["similarity"] < min_similarity:
                continue
            exemplar = await resolve(pool, row["source_artifacts"])
            if exemplar is None or (language and exemplar.language and exemplar.language.lower() != language.lower()):
                continue
            # the score travels with the exemplar, so a caller can apply its own floor or show its confidence
            found.append((float(row["similarity"]), dataclasses.replace(
                exemplar, extra={**dict(exemplar.extra), "similarity": round(float(row["similarity"]), 4)})))
            if len(found) >= limit:
                break
        return [e for _s, e in found]
    except Exception:  # noqa: BLE001 -- the answer stands without its exemplars
        import logging

        logging.getLogger(__name__).warning("reference-code lookup failed for %r", goal_text[:80], exc_info=True)
        return []
