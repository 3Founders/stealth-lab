"""
SKILL.md -> real procedure ingestion (architecture audit Phase 2,
.scratch/research/global-procedural-memory-architecture-audit-2026-08-30.md
section E; imperative-twirling-plum.md Part A).

DELIBERATELY NOT built on procedure_extraction/'s ExtractionStrategy ABC --
that ABC's extract() takes a ProcedureEvidence (trace-shaped: tool
sequences, observations, an episode). A SKILL.md is a document, not a
recorded run; there is no trace evidence to hand it. This module is a
separate, document-shaped path, same discipline this session's earlier
document-ingestion design (Path 2 vs Path 2's own trace-extraction
sibling) already established -- reuses capture_procedure() directly, the
same real writer submit_procedure()/seed_canonical_coding_procedures.py
already call, rather than forcing a document through a contract built for
traces.

TIER-1 TEMPLATE MATCHING IS DELIBERATELY NOT USED HERE. Confirmed this
session, empirically, against real skill descriptions (not banking's):
real "when to use this" text is scenario/keyword-shaped prose ("use when
the user wants to research a topic"), not comparison bullets ("credit
score >= 765") -- the banking-derived TEMPLATE_REGISTRY would essentially
never match. Preconditions land empty and honest; relevance is entirely
the job of embedding retrieval (search_procedures), same "un-normalizable
stays as agent-facing prose, never fabricated into a structured
predicate" discipline the banking precondition work established.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.services.applicability import find_applicable_procedures
from app.services.embeddings import Embedder
from app.services.procedures import (
    capture_procedure,
    mark_procedure_stale,
    supersede_procedure,
)

NOVELTY_THRESHOLD = 0.90
"""Same value applicability.py's own retrieval code treats as "confidently
the same thing" -- reused, not reinvented, for the admission-controller's
novelty check named in the architecture audit section H."""


class SkillMdParseError(ValueError):
    """The document doesn't have enough real structure to become a
    procedure -- surfaced to the caller, never silently guessed at."""


@dataclass
class ParsedSkill:
    name: str
    description: str
    steps: list[str]
    applies_when: Optional[str] = None
    frontmatter: dict[str, Any] = field(default_factory=dict)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_FRONTMATTER_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
_NUMBERED_STEP_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_BULLET_STEP_RE = re.compile(r"^\s*[-*]\s+(.+)$")
_APPLIES_WHEN_RE = re.compile(
    r"^\s*(?:applies when|use when|when to use)\s*:?\s*(.+)$", re.IGNORECASE,
)


def parse_skill_md(content: str, *, fallback_name: str = "unnamed-skill") -> ParsedSkill:
    """
    Parse a real SKILL.md's real structure: optional YAML-shaped
    frontmatter (`name`/`description` fields, minimal hand-rolled parse --
    no new YAML dependency for two flat string fields), a numbered or
    bulleted step list, and an optional single-line "applies when"/"use
    when" trigger line, kept as PROSE, never forced into a Predicate
    (see module docstring).

    Raises SkillMdParseError if there is no real content to extract from
    (empty document, or a document with no steps and no description) --
    an empty procedure is not a procedure, same rule
    ExtractedProcedure.steps_not_empty already enforces for trace-derived
    procedures.
    """
    frontmatter: dict[str, str] = {}
    body = content
    m = _FRONTMATTER_RE.match(content)
    if m:
        raw_frontmatter, body = m.group(1), m.group(2)
        for line in raw_frontmatter.splitlines():
            fm = _FRONTMATTER_FIELD_RE.match(line)
            if fm:
                frontmatter[fm.group(1).strip().lower()] = fm.group(2).strip().strip('"\'')

    name = frontmatter.get("name") or fallback_name
    description = frontmatter.get("description") or ""

    steps: list[str] = []
    applies_when: Optional[str] = None
    description_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        aw = _APPLIES_WHEN_RE.match(stripped)
        if aw:
            applies_when = aw.group(1).strip()
            continue
        num = _NUMBERED_STEP_RE.match(line)
        if num:
            steps.append(num.group(1).strip())
            continue
        bullet = _BULLET_STEP_RE.match(line)
        if bullet:
            steps.append(bullet.group(1).strip())
            continue
        if not steps:
            # Prose before any step list, not already claimed by
            # frontmatter -- the closest thing to a body description a
            # real SKILL.md offers when frontmatter has none.
            description_lines.append(stripped)

    if not description and description_lines:
        description = " ".join(description_lines[:3])

    if not steps and not description:
        raise SkillMdParseError(
            "no real content found -- no frontmatter description, no body "
            "prose, no numbered/bulleted steps"
        )
    if not steps:
        # A skill can legitimately be a single-paragraph capability with
        # no numbered procedure (many real SKILL.md files are exactly
        # this) -- one honest step carrying the description forward
        # rather than a fabricated breakdown.
        steps = [description]

    return ParsedSkill(
        name=name, description=description or steps[0], steps=steps,
        applies_when=applies_when, frontmatter=frontmatter,
    )


async def check_novelty(
    pool: asyncpg.Pool, embedder: Embedder, goal_text: str,
) -> Optional[dict]:
    """Admission-controller novelty check (architecture audit section H):
    does something >=0.90 similar already exist? Reuses
    find_applicable_procedures directly rather than re-describing the
    mechanism -- returns the existing match if novelty fails, None if
    genuinely novel.

    Refuse-only, and structurally so: this only ever sees ONE candidate
    at a time, before it is written, so it can reject an incoming near-
    duplicate but can never reconcile two rows that are already both
    persisted (e.g. the same capability ingested once via this module
    with provenance='prior_library' and once via organic extraction with
    provenance='system_pending_review'). That later-stage real merge --
    survivor selection off the ticket-13 verification ladder, tombstone
    (never delete) the loser(s), union their evidence_refs/
    source_episode_ids onto the survivor -- is
    procedures.py::merge_duplicate_procedures() /
    run_procedure_dedup_sweep() (Phase 5, memory-substrate map, gap #8),
    a separate, later-stage path built specifically because this
    function cannot do it. This function's own behavior is unchanged."""
    goal_vec = await embedder.embed_one(goal_text, input_type="query")
    matches = await find_applicable_procedures(
        pool, goal_embedding=goal_vec, require_verified=False, limit=1,
    )
    if matches and (matches[0].get("_similarity_score") or 0) >= NOVELTY_THRESHOLD:
        return matches[0]
    return None


async def ingest_skill_md(
    pool: asyncpg.Pool, content: str, *,
    fallback_name: str = "unnamed-skill",
    domain: Optional[str] = None,
    created_by: str = "skill_md_ingestion",
    embedder: Optional[Embedder] = None,
    invariants: Optional[list[dict]] = None,
) -> dict:
    """
    Parse + dedup-check + write, end to end. Returns
    {"status": "captured", "id", "procedure_id"} or
    {"status": "duplicate", "existing_procedure_id", "similarity"} --
    never silently overwrites or skips without saying which happened.

    provenance='prior_library': this codebase's real, existing convention
    for vetted external material entering the substrate (onboarding/seed.py
    uses the same value for the same reason) -- distinct from
    'system_pending_review' (this substrate's own generated/extracted
    content) and 'company_ingested' (the company's own documents).

    invariants: an OPTIONAL, explicit, structured passthrough to
    capture_procedure()'s own `invariants` param (e.g.
    [{"kind": "numeric", "expr": "pandas_version >= 2.0"}]) -- NOT parsed
    out of the document's `applies_when` prose. That line stays raw
    prose by design (module docstring: real "applies when" text is
    scenario-shaped, not a comparison this module can safely turn into a
    z3 expression without risking a fabricated predicate). This
    parameter exists for a caller that already knows the real structured
    invariant a given skill encodes and wants it to land on the
    procedure it captures, same "real structure passes through, prose
    stays prose unless something with actual knowledge supplies
    structure" discipline the rest of this codebase uses.
    """
    parsed = parse_skill_md(content, fallback_name=fallback_name)
    embedder = embedder or Embedder()

    existing = await check_novelty(pool, embedder, parsed.description)
    if existing is not None:
        return {
            "status": "duplicate",
            "existing_procedure_id": existing["procedure_id"],
            "similarity": existing.get("_similarity_score"),
        }

    # REAL BUG this session's own production test found and fixed
    # elsewhere (submit_procedure, backfill_procedure_embeddings.py):
    # a procedure captured with no embedding can be completely starved
    # out of search once the corpus has any real size. Computed here,
    # same input_type="document" convention, BEFORE the write, not after.
    goal_vec = await embedder.embed_one(parsed.description, input_type="document")

    steps = [{"order": i, "goal": s} for i, s in enumerate(parsed.steps)]
    result = await capture_procedure(
        pool, name=parsed.name, goal=parsed.description, steps=steps,
        provenance="prior_library", domain=domain,
        domain_payload={
            "source": "skill_md",
            "applies_when": parsed.applies_when,  # kept as PROSE, never a fabricated Predicate
            "frontmatter": parsed.frontmatter,
        },
        scope_type="entity" if domain else "global",
        created_by=created_by,
        embedding=goal_vec,
        invariants=invariants,
    )
    return {"status": "captured", "id": result["id"], "procedure_id": result["procedure_id"]}


# ---------------------------------------------------------------------------
# Phase 2 ingestion compiler (.scratch/phase2_ingestion_plan.md section 2b).
#
# ingest_skill_md() above is the raw "here is a string, make a procedure"
# entry point. compile_skill_artifact() is the real compiler: it takes a
# SourceArtifact (from app/services/ingestion_sources/), and additionally
#   - abstracts a capability_statement when a model client is available,
#     ABSTAINING rather than fabricating one (brief section 6),
#   - detects an unchanged / changed source by content hash against the
#     ingested_artifacts provenance table -- unchanged is a no-op, changed
#     produces a NEW procedure version via supersede_procedure (brief 10/11),
#   - materializes each parsed step as a real task_nodes row + an
#     OWNS/DECOMPOSES_TO edge, keeping the procedure's own `steps` JSON
#     planner-neutral and untouched (brief section 4),
#   - records a row in ingested_artifacts for provenance/freshness, and
#   - run_skill_ingestion() drives an adapter end to end and writes one
#     ingestion_runs manifest row with the brief section 14 metrics.
#
# None of this is a parallel store: procedures / task_nodes / edges are the
# existing substrate tables; ingested_artifacts + ingestion_runs are
# provenance/telemetry side tables only (migration 32).
# ---------------------------------------------------------------------------

EXTRACTOR_VERSION_DETERMINISTIC = "skill_md_v1"
EXTRACTOR_VERSION_GROUNDED = "skill_md_grounded_v1"

_SKILL_ABSTRACTION_SYSTEM_PROMPT = """You restate a software skill's capability as ONE abstract, \
reusable sentence.

You are given the skill's name, its description, and its steps. Produce exactly one line:
CAPABILITY: <one sentence naming the general skill this represents, with NO specific file names, \
repository names, tool names, package names, command strings, or version numbers -- it must \
describe something that would apply to a DIFFERENT project doing a similar kind of work>

If you cannot produce a genuinely abstract statement, reply with exactly: ABSTAIN
"""

# Light heuristic for brief section 11 / section 12's migrate_deprecated_api
# case: a source that talks about a removed/deprecated API or pins a version
# is a signal that the PRIOR procedure version it replaces is now stale.
_DEPRECATED_SIGNAL_RE = re.compile(
    r"deprecat|removed in|no longer|has no attribute|"
    r"(?:>=|<=|==|<|>)\s*\d|version\s*\d",
    re.IGNORECASE,
)

_BACKTICK_RE = re.compile(r"`([^`]+)`")
_DOTTED_TOKEN_RE = re.compile(r"\b[\w-]+(?:\.[\w-]+)+\b")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class IngestOutcome:
    """The result of compiling one SourceArtifact. `status` is one of
    "captured" (new procedure), "new_version" (source changed -> superseded
    version), "duplicate" (a >=0.90-similar procedure already exists --
    provenance attached, nothing inserted), "unchanged" (byte-identical
    source already ingested -- last_seen bumped, nothing else), or
    "rejected" (the document had no extractable structure)."""

    status: str
    procedure_id: Optional[str] = None
    version_row_id: Optional[str] = None
    task_node_ids: list[str] = field(default_factory=list)
    artifact_id: Optional[str] = None
    capability_abstained: bool = False
    marked_stale: bool = False
    reason: Optional[str] = None


def _slugify(text: str, *, maxlen: int = 80) -> str:
    s = _NON_SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:maxlen].rstrip("-") or "step"


