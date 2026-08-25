"""
ClaimFamily resolver v0 (Band 2.6 / ROADMAP item 6; spec v4 §10).

A Claim Family represents PROPOSITIONAL IDENTITY, not merely topic or
sentence similarity (§10's own words, and schema.md's ClaimFamily entry:
"Similarity is candidate generation, not identity"). This module resolves,
for one claim, whether an existing same-project family already holds its
proposition -- and attaches it when one does.

Architecture mirrors capability.py's precedent: a PURE decision core that
is provable offline, plus a thin adoptable DB boundary. Nothing existing
changes behavior until a caller wires resolve_claim_family() into the
claim-capture path; that wiring is deliberately a separate change.

The v0 cascade, mapped onto §10's pipeline:

    candidate claims            -> block_candidates(): PROJECT SCOPE is the
                                   hard blocking key (v0 is project-scoped;
                                   cross-project families are Band 4's LSH
                                   wave). Similarity ORDERS and CAPS the
                                   survivors -- it can never admit a row the
                                   scope gate rejected, and it can never
                                   decide identity.
        ↓
    semantic similarity         -> advisory rank only (stored-embedding
                                   cosine when available, lexical Jaccard
                                   otherwise). Never consulted below this
                                   line.
        ↓
    proposition matching        -> decide() stage "proposition_match":
                                   normalized subject|predicate|object|type
                                   equality. THE identity gate. Fails closed:
                                   a claim with no structured proposition
                                   merges with nothing.
        ↓
    entity / ontology matching  -> decide() stage "ontology_overlap": same
                                   predicate with strictly nested
                                   subject/object token sets yields
                                   generalizes/specializes; >=2 of 3
                                   normalized slots shared yields
                                   related_family. Deliberately
                                   conservative (see _partial_decision).
        ↓
    condition matching          -> decide() stage "condition_match":
                                   normalized condition-set equality decides
                                   same_family vs related_family among
                                   propositional identities (§10: "one
                                   family IF proposition AND conditions
                                   align").
        ↓
    outcome matching            -> NOT IMPLEMENTED in v0, honestly: claims
                                   carry no outcome field yet (outcomes live
                                   on Evidence). Nothing here pretends to
                                   check one.
        ↓
    scope / domain              -> enforced UPSTREAM by blocking, not per
                                   decision: a cross-project row is never a
                                   candidate at all.
        ↓
    contradiction check         -> dominates everything: a candidate joined
                                   to the subject by a CONTRADICTS edge --
                                   or a negation flip (exactly one side
                                   proposition_type='negative' over an
                                   otherwise-shared proposition) -- is
                                   distinct NO MATTER how similar it is.
                                   Evaluated first in code because its
                                   verdict dominates; §10 places it last in
                                   the diagram, but last-vs-first changes
                                   nothing about any outcome except making
                                   the dominance accidental instead of
                                   explicit.
        ↓
    family decision             -> same_family | related_family |
                                   generalizes | specializes | distinct.

Persistence rides the EXISTING generic tables -- NO migration, same idiom
as claims.py/failure_capture.py (node_type is TEXT; custom_edge_type
carries what the edge_type enum lacks):

  - A family is a knowledge_node with node_type='claim_family',
    properties.canonical_key = the anchor proposition's normalized
    "subject|predicate|object|type", scoped to the project pair. Created
    lazily on the first same_family merge; mechanically produced, so it
    stamps provenance='system_pending_review' (migration 21's value for
    exactly this) and extractor_version=RESOLVER_VERSION per the V0 gate's
    derived-object rule.
  - Membership is an edge hub --OWNS--> claim with custom_edge_type=
    'FAMILY_MEMBER' (graph_ingest.py's container idiom; OWNS is the
    semantically closest enum carrier, the truth is in custom_edge_type,
    exactly how claims.py carries CONTRADICTS under SUPERSEDES).
  - related/generalizes/specializes verdicts are RETURNED, not persisted,
    in v0: family-graph edges between hubs are Band 4 territory alongside
    cross-project resolution. Identity is persisted; resemblance is only
    reported.

TMS interaction: truth_state='OUT' claims are dead for family purposes --
they neither anchor nor join families (CORE-A's 2.7 made retrieval honor
OUT; a family is retrieval context, so it honors it too). History stays
queryable as always; nothing is invalidated here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

import asyncpg

from app.services.access import AccessScope, visibility_predicate
from app.services.v0_gate import V0Violation, validate_scope

CREATED_BY = "claim_family_resolver"

# V0 derived-object stamp (validate_provenance(derived=True) demands one;
# swapping resolver versions must be as visible as swapping extractors).
RESOLVER_VERSION = "claim_family_resolver@v0"

FAMILY_NODE_TYPE = "claim_family"
MEMBERSHIP_EDGE_TYPE = "OWNS"
MEMBERSHIP_CUSTOM_TYPE = "FAMILY_MEMBER"

# v0 is project-scoped (ROADMAP Band 2 item 6). Hard constant, not config:
# widening scope is Band 4's LSH wave, not a deployment knob.
PROJECT_SCOPE_TYPE = "project"

# Candidate cap AFTER scope filtering and ranking. Blocking exists to keep
# the O(candidates) proposition cascade bounded; the cap bounds rank, never
# identity -- a same-proposition claim ranked 21th joins late or not at all
# rather than the gate ever being skipped.
BLOCK_LIMIT = 20

SAME_FAMILY = "same_family"
RELATED_FAMILY = "related_family"
GENERALIZES = "generalizes"
SPECIALIZES = "specializes"
DISTINCT = "distinct"

VERDICTS = (SAME_FAMILY, RELATED_FAMILY, GENERALIZES, SPECIALIZES, DISTINCT)

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACES = re.compile(r"\s+")


def normalize(text: Optional[str]) -> str:
    """Case/punctuation/article/whitespace-insensitive form of a slot.

    Deterministic and dumb on purpose: §10's "normalized proposition" for
    v0. "The Data augmentation improves Image Classification." and
    "data augmentation improves image classification" reduce to the same
    string; meaning-bearing words are never touched.
    """
    if not text:
        return ""
    lowered = _NON_ALNUM.sub(" ", text.lower())
    lowered = _ARTICLES.sub(" ", lowered)
    return _SPACES.sub(" ", lowered).strip()


def _tokens(text: Optional[str]) -> frozenset[str]:
    return frozenset(t for t in normalize(text).split(" ") if t)


@dataclass(frozen=True)
class Proposition:
    """A claim's machine-operable content: schema.md's Claim.proposition,
    carried by knowledge_nodes' structured columns (migration 21) with the
    properties JSONB as fallback for writers that predate it."""

    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    proposition_type: Optional[str] = None
    conditions: tuple[str, ...] = ()

    @property
    def is_structured(self) -> bool:
        return bool(self.subject or self.predicate or self.object)

    @property
    def canonical_key(self) -> str:
        return "|".join(
            (
                normalize(self.subject),
                normalize(self.predicate),
                normalize(self.object),
                normalize(self.proposition_type),
            )
        )


def proposition_from_claim_row(row: Mapping[str, Any]) -> Proposition:
    """Structured columns first, properties JSONB as fallback (claims.py
    writes both shapes); conditions ride properties['conditions']."""
    props = row.get("properties") or {}
    raw_conditions = props.get("conditions")
    conditions: tuple[str, ...] = ()
    if isinstance(raw_conditions, (list, tuple)):
        conditions = tuple(str(c) for c in raw_conditions if str(c).strip())
    return Proposition(
        subject=row.get("subject") or props.get("subject"),
        predicate=row.get("predicate") or props.get("predicate"),
        object=row.get("object") or props.get("object"),
        proposition_type=row.get("proposition_type") or props.get("proposition_type"),
        conditions=conditions,
    )


@dataclass(frozen=True)
class CandidateClaim:
    claim_id: str
    proposition: Proposition
    # Advisory ONLY. Recorded on decisions for traceability; consulted by
    # nothing that can flip a verdict. A 0.99-similarity candidate that
    # fails the proposition gate is distinct, full stop.
    similarity: float = 0.0
    contradicts_subject: bool = False


@dataclass(frozen=True)
class FamilyDecision:
    verdict: str
    candidate_id: Optional[str]
    # Cascade stages actually evaluated, in order -- tests pin the order
    # and the teeth (a verdict must never appear without its stages).
    stages: tuple[str, ...] = ()
    reason: str = ""


def _jaccard(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    ta = _tokens(a.get("statement") or a.get("name"))
    tb = _tokens(b.get("statement") or b.get("name"))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def block_candidates(
    subject_row: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = BLOCK_LIMIT,
) -> list[CandidateClaim]:
    """Candidate generation. Project scope is the HARD filter; similarity
    only ranks and caps what scope already admitted.

    Rows whose scope pair differs from the subject's are invisible here --
    including scope-less rows, because nothing is implicit-global (the V0
    rule). A global-scoped twin of the subject's proposition is NOT a v0
    candidate; cross-scope families are Band 4's entire reason to exist.

    Raises V0Violation if the SUBJECT itself carries no scope: resolving
    an unscoped claim would silently widen v0 past its project mandate.
    """
    subject_scope = (subject_row.get("scope_type"), subject_row.get("scope_entity_id"))
    validate_scope(subject_scope[0], subject_scope[1])

    subject_id = subject_row.get("id")
    candidates: list[CandidateClaim] = []
    for row in rows:
        if row.get("id") == subject_id:
            continue
        if row.get("t_invalid") is not None:
            continue
        props = row.get("properties") or {}
        if props.get("truth_state") == "OUT":
            continue  # TMS readability: dead claims neither anchor nor join
        if (row.get("scope_type"), row.get("scope_entity_id")) != subject_scope:
            continue  # the hard filter; similarity has no vote here
        candidates.append(
            CandidateClaim(
                claim_id=str(row["id"]),
                proposition=proposition_from_claim_row(row),
                similarity=float(row["_similarity"]) if row.get("_similarity") is not None else _jaccard(subject_row, row),
                contradicts_subject=bool(row.get("_contradicts_subject")),
            )
        )
    candidates.sort(key=lambda c: (-c.similarity, c.claim_id))
    return candidates[:limit]


def _contradiction_reason(subject: Proposition, candidate: CandidateClaim) -> Optional[str]:
    if candidate.contradicts_subject:
        return "CONTRADICTS edge between subject and candidate"
    if bool(subject.proposition_type and normalize(subject.proposition_type) == "negative") != bool(
        candidate.proposition.proposition_type
        and normalize(candidate.proposition.proposition_type) == "negative"
    ):
        return "negation flip: exactly one side is a negative proposition"
    return None


def _partial_decision(
    subject: Proposition, candidate: CandidateClaim, stages: tuple[str, ...]
) -> FamilyDecision:
    """Reached when proposition identity failed. Ontology-overlap ladder,
    deliberately conservative: v0 prefers MISSING a related-family link
    over inventing one (false families poison downstream belief pooling;
    a missed link just waits for better extraction)."""
    s_slots = (normalize(subject.subject), normalize(subject.predicate), normalize(subject.object))
    c_slots = (
        normalize(candidate.proposition.subject),
        normalize(candidate.proposition.predicate),
        normalize(candidate.proposition.object),
    )

    negation = _contradiction_reason(subject, candidate)
    if negation:
        return FamilyDecision(DISTINCT, candidate.claim_id, stages + ("contradiction_check",), negation)

    if s_slots[1] and s_slots[1] == c_slots[1]:
        # Strict token nesting on one slot decides direction (more tokens =
        # more qualifiers = narrower proposition, §10's hierarchy example:
        # "augmentation improves learning" generalizes "image augmentation
        # improves image classification"). First nested slot wins; MIXED
        # directions across slots are genuinely ambiguous and fall through
        # to the conservative related/distinct answers below.
        for a, b in (_tokens(subject.subject), _tokens(candidate.proposition.subject)), (
            _tokens(subject.object),
            _tokens(candidate.proposition.object),
        ):
            if a and b and (a < b or b < a):
                verdict = SPECIALIZES if b < a else GENERALIZES
                return FamilyDecision(verdict, candidate.claim_id, stages + ("ontology_overlap",), "same predicate, strictly nested subject/object")
    if sum(1 for a, b in zip(s_slots, c_slots) if a and a == b) >= 2:
        return FamilyDecision(RELATED_FAMILY, candidate.claim_id, stages + ("ontology_overlap",), ">=2 of 3 normalized slots shared")
    return FamilyDecision(DISTINCT, candidate.claim_id, stages + ("ontology_overlap",), "proposition differs; no conservative overlap")


def decide(subject: Proposition, candidate: CandidateClaim) -> FamilyDecision:
    """One candidate through the v0 cascade. See module docstring for the
    §10 mapping; outcome matching is honestly absent (claims carry no
    outcome field), scope/domain was enforced upstream by blocking."""
    # Dominant contradiction check (see module docstring on ordering).
    negation = _contradiction_reason(subject, candidate)
    if negation:
        return FamilyDecision(DISTINCT, candidate.claim_id, ("contradiction_check",), negation)

    stages = ["contradiction_check", "proposition_match"]
    if subject.canonical_key != candidate.proposition.canonical_key:
        return _partial_decision(subject, candidate, tuple(stages))

    if not candidate.proposition.is_structured:
        # Fail closed: statement-only claims (no extracted SPO) merge with
        # nothing in v0 -- textual identity without structure is exactly the
        # similarity-as-identity mistake §10 forbids.
        return FamilyDecision(DISTINCT, candidate.claim_id, tuple(stages), "no structured proposition; v0 refuses statement-only identity")

    stages.append("condition_match")
    s_conditions = {normalize(c) for c in subject.conditions}
    c_conditions = {normalize(c) for c in candidate.proposition.conditions}
    if s_conditions == c_conditions:
        return FamilyDecision(SAME_FAMILY, candidate.claim_id, tuple(stages), "normalized proposition and conditions align")
    return FamilyDecision(RELATED_FAMILY, candidate.claim_id, tuple(stages), "proposition matches; conditions diverge")


# ----------------------------------------------------------------------------
# DB boundary -- thin, adoptable, generic tables only (no migration).
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyResolution:
    claim_id: str
    verdict: str
    family_node_id: Optional[str]
    attached: bool
    decisions: tuple[FamilyDecision, ...]
    candidates_considered: int


async def resolve_claim_family(
    pool: asyncpg.Pool,
    *,
    claim_id: str,
    access_scope: Optional[AccessScope] = None,
    attach: bool = True,
    limit: int = BLOCK_LIMIT,
) -> Optional[FamilyResolution]:
    """Resolve (and by default attach) one claim to its project-scoped
    ClaimFamily.

    Returns None when the subject cannot be resolved AT ALL: unknown id,
    not a claim, invalidated (t_invalid), or truth_state OUT. None is a
    contract answer, not an error -- callers routing capture-time
    resolution treat it as "nothing to do".

    Raises V0Violation when the subject claim predates scope stamping
    (fresh-start rows always carry one; legacy rows must not be silently
    resolved against an invented scope).
    """
    scope = access_scope or AccessScope.unrestricted()
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)

    async with pool.acquire() as conn:
        subject_row = await conn.fetchrow(
            f"SELECT id, node_type, t_invalid, scope_type, scope_entity_id, "
            f"owner_id, visibility, subject, predicate, object, proposition_type, "
            f"properties, embedding FROM knowledge_nodes "
            f"WHERE id = $1::uuid AND {vis_sql}",
            claim_id,
            *vis_params,
        )
        if subject_row is None:
            return None
        if subject_row["node_type"] != "claim" or subject_row["t_invalid"] is not None:
            return None
        if (subject_row["properties"] or {}).get("truth_state") == "OUT":
            return None

        # Fresh-start rows carry a scope pair; anything else is loud.
        validate_scope(subject_row["scope_type"], subject_row["scope_entity_id"])

        # Blocking SELECT: scope pair + liveness + TMS + visibility are all
        # hard WHERE terms; similarity is only the ORDER BY (and only when
        # the subject itself is embedded -- a cold embedder must never turn
        # into a silent full-table scan ordering).
        params: list[Any] = [claim_id, *vis_params]
        scope_idx = len(params) + 1
        params.extend([subject_row["scope_type"], subject_row["scope_entity_id"]])
        if subject_row["embedding"] is not None:
            order = f"ORDER BY embedding <=> ${scope_idx + 2}::vector NULLS LAST"
            params.append(subject_row["embedding"])
        else:
            order = "ORDER BY t_valid DESC"
        limit_idx = len(params) + 1
        params.append(limit)
        candidate_rows = await conn.fetch(
            f"SELECT id, name, subject, predicate, object, proposition_type, properties, "
            f"scope_type, scope_entity_id FROM knowledge_nodes "
            f"WHERE node_type = 'claim' AND t_invalid IS NULL AND id <> $1::uuid "
            f"AND scope_type = ${scope_idx} AND scope_entity_id = ${scope_idx + 1} "
            f"AND COALESCE(properties->>'truth_state', 'IN') <> 'OUT' "
            f"AND {vis_sql} {order} LIMIT ${limit_idx}",
            *params,
        )

        contradicts_pairs: set[tuple[str, str]] = set()
        cand_ids = [r["id"] for r in candidate_rows]
        if cand_ids:
            for r in await conn.fetch(
                "SELECT source_id, target_id FROM edges WHERE t_invalid IS NULL "
                "AND custom_edge_type = 'CONTRADICTS' AND ("
                "(source_id = $1::uuid AND target_id = ANY($2::uuid[])) OR "
                "(target_id = $1::uuid AND source_id = ANY($2::uuid[])))",
                claim_id,
                cand_ids,
            ):
                contradicts_pairs.add((str(r["source_id"]), str(r["target_id"])))

        def _mark(row: dict[str, Any]) -> dict[str, Any]:
            row["_contradicts_subject"] = (str(row["id"]), str(claim_id)) in contradicts_pairs or (
                str(claim_id),
                str(row["id"]),
            ) in contradicts_pairs
            return row

        subject_prop = proposition_from_claim_row(subject_row)
        candidates = block_candidates(
            dict(subject_row), [_mark(dict(r)) for r in candidate_rows], limit=limit
        )
        decisions = tuple(decide(subject_prop, c) for c in candidates)
        winner = next((d for d in decisions if d.verdict == SAME_FAMILY), None)

        family_node_id: Optional[str] = None
        attached = False
        if winner is not None and attach:
            # The hub lookup's placeholders start at $4 ($1..$3 below), so
            # the visibility predicate is generated for THAT index -- never
            # string-replace placeholders into a fragment built for another.
            hub_vis_sql, hub_vis_params = visibility_predicate(scope, param_index=4)
            async with conn.transaction():
                hub_id = await conn.fetchval(
                    f"SELECT id FROM knowledge_nodes WHERE node_type = '{FAMILY_NODE_TYPE}' "
                    f"AND t_invalid IS NULL AND scope_type = $1 AND scope_entity_id = $2 "
                    f"AND properties->>'canonical_key' = $3 AND {hub_vis_sql} LIMIT 1",
                    subject_row["scope_type"],
                    subject_row["scope_entity_id"],
                    subject_prop.canonical_key,
                    *hub_vis_params,
                )
                if hub_id is None:
                    display = " ".join(
                        filter(None, (subject_prop.subject, subject_prop.predicate, subject_prop.object))
                    )
                    hub_props = {
                        "canonical_key": subject_prop.canonical_key,
                        "proposition": {
                            "subject": subject_prop.subject,
                            "predicate": subject_prop.predicate,
                            "object": subject_prop.object,
                            "type": subject_prop.proposition_type,
                        },
                        "conditions": list(subject_prop.conditions),
                        "member_count": 0,
                    }
                    hub_id = await conn.fetchval(
                        f"INSERT INTO knowledge_nodes (node_type, name, properties, "
                        f"embedding, extractor_version, scope_type, scope_entity_id, "
                        f"owner_id, visibility, created_by, provenance) VALUES ("
                        f"'{FAMILY_NODE_TYPE}', $1, $2, $3::vector, '{RESOLVER_VERSION}', "
                        f"$4, $5, $6, $7::visibility_level, '{CREATED_BY}', "
                        f"'system_pending_review') RETURNING id",
                        display[:200],
                        hub_props,
                        subject_row["embedding"],
                        subject_row["scope_type"],
                        subject_row["scope_entity_id"],
                        subject_row["owner_id"],
                        subject_row["visibility"],
                    )
                already = await conn.fetchval(
                    f"SELECT 1 FROM edges WHERE edge_type = '{MEMBERSHIP_EDGE_TYPE}' "
                    f"AND custom_edge_type = '{MEMBERSHIP_CUSTOM_TYPE}' AND t_invalid IS NULL "
                    f"AND source_id = $1::uuid AND target_id = $2::uuid",
                    hub_id,
                    claim_id,
                )
                if already is None:
                    await conn.execute(
                        f"INSERT INTO edges (edge_type, custom_edge_type, source_id, "
                        f"source_table, target_id, target_table, properties, created_by, "
                        f"provenance) VALUES ('{MEMBERSHIP_EDGE_TYPE}', "
                        f"'{MEMBERSHIP_CUSTOM_TYPE}', $1::uuid, 'knowledge_nodes', "
                        f"$2::uuid, 'knowledge_nodes', {{}}, '{CREATED_BY}', "
                        f"'system_pending_review')",
                        hub_id,
                        claim_id,
                    )
                    await conn.execute(
                        "UPDATE knowledge_nodes SET properties = properties || "
                        "jsonb_build_object('member_count', "
                        "(COALESCE(properties->>'member_count', '0')::int + 1)) "
                        "WHERE id = $1::uuid",
                        hub_id,
                    )
                    attached = True
            family_node_id = str(hub_id)

        return FamilyResolution(
            claim_id=str(claim_id),
            verdict=winner.verdict if winner is not None else (decisions[0].verdict if decisions else DISTINCT),
            family_node_id=family_node_id,
            attached=attached,
            decisions=decisions,
            candidates_considered=len(candidates),
        )
