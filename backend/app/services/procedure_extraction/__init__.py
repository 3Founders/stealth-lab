"""
Procedure extraction (memory-substrate map): the public API. Turns an
episode of real work into a real `procedures` row -- the producer
capture_procedure()'s own docstring names as missing ("a future
extraction step's job at capture time, not this function's").

extract_procedure() is the ONLY function most callers need. Everything
else in this package (evidence/schema/derive/strategies/validators/
registry, plus the top-level slot_binders.py) is internal wiring this
function orchestrates -- see this package's individual modules for why
each seam exists.

WHAT THIS PASS DOES NOT DO, stated rather than implied: nothing consumes
an extracted procedure yet. Retrieval wiring (find_applicable_procedures
already exists and is untouched), executor adapters (_seed_plan/
_verify_precondition), and an approval UI are all separate, later work.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.applicability import _scope_matches
from app.services.procedure_extraction.evidence import EvidenceSource, ProcedureEvidence
from app.services.procedure_extraction.registry import select_extractor
from app.services.procedure_extraction.schema import ExtractedProcedure, ExtractionResult
from app.services.procedure_extraction.strategies import (
    DeterministicExtractor,
    ExtractionStrategy,
    GroundedHybridExtractor,
)
from app.services.procedure_extraction.validators import ValidationContext, validate
from app.services.procedures import capture_procedure

_DETERMINISTIC_TAG = "deterministic_v1@1"


def _evidence_tokens(evidence: ProcedureEvidence) -> frozenset[str]:
    """What V4 (capability abstraction) scans capability_statement
    against -- every concrete file path/command string this episode's
    own observations actually mention. Deliberately drawn from the
    evidence, not from the extracted procedure itself: the check is
    "did you leak something FROM THIS EPISODE", not a generic
    specificity heuristic."""
    tokens: set[str] = set()
    for obs in evidence.observations:
        props = obs.get("properties") or {}
        for key in ("file_path", "command"):
            value = props.get(key)
            if isinstance(value, str) and value:
                tokens.add(value)
    return frozenset(tokens)


async def extract_procedure(
    pool: asyncpg.Pool,
    evidence_source: EvidenceSource,
    *,
    client: Any = None,
    extractor_scope: Optional[dict] = None,
    repo_root: Optional[str] = None,
    entry_seed_files: Optional[list[str]] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    dry_run: bool = False,
) -> ExtractionResult:
    """
    The full pipeline: collect evidence, select an extractor (registry,
    falling back to the seeded deterministic baseline), run it, validate
    the result, persist via capture_procedure() -- UNCHANGED, called
    exactly as any other caller would -- plus the migration-20 columns
    capture_procedure() does not yet know about (approval_status,
    capability_statement, extracted_by), set via one follow-up UPDATE
    rather than modifying that function's own signature.

    V5 (evidence sufficiency) is checked BEFORE calling a strategy at
    all, not just on the strategy's output -- refusing to even attempt
    extraction from a failed or observation-free episode, per that
    rule's own docstring: extraction from nothing produces a fabricated
    procedure, which is worse than none.

    `dry_run=True`: runs source -> strategy -> validators and returns
    the candidate WITHOUT persisting -- the loop needed to iterate on
    an extractor's prompt/config without polluting the procedures table.
    """
    evidence = await evidence_source.collect()

    if evidence.outcome != "success" or not evidence.has_observations():
        return ExtractionResult(
            validation_failures=[
                "V5_evidence_sufficiency: refusing to extract from a "
                f"{'failed' if evidence.outcome != 'success' else 'observation-free'} episode",
            ],
        )

    strategy, extracted_by, allowed_binders = await _select_strategy(
        pool, client=client, extractor_scope=extractor_scope,
    )

    extracted: ExtractedProcedure = await strategy.extract(
        pool, evidence, repo_root=repo_root, entry_seed_files=entry_seed_files,
    )

    from app.services.environment_probe import PROBE_PREDICATE_VOCABULARY
    ctx = ValidationContext(
        probe_vocabulary=PROBE_PREDICATE_VOCABULARY,
        evidence_tokens=_evidence_tokens(evidence),
        allowed_binders=allowed_binders,
    )
    failures = validate(extracted, ctx)
    if failures:
        return ExtractionResult(
            extracted=extracted, extracted_by=extracted_by,
            validation_failures=[str(f) for f in failures],
        )

    if dry_run:
        return ExtractionResult(extracted=extracted, extracted_by=extracted_by)

    result = await capture_procedure(
        pool,
        name=extracted.name, goal=extracted.goal,
        steps=[s.model_dump() for s in extracted.steps],
        parameter_schema={
            "slots": [s.model_dump() for s in extracted.slots],
            "extraction_method": extracted_by,
        },
        preconditions=[p.model_dump() for p in extracted.preconditions],
        scope=extracted.scope,
        exclusions=extracted.exclusions,
        failure_conditions=extracted.failure_conditions,
        source_episode_ids=[evidence.episode_id] if evidence.episode_id else None,
        owner_id=owner_id, visibility=visibility,
    )

    # The migration-20 columns capture_procedure() does not carry --
    # left as an intentional follow-up UPDATE rather than widening that
    # function's own signature, so every existing caller/test of
    # capture_procedure() is untouched by this pass.
    await pool.execute(
        "UPDATE procedures SET approval_status = 'proposed', "
        "capability_statement = $2, extracted_by = $3 WHERE id = $1::uuid",
        result["id"], extracted.capability_statement, extracted_by,
    )

    return ExtractionResult(
        procedure_id=result["procedure_id"], version_row_id=result["id"],
        extracted_by=extracted_by, extracted=extracted,
    )


async def _select_strategy(
    pool: asyncpg.Pool, *, client: Any, extractor_scope: Optional[dict],
) -> tuple[ExtractionStrategy, str, frozenset[str]]:
    """Registry-driven selection with an always-available fallback --
    see registry.select_extractor()'s own docstring for why None means
    'use the seeded deterministic baseline', not an error."""
    row = await select_extractor(pool, current_scope=extractor_scope or {})
    if row is None:
        return DeterministicExtractor(), _DETERMINISTIC_TAG, frozenset({"literal"})

    tag = f"{row['name']}@{row['version']}"
    config = row.get("config") or {}
    allowed_binders = frozenset(config.get("allowed_binders") or []) or frozenset(
        {"call_graph_reachable", "import_deps", "related_tests", "relevant_symbols", "literal"}
    )
    if row["kind"] == "deterministic" or client is None:
        return DeterministicExtractor(), tag, allowed_binders

    strategy = GroundedHybridExtractor(
        client, model=config.get("model", "gemma-4-31B-it"),
        temperature=config.get("temperature", 0.2),
    )
    return strategy, tag, allowed_binders


async def evaluate_extractor(
    pool: asyncpg.Pool,
    extractor_id: str,
    golden_episode_ids: list[str],
    *,
    client: Any,
    build_evidence_source,
) -> dict:
    """
    What keeps 'improvable over time' from meaning 'driftable'. Runs a
    CANDIDATE extractor over a golden set of past episodes with
    dry_run=True (validators run, nothing persists), and reports
    per-rule pass rates -- the report a human reads before flipping an
    extractor's `enabled` bit, per registry.py's own review flow.

    `build_evidence_source(episode_id) -> EvidenceSource`: caller-
    supplied, since this module does not assume how a golden episode id
    maps back to a real EvidenceSource (that mapping is
    application-specific -- a session_id lookup, a stored fixture,
    etc).

    Offline comparison only -- this proves the candidate produces
    well-formed output on real past episodes, NOT that its procedures
    will succeed when reused (that is registry.extractor_stats()'s
    downstream_success_rate, necessarily delayed). Both numbers matter;
    this function only ever answers the first.
    """
    from app.services.environment_probe import PROBE_PREDICATE_VOCABULARY

    row = await pool.fetchrow(
        "SELECT name, version, kind, config FROM procedure_extractors WHERE id = $1::uuid",
        extractor_id,
    )
    if row is None:
        raise ValueError(f"no procedure_extractors row for id={extractor_id!r}")

    if row["kind"] == "deterministic":
        strategy: ExtractionStrategy = DeterministicExtractor()
    else:
        config = row["config"] or {}
        strategy = GroundedHybridExtractor(
            client, model=config.get("model", "gemma-4-31B-it"),
            temperature=config.get("temperature", 0.2),
        )

    per_rule_failures: dict[str, int] = {}
    attempted = 0
    well_formed = 0

    for episode_id in golden_episode_ids:
        source = build_evidence_source(episode_id)
        evidence = await source.collect()
        if evidence.outcome != "success" or not evidence.has_observations():
            continue
        attempted += 1

        extracted = await strategy.extract(pool, evidence)
        ctx = ValidationContext(
            probe_vocabulary=PROBE_PREDICATE_VOCABULARY,
            evidence_tokens=_evidence_tokens(evidence),
            allowed_binders=frozenset(
                {"call_graph_reachable", "import_deps", "related_tests", "relevant_symbols", "literal"}
            ),
        )
        failures = validate(extracted, ctx)
        if not failures:
            well_formed += 1
        for f in failures:
            per_rule_failures[f.rule] = per_rule_failures.get(f.rule, 0) + 1

    return {
        "extractor": f"{row['name']}@{row['version']}",
        "golden_set_size": len(golden_episode_ids),
        "attempted": attempted,
        "well_formed": well_formed,
        "well_formed_rate": (well_formed / attempted) if attempted else None,
        "failures_by_rule": per_rule_failures,
    }
