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
from typing import Any, Optional

import asyncpg

from app.services.applicability import find_applicable_procedures
from app.services.embeddings import Embedder
from app.services.procedures import capture_procedure

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