def _artifact_fallback_name(artifact: Any) -> str:
    ref = (artifact.path or artifact.uri or "").replace("\\", "/").rstrip("/")
    parts = [p for p in ref.split("/") if p]
    if not parts:
        return "unnamed-skill"
    last = parts[-1]
    if last.lower() == "skill.md" and len(parts) >= 2:
        # `.../research/SKILL.md` -> the meaningful name is the folder.
        return parts[-2]
    return re.sub(r"\.skill\.md$", "", last, flags=re.IGNORECASE) or "unnamed-skill"


def _concrete_tokens(parsed: ParsedSkill) -> set[str]:
    """Tokens that must NOT appear in an abstracted capability statement --
    backtick-quoted spans and dotted identifiers (df.append, pandas 2.0,
    foo/bar.py) drawn from the skill's own name and steps. Same "did you
    leak something concrete" discipline procedure_extraction's V4 applies,
    scoped to what this document itself mentions."""
    tokens: set[str] = set()
    haystack = " ".join([parsed.name, parsed.description, *parsed.steps])
    for m in _BACKTICK_RE.finditer(haystack):
        tokens.add(m.group(1).strip())
    for m in _DOTTED_TOKEN_RE.finditer(haystack):
        tokens.add(m.group(0))
    return {t for t in tokens if len(t) >= 3}


