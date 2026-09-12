"""
Phase 14 backfill: find every legacy templated document Claim ("The source
X documents a procedure for: Y" -- the bug app/services/claim_extraction.py
replaces) and, where the original document's normalized blocks are still
available, re-extract real Claims from them via the SAME real extraction
path skill_ingestion.py now uses. Never deletes a legacy row; never
fabricates a replacement when re-extraction cannot run.

IDENTIFICATION (deterministic, not a text-content heuristic)
    Every legacy templated Claim was written with the EXACT structured
    triple `properties->>'predicate' = 'documents_procedure_for'`
    (`skill_ingestion.py`'s old `_emit_document_screening_and_claim`,
    removed). That triple was NEVER written by any other code path in
    this codebase (grep-confirmed) -- an exact JSONB equality match finds
    every legacy row with zero false positives and zero false negatives,
    unlike matching on the statement's own English wording.

IDEMPOTENT / RESUMABLE
    A legacy row already processed by a prior run of this script carries
    `properties['legacy_backfill_status']` ('reextracted' or
    'no_source_available') and is skipped on the next run. Re-running
    after a partial/interrupted run only touches rows still missing that
    marker.

WHAT "RE-EXTRACT" MEANS HERE
    This codebase does not store an ingested document's raw original
    bytes anywhere -- only its content_hash and (since B16) its
    structurally-normalized `artifact_blocks` rows. Re-extraction here
    means: pull those already-persisted blocks (real, previously-derived
    data -- not fabricated) for the SAME ingestion_context_id the legacy
    Claim was captured under, and run them through the SAME
    `claim_extraction.extract_claim_candidates` the live ingestion path
    uses. A legacy Claim whose ingestion_context_id has no persisted
    blocks (pre-B16 rows) CANNOT be re-extracted -- it is still
    deprecated (see below), just with no replacement, exactly per this
    backfill's own "never hallucinate a replacement" rule.

LIFECYCLE (no new status invented)
    A legacy row is never deleted (FK-safe: procedure_claim_refs /
    evidence / claim_sources may already reference it, and its history
    must survive). It is deprecated via the EXISTING `truth_state='OUT'`
    mechanism (claims.py's own real TMS field -- "no longer current
    belief") plus an audit marker in `properties`, exactly the same
    "supersede by tombstone, never delete" idiom this codebase already
    uses everywhere else (procedures.staleness, evidence.t_invalid, ...).
    Real replacement Claims (when re-extraction succeeds) are captured as
    ordinary NEW Claims via the same `persist_claim_candidate` production
    path -- never written directly.

USAGE (from backend/)
    python scripts/backfill_claim_extraction.py                 # dry-run report only
    python scripts/backfill_claim_extraction.py --apply          # actually write
    python scripts/backfill_claim_extraction.py --apply --limit 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from app.db.session import create_pool
from app.services.artifact_blocks import Block
from app.services.claim_extraction import extract_claim_candidates, persist_claim_candidate
from app.services.embeddings import Embedder
from app.services.procedure_claim_refs import add_procedure_claim_ref

LEGACY_PREDICATE = "documents_procedure_for"
CREATED_BY = "backfill_claim_extraction"

_STATE_DIR = Path(__file__).resolve().parent / ".backfill_state"
_REPORT_PATH = _STATE_DIR / "claim_extraction_backfill.jsonl"


def _record(row: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


async def _find_legacy_claims(pool, *, limit: Optional[int]) -> list[dict]:
    query = (
        "SELECT id, name, properties, ingestion_context_id "
        "FROM knowledge_nodes "
        "WHERE node_type = 'claim' "
        "AND properties->>'predicate' = $1 "
        "AND properties->>'legacy_backfill_status' IS NULL "
        "AND t_invalid IS NULL "
        "ORDER BY t_created ASC"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    rows = await pool.fetch(query, LEGACY_PREDICATE)
    return [dict(r) for r in rows]


async def _load_blocks_for_context(pool, ingestion_context_id: Optional[str]) -> list[Block]:
    if ingestion_context_id is None:
        return []
    rows = await pool.fetch(
        "SELECT block_index, block_type, depth, text, source_start, source_end, "
        "anchor, parent_block_id "
        "FROM artifact_blocks WHERE ingestion_context_id = $1::uuid ORDER BY block_index",
        ingestion_context_id,
    )
    # `parent_index` (an in-list index) is only used by artifact_blocks.py's
    # OWN persistence path to resolve parent_block_id at write time -- for
    # re-extraction we only need block_index/block_type/text, so None here
    # is correct, not a missing value.
    return [
        Block(
            block_index=r["block_index"], block_type=r["block_type"], depth=r["depth"],
            text=r["text"], source_start=r["source_start"], source_end=r["source_end"],
            anchor=r["anchor"], parent_index=None,
        )
        for r in rows
    ]


async def _referencing_rows(pool, claim_id: str) -> dict[str, int]:
    """Informational only (Phase 14 item 2/3): counts, never a gate on
    whether to deprecate -- deprecation via t_invalid/truth_state is safe
    regardless of referencing rows (nothing here is deleted)."""
    refs = await pool.fetchval(
        "SELECT count(*) FROM procedure_claim_refs WHERE claim_id = $1::uuid AND t_invalid IS NULL",
        claim_id,
    )
    evidence = await pool.fetchval(
        "SELECT count(*) FROM evidence WHERE target_type = 'claim' AND target_id = $1::uuid "
        "AND t_invalid IS NULL",
        claim_id,
    )
    sources = await pool.fetchval(
        "SELECT count(*) FROM claim_sources WHERE claim_id = $1::uuid", claim_id,
    )
    return {"procedure_claim_refs": refs, "evidence": evidence, "claim_sources": sources}


async def _deprecate_legacy_claim(
    pool, claim_id: str, properties: dict, *, status: str, new_claim_ids: list[str], dry_run: bool,
) -> None:
    updated_props = {
        **properties,
        "legacy_backfill_status": status,
        "legacy_backfill_replacement_claim_ids": new_claim_ids,
    }
    if dry_run:
        return
    await pool.execute(
        "UPDATE knowledge_nodes SET properties = $2::jsonb "
        "WHERE id = $1::uuid AND node_type = 'claim'",
        claim_id, updated_props,
    )
    # Real, existing TMS mechanism (claims.py's own truth_state field):
    # this specific templated statement is no longer current belief. The
    # row (and its history/references) is never deleted.
    await pool.execute(
        "UPDATE knowledge_nodes SET properties = jsonb_set(properties, '{truth_state}', '\"OUT\"') "
        "WHERE id = $1::uuid AND node_type = 'claim'",
        claim_id,
    )


async def _find_procedure_refs(pool, claim_id: str) -> list[dict]:
    return [
        dict(r) for r in await pool.fetch(
            "SELECT procedure_id, procedure_version FROM procedure_claim_refs "
            "WHERE claim_id = $1::uuid AND t_invalid IS NULL",
            claim_id,
        )
    ]


async def run(
    *, apply: bool, limit: Optional[int], client=None, model: str = "gemma-4-31B-it",
    pool=None,
) -> dict:
    owns_pool = pool is None
    if owns_pool:
        pool = await create_pool(os.environ["DATABASE_URL"])
    embedder = Embedder()
    counts = {
        "found": 0, "reextracted": 0, "reextracted_zero_claims": 0,
        "no_source_available": 0, "new_claims_created": 0,
    }
    try:
        legacy = await _find_legacy_claims(pool, limit=limit)
        counts["found"] = len(legacy)
        for row in legacy:
            claim_id = str(row["id"])
            props = row["properties"] if isinstance(row["properties"], dict) else json.loads(row["properties"])
            refs_before = await _referencing_rows(pool, claim_id)
            blocks = await _load_blocks_for_context(pool, row["ingestion_context_id"])
            candidates = extract_claim_candidates(client, blocks, model=model) if blocks else []

            new_claim_ids: list[str] = []
            if candidates:
                procedure_refs = await _find_procedure_refs(pool, claim_id)
                for candidate in candidates:
                    new_id = None
                    if not apply:
                        pass  # dry-run: report the candidate, write nothing
                    else:
                        new_id = await persist_claim_candidate(
                            pool, candidate,
                            ingestion_context_id=row["ingestion_context_id"],
                            created_by=CREATED_BY, embedder=embedder,
                            equivalence_client=client, equivalence_model=model,
                        )
                    if new_id is not None:
                        new_claim_ids.append(new_id)
                        if candidate.suggested_procedure_role is not None:
                            for pref in procedure_refs:
                                await add_procedure_claim_ref(
                                    pool, procedure_id=str(pref["procedure_id"]),
                                    procedure_version=int(pref["procedure_version"]),
                                    claim_id=new_id, role=candidate.suggested_procedure_role,
                                    ref_origin="derived", extractor_version=CREATED_BY,
                                    ingestion_context_id=row["ingestion_context_id"],
                                    created_by=CREATED_BY,
                                )
                status = "reextracted"
                counts["reextracted"] += 1
                counts["new_claims_created"] += len(new_claim_ids)
            elif not blocks:
                status = "no_source_available"
                counts["no_source_available"] += 1
            else:
                # Blocks existed but extraction found nothing worth keeping
                # (no client configured, or the document genuinely has no
                # independently meaningful proposition) -- distinct from
                # "we had nothing to re-extract FROM at all".
                status = "reextracted_zero_claims"
                counts["reextracted_zero_claims"] += 1

            await _deprecate_legacy_claim(
                pool, claim_id, props, status=status, new_claim_ids=new_claim_ids, dry_run=not apply,
            )
            _record({
                "claim_id": claim_id, "legacy_statement": row["name"], "status": status,
                "referencing_rows_before": refs_before, "candidates_found": len(candidates),
                "new_claim_ids": new_claim_ids, "applied": apply,
            })
    finally:
        if owns_pool:
            await pool.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run report only).")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N legacy claims.")
    args = parser.parse_args()

    counts = asyncio.run(run(apply=args.apply, limit=args.limit))
    print(f"{'APPLIED' if args.apply else 'DRY RUN'} -- {json.dumps(counts, indent=2)}")
    print(f"Per-row detail: {_REPORT_PATH}")
    if not args.apply:
        print("Re-run with --apply to actually write these changes.")


if __name__ == "__main__":
    main()