def _abstract_capability(
    client: Any, parsed: ParsedSkill, *,
    model: str = "gemma-4-31B-it", temperature: float = 0.2,
) -> Optional[str]:
    """One focused model call for an abstract capability_statement, or None.

    Returns None -- never a fabricated string -- on any of: no client, an
    API error, an explicit ABSTAIN, a response with no CAPABILITY line, or
    a statement that echoes a concrete token from the skill's own text
    (brief section 6: "prefer ABSTAIN/rejection over hallucinated
    structure"). The caller records capability_abstained=True and the
    procedure's capability_statement column stays NULL."""
    if client is None:
        return None
    user_prompt = (
        f"Name: {parsed.name}\n"
        f"Description: {parsed.description}\n"
        "Steps:\n" + "\n".join(f"- {s}" for s in parsed.steps)
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SKILL_ABSTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=160,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 -- a model call's own failure degrades
        return None
    if text.strip() == "ABSTAIN":
        return None
    capability: Optional[str] = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("CAPABILITY:"):
            capability = line[len("CAPABILITY:"):].strip()
            break
    if not capability:
        return None
    lowered = capability.lower()
    if any(tok.lower() in lowered for tok in _concrete_tokens(parsed)):
        return None
    return capability


def _mentions_deprecated_api(parsed: ParsedSkill) -> bool:
    hay = " ".join([parsed.description, parsed.applies_when or "", *parsed.steps])
    return bool(_DEPRECATED_SIGNAL_RE.search(hay))


def _source_provenance(artifact: Any) -> dict:
    return {
        "source_type": artifact.source_type,
        "uri": artifact.uri,
        "repository": artifact.repository,
        "path": artifact.path,
        "commit": artifact.commit,
        "content_hash": artifact.content_hash,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def _domain_payload(artifact: Any, parsed: ParsedSkill) -> dict:
    return {
        "source": _source_provenance(artifact),
        "applies_when": parsed.applies_when,  # PROSE, never a fabricated Predicate
        "frontmatter": parsed.frontmatter,
    }


async def _write_task_nodes(
    pool: asyncpg.Pool, *, procedure_row_id: str, steps: list[str], created_by: str,
) -> list[str]:
    """One task_nodes row per parsed step + an OWNS/DECOMPOSES_TO edge from
    the procedure version row to each. Brief section 4: the external skill's
    step list becomes real Task nodes in the EXISTING table; the procedure's
    own `steps` JSON is left planner-neutral and is not touched here.

    Edge shape follows this codebase's established base-enum + custom-subtype
    convention (hierarchy.py's OWNS/PARENT_OF, dedup.py's SUPERSEDES/
    DUPLICATE_OF) -- edge_type is the real enum value 'OWNS', the specific
    relation rides custom_edge_type='DECOMPOSES_TO'."""
    task_node_ids: list[str] = []
    for i, step_text in enumerate(steps):
        row = await pool.fetchrow(
            "INSERT INTO task_nodes (id, name, description, provenance, created_by) "
            "VALUES (gen_random_uuid(), $1, $2, 'prior_library', $3) RETURNING id",
            _slugify(step_text), step_text, created_by,
        )
        task_node_id = str(row["id"])
        task_node_ids.append(task_node_id)
        await pool.execute(
            "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
            "VALUES ('OWNS', 'DECOMPOSES_TO', $1::uuid, 'procedures', $2::uuid, 'task_nodes', "
            "$3::jsonb, 'prior_library', now(), now(), $4)",
            procedure_row_id, task_node_id, {"order": i}, created_by,
        )
    return task_node_ids


async def _write_artifact_row(
    pool: asyncpg.Pool, artifact: Any, *,
    run_id: Optional[str], procedure_id: Optional[str],
    procedure_row_id: Optional[str], extractor_version: str,
    owner_id: Optional[str] = None,
) -> str:
    row = await pool.fetchrow(
        "INSERT INTO ingested_artifacts (id, source_type, uri, repository, path, "
        "\"commit\", content_hash, extractor_version, procedure_id, procedure_row_id, "
        "run_id, first_seen, last_seen, owner_id) "
        "VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8::uuid, $9::uuid, "
        "$10::uuid, now(), now(), $11) RETURNING id",
        artifact.source_type, artifact.uri, artifact.repository, artifact.path,
        artifact.commit, artifact.content_hash, extractor_version,
        procedure_id, procedure_row_id, run_id, owner_id,
    )
    return str(row["id"])


async def compile_skill_artifact(
    pool: asyncpg.Pool,
    artifact: Any,
    *,
    embedder: Optional[Embedder] = None,
    client: Any = None,
    domain: Optional[str] = None,
    run_id: Optional[str] = None,
    created_by: str = "skill_md_ingestion",
    invariants: Optional[list[dict]] = None,
    owner_id: Optional[str] = None,
) -> IngestOutcome:
    """Compile one SourceArtifact into the substrate. See the section
    comment above for the full contract. Never raises for an
    unstructured document -- returns status="rejected" instead."""
    try:
        parsed = parse_skill_md(
            artifact.content, fallback_name=_artifact_fallback_name(artifact),
        )
    except SkillMdParseError as exc:
        return IngestOutcome(status="rejected", reason=str(exc))

    embedder = embedder or Embedder()
    capability_statement = _abstract_capability(client, parsed)
    capability_abstained = capability_statement is None
    extractor_version = (
        EXTRACTOR_VERSION_GROUNDED if capability_statement is not None
        else EXTRACTOR_VERSION_DETERMINISTIC
    )

    # --- staleness / version detection against the provenance table ---
    exact = await pool.fetchrow(
        "SELECT id, procedure_id FROM ingested_artifacts "
        "WHERE source_type = $1 AND uri = $2 AND content_hash = $3 "
        "AND t_invalid IS NULL ORDER BY first_seen DESC LIMIT 1",
        artifact.source_type, artifact.uri, artifact.content_hash,
    )
    if exact is not None:
        await pool.execute(
            "UPDATE ingested_artifacts SET last_seen = now() WHERE id = $1::uuid",
            str(exact["id"]),
        )
        return IngestOutcome(
            status="unchanged",
            procedure_id=str(exact["procedure_id"]) if exact["procedure_id"] else None,
            artifact_id=str(exact["id"]),
            capability_abstained=capability_abstained,
        )

    prior_art = await pool.fetchrow(
        "SELECT id, procedure_id, procedure_row_id, content_hash FROM ingested_artifacts "
        "WHERE source_type = $1 AND uri = $2 AND procedure_row_id IS NOT NULL "
        "AND t_invalid IS NULL ORDER BY first_seen DESC LIMIT 1",
        artifact.source_type, artifact.uri,
    )

    steps_json = [{"order": i, "goal": s} for i, s in enumerate(parsed.steps)]

    if prior_art is not None:
        changed_fields: dict[str, Any] = {
            "name": parsed.name,
            "goal": parsed.description,
            "steps": steps_json,
            "parameter_schema": {"source": "skill_md"},
            "domain_payload": _domain_payload(artifact, parsed),
            # A superseding version is fresh even if the one it replaces was
            # flagged stale below -- supersede_procedure carries `staleness`
            # forward otherwise.
            "staleness": "fresh",
        }
        if capability_statement is not None:
            changed_fields["capability_statement"] = capability_statement
        if invariants is not None:
            changed_fields["invariants"] = invariants

        superseded = await supersede_procedure(
            pool,
            prior_row_id=str(prior_art["procedure_row_id"]),
            changed_fields=changed_fields,
            superseded_by=created_by,
            reason=(
                f"source content changed for {artifact.uri}: "
                f"{str(prior_art['content_hash'])[:12]} -> {artifact.content_hash[:12]}"
            ),
        )
        if superseded is not None:
            marked_stale = False
            if _mentions_deprecated_api(parsed):
                await mark_procedure_stale(
                    pool,
                    procedure_row_id=str(prior_art["procedure_row_id"]),
                    reason=(
                        "replaced by a newer source revision that references "
                        "updated or deprecated APIs"
                    ),
                    detected_by=created_by,
                )
                marked_stale = True

            task_node_ids = await _write_task_nodes(
                pool, procedure_row_id=superseded["id"],
                steps=parsed.steps, created_by=created_by,
            )
            artifact_id = await _write_artifact_row(
                pool, artifact, run_id=run_id,
                procedure_id=superseded["procedure_id"],
                procedure_row_id=superseded["id"],
                extractor_version=extractor_version, owner_id=owner_id,
            )
            return IngestOutcome(
                status="new_version",
                procedure_id=superseded["procedure_id"],
                version_row_id=superseded["id"],
                task_node_ids=task_node_ids,
                artifact_id=artifact_id,
                capability_abstained=capability_abstained,
                marked_stale=marked_stale,
            )
        # prior row already gone (concurrent merge/supersede) -- fall
        # through and treat this as a fresh capture.

    # --- novelty / dedup ---
    existing = await check_novelty(pool, embedder, parsed.description)
    if existing is not None:
        provenance_entry = {
            "source": _source_provenance(artifact),
            "note": "additional source observed for an already-ingested procedure",
        }
        await pool.execute(
            "UPDATE procedures SET evidence_refs = evidence_refs || $2::jsonb, "
            "updated_at = now() WHERE procedure_id = $1::uuid AND t_invalid IS NULL",
            str(existing["procedure_id"]), [provenance_entry],
        )
        artifact_id = await _write_artifact_row(
            pool, artifact, run_id=run_id,
            procedure_id=str(existing["procedure_id"]),
            procedure_row_id=None,
            extractor_version=extractor_version, owner_id=owner_id,
        )
        return IngestOutcome(
            status="duplicate",
            procedure_id=str(existing["procedure_id"]),
            artifact_id=artifact_id,
            capability_abstained=capability_abstained,
            reason=f"similarity {existing.get('_similarity_score')}",
        )

    # --- fresh capture ---
    goal_vec = await embedder.embed_one(
        capability_statement or parsed.description, input_type="document",
    )
    result = await capture_procedure(
        pool, name=parsed.name, goal=parsed.description, steps=steps_json,
        provenance="prior_library", domain=domain,
        domain_payload=_domain_payload(artifact, parsed),
        scope_type="entity" if domain else "global",
        scope_entity_id=domain,
        created_by=created_by,
        embedding=goal_vec,
        invariants=invariants,
        owner_id=owner_id,
    )
    if capability_statement is not None:
        await pool.execute(
            "UPDATE procedures SET capability_statement = $2 WHERE id = $1::uuid",
            result["id"], capability_statement,
        )
    task_node_ids = await _write_task_nodes(
        pool, procedure_row_id=result["id"], steps=parsed.steps, created_by=created_by,
    )
    artifact_id = await _write_artifact_row(
        pool, artifact, run_id=run_id,
        procedure_id=result["procedure_id"], procedure_row_id=result["id"],
        extractor_version=extractor_version, owner_id=owner_id,
    )
    return IngestOutcome(
        status="captured",
        procedure_id=result["procedure_id"],
        version_row_id=result["id"],
        task_node_ids=task_node_ids,
        artifact_id=artifact_id,
        capability_abstained=capability_abstained,
    )


_ACCEPTED_STATUSES = frozenset({"captured", "new_version"})


async def run_skill_ingestion(
    pool: asyncpg.Pool,
    adapter: Any,
    *,
    embedder: Optional[Embedder] = None,
    client: Any = None,
    domain: Optional[str] = None,
    created_by: str = "skill_md_ingestion",
    invariants: Optional[list[dict]] = None,
    owner_id: Optional[str] = None,
) -> dict:
    """Drive one source adapter end to end and record a manifest.

    Writes an ingestion_runs row up front, compiles every artifact the
    adapter discovers (a per-artifact failure is counted, never aborts the
    batch -- same discipline as ingestion_jobs.process_pending_jobs), then
    finalizes the row with the brief section 14 metrics. Returns
    {"run_id", "metrics", "outcomes"}."""
    embedder = embedder or Embedder()
    source_spec = {
        "adapter": type(adapter).__name__,
        "source_type": getattr(adapter, "source_type", None),
        "domain": domain,
    }
    run_row = await pool.fetchrow(
        "INSERT INTO ingestion_runs (started_at, source_spec, created_by) "
        "VALUES (now(), $1::jsonb, $2) RETURNING run_id",
        source_spec, created_by,
    )
    run_id = str(run_row["run_id"])

    metrics = {
        "sources_seen": 1,
        "artifacts_seen": 0,
        "candidates": 0,
        "accepted": 0,
        "duplicates": 0,
        "unchanged": 0,
        "stale": 0,
        "rejected": 0,
        "errors": 0,
    }
    outcomes: list[IngestOutcome] = []

    for ref in adapter.discover():
        metrics["artifacts_seen"] += 1
        try:
            artifact = adapter.fetch(ref)
            outcome = await compile_skill_artifact(
                pool, artifact, embedder=embedder, client=client, domain=domain,
                run_id=run_id, created_by=created_by, invariants=invariants,
                owner_id=owner_id,
            )
        except Exception as exc:  # noqa: BLE001 -- one bad artifact must not
            # sink the run; the failure is counted and surfaced.
            metrics["errors"] += 1
            outcomes.append(IngestOutcome(status="error", reason=repr(exc)))
            continue

        outcomes.append(outcome)
        # `candidates` counts every artifact that reached the compiler,
        # rejected ones included (brief section 14: candidates == accepted +
        # duplicates + rejected + ...); only a fetch/compile exception is
        # excluded, and that is what `errors` is for. So:
        #   artifacts_seen == candidates + errors, and
        #   candidates == accepted + duplicates + unchanged + rejected.
        metrics["candidates"] += 1
        if outcome.status == "rejected":
            metrics["rejected"] += 1
        elif outcome.status in _ACCEPTED_STATUSES:
            metrics["accepted"] += 1
        elif outcome.status == "duplicate":
            metrics["duplicates"] += 1
        elif outcome.status == "unchanged":
            metrics["unchanged"] += 1
        if outcome.marked_stale:
            metrics["stale"] += 1

    await pool.execute(
        "UPDATE ingestion_runs SET finished_at = now(), metrics = $2::jsonb WHERE run_id = $1::uuid",
        run_id, metrics,
    )
    return {"run_id": run_id, "metrics": metrics, "outcomes": outcomes}
