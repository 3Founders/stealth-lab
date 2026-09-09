"""
Backfill procedure embeddings + the canonical retrieval representation +
human-facing display metadata, in one auditable pass over live rows.

Three modes, all resumable and idempotent:

  --representation   (default)
      For every live procedure whose stored retrieval_document_version is
      not the current RETRIEVAL_DOCUMENT_VERSION -- or whose canonical
      document text has changed -- rebuild build_procedure_retrieval_document(),
      embed it via the configured provider chain, validate the dimension,
      and persist the new embedding + provider stamps + document + version
      + sha256 in ONE UPDATE. A row whose embedding call fails keeps its
      OLD vector and its OLD version (never marked current) and is written
      to a failure log. Re-running the script retries only those.

  --embed-missing
      The original narrow job: embed live rows that have NO embedding at
      all (still using the canonical representation). Useful right after a
      capture path that deferred embedding.

  --display-metadata
      Rebuild display_name / display_description / display_metadata_version
      deterministically for every live row not on the current
      DISPLAY_METADATA_VERSION. No embedding, no provider spend. Rows the
      deterministic recipe cannot describe usefully are stamped
      DISPLAY_METADATA_FALLBACK_VERSION and listed in the report so a human
      can fix the source.

Usage (from backend/):
    python scripts/backfill_procedure_embeddings.py --representation [--limit N] [--dry-run]
    python scripts/backfill_procedure_embeddings.py --display-metadata [--limit N] [--dry-run]
    python scripts/backfill_procedure_embeddings.py --embed-missing [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Rows per provider embedding call. ~64 short canonical docs is a few
# thousand tokens -- comfortably under the configured per-minute budget --
# so 2.5k rows become tens of HTTP calls, not thousands.
_EMBED_BATCH = 64


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from app.db.session import create_pool
from app.services.embeddings import Embedder, EmbeddingError, to_pgvector
from app.services.procedure_display import (
    DISPLAY_METADATA_FALLBACK_VERSION,
    DISPLAY_METADATA_VERSION,
    build_display_metadata,
)
from app.services.retrieval_document import (
    RETRIEVAL_DOCUMENT_VERSION,
    build_procedure_retrieval_document,
    retrieval_document_sha256,
)

_STATE_DIR = Path(__file__).resolve().parent / ".backfill_state"
_FAIL_LOG = _STATE_DIR / f"{RETRIEVAL_DOCUMENT_VERSION}.failed.jsonl"
_DISPLAY_REPORT = _STATE_DIR / f"{DISPLAY_METADATA_VERSION}.display_quality.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(path: Path, row: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


async def _dependency_refs_by_procedure(pool, procedure_ids: list) -> dict[str, list[str]]:
    if not procedure_ids:
        return {}
    rows = await pool.fetch(
        "SELECT procedure_id, dependency_ref FROM procedure_dependencies "
        "WHERE procedure_id = ANY($1::uuid[])",
        procedure_ids,
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(str(r["procedure_id"]), []).append(r["dependency_ref"])
    return out


# --------------------------------------------------------------------------
# --representation
# --------------------------------------------------------------------------
async def backfill_representation(
    *, dry_run: bool = False, limit: int | None = None, force: bool = False,
    pool=None, embedder=None, provider: str | None = None,
) -> dict:
    """Re-embed every live procedure off the current canonical retrieval
    document. Resumable: selects only rows not already on
    RETRIEVAL_DOCUMENT_VERSION (unless --force). Atomic per row. Failures
    are logged and leave the prior state intact.

    `pool` / `embedder` are injectable for testing; created from config
    when omitted."""
    owns_pool = pool is None
    pool = pool or await create_pool(min_size=1, max_size=4)
    stats = {"selected": 0, "reembedded": 0, "unchanged_text": 0, "skipped_dry": 0, "failed": 0}
    try:
        embedder = embedder or Embedder(rate_limit_pool=pool, provider=provider)
        # The configured target space: a row whose stored embedding_model_id
        # is not THIS is on a different (incompatible) vector space and owes
        # a re-embed, even if its retrieval_document_version already matches.
        # embedding_model_id() reads settings only, no IO.
        target_model_id = embedder.embedding_model_id()

        where = "t_invalid IS NULL"
        if not force:
            where += (
                " AND (retrieval_document_version IS NULL "
                f"OR retrieval_document_version <> '{RETRIEVAL_DOCUMENT_VERSION}' "
                "OR embedding IS NULL "
                "OR embedding_model_id IS DISTINCT FROM $1)"
            )
        sql = f"SELECT * FROM procedures WHERE {where} ORDER BY t_created"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = await (pool.fetch(sql) if force else pool.fetch(sql, target_model_id))
        stats["selected"] = len(rows)
        print(f"{len(rows)} live procedure(s) selected for representation backfill "
              f"-> {RETRIEVAL_DOCUMENT_VERSION} @ {target_model_id}")

        deps = await _dependency_refs_by_procedure(
            pool, [r["procedure_id"] for r in rows]
        )

        # Build every doc first, split into the no-op fast path (canonical
        # text unchanged -> just stamp the version) and the real re-embed
        # set, then embed the latter in provider batches so 2.5k rows are
        # tens of HTTP calls, not thousands.
        pending: list[tuple[dict, str, str]] = []  # (proc, doc, sha)
        for r in rows:
            proc = dict(r)
            doc = build_procedure_retrieval_document(
                proc, dependencies=deps.get(str(proc["procedure_id"])),
            )
            sha = retrieval_document_sha256(doc)
            if (
                not force
                and proc.get("retrieval_document_sha256") == sha
                and proc.get("embedding") is not None
                and proc.get("embedding_model_id") == target_model_id
            ):
                # canonical text unchanged AND the vector is already in the
                # target space -> stamp the version, don't re-embed.
                stats["unchanged_text"] += 1
                if not dry_run:
                    await pool.execute(
                        "UPDATE procedures SET retrieval_document = $2, "
                        "retrieval_document_version = $3, retrieval_document_sha256 = $4 "
                        "WHERE id = $1",
                        proc["id"], doc, RETRIEVAL_DOCUMENT_VERSION, sha,
                    )
                continue
            if dry_run:
                stats["skipped_dry"] += 1
                continue
            pending.append((proc, doc, sha))

        if dry_run:
            print(f"  [dry-run] would re-embed {stats['skipped_dry']}, "
                  f"stamp-only {stats['unchanged_text']}")

        model_id = embedder.embedding_model_id()
        provider = embedder._configured_provider()  # noqa: SLF001
        for start in range(0, len(pending), _EMBED_BATCH):
            chunk = pending[start:start + _EMBED_BATCH]
            texts = [doc for _proc, doc, _sha in chunk]
            try:
                vectors = await embedder.embed(texts, input_type="document")
                if len(vectors) != len(texts) or (
                    vectors and len(vectors[0]) != embedder.dimension
                ):
                    raise EmbeddingError(
                        f"batch shape {len(vectors)}x"
                        f"{len(vectors[0]) if vectors else 0} != "
                        f"{len(texts)}x{embedder.dimension}"
                    )
            except Exception as exc:  # noqa: BLE001 -- isolate the culprit
                print(f"  batch {start}-{start+len(chunk)} failed ({str(exc)[:80]}); "
                      f"retrying row-by-row")
                vectors = []
                for _proc, doc, _sha in chunk:
                    try:
                        one = await embedder.embed([doc], input_type="document")
                        if not one or len(one[0]) != embedder.dimension:
                            raise EmbeddingError(
                                f"dimension {len(one[0]) if one else 0} != {embedder.dimension}"
                            )
                        vectors.append(one[0])
                    except Exception as row_exc:  # noqa: BLE001
                        vectors.append(None)
                        stats["failed"] += 1
                        _record(_FAIL_LOG, {
                            "ts": _now(), "id": str(_proc["id"]),
                            "procedure_id": str(_proc["procedure_id"]),
                            "name": _proc["name"], "error": repr(row_exc)[:400],
                        })
                        print(f"    FAILED (kept old vector + version): {_proc['name']} "
                              f"-- {str(row_exc)[:100]}")

            for (proc, doc, sha), vec in zip(chunk, vectors):
                if vec is None:
                    continue
                # One statement per row: the new vector and the representation
                # version it was built from land together, or not at all.
                await pool.execute(
                    "UPDATE procedures SET embedding = $2::vector, embedding_model_id = $3, "
                    "embedding_dim = $4, embedding_provider = $5, embedding_input_type = $6, "
                    "embedding_text_hash = $7, retrieval_document = $8, "
                    "retrieval_document_version = $9, retrieval_document_sha256 = $10, "
                    "domain_payload = jsonb_set(COALESCE(domain_payload, '{}'::jsonb), "
                    "'{embedding}', $11::jsonb, true), updated_at = now() "
                    "WHERE id = $1",
                    proc["id"], to_pgvector(vec), model_id, embedder.dimension,
                    provider, "document", _text_sha256(doc), doc,
                    RETRIEVAL_DOCUMENT_VERSION, sha,
                    json.dumps({"provider": provider, "model_id": model_id,
                                "dimension": embedder.dimension, "input_type": "document",
                                "text_sha256": _text_sha256(doc)}),
                )
                stats["reembedded"] += 1
            print(f"  {stats['reembedded']}/{len(pending)} re-embedded", flush=True)

        print(f"\nrepresentation backfill: {json.dumps(stats)}")
        if stats["failed"]:
            print(f"failures logged to {_FAIL_LOG}")
        return stats
    finally:
        if owns_pool:
            await pool.close()


# --------------------------------------------------------------------------
# --embed-missing  (the original narrow job, on the canonical representation)
# --------------------------------------------------------------------------
async def backfill_missing_embeddings(*, dry_run: bool = False, limit: int | None = None) -> dict:
    pool = await create_pool(min_size=1, max_size=4)
    stats = {"selected": 0, "embedded": 0, "failed": 0}
    try:
        sql = (
            "SELECT * FROM procedures WHERE t_invalid IS NULL AND embedding IS NULL "
            "ORDER BY t_created"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = await pool.fetch(sql)
        stats["selected"] = len(rows)
        print(f"{len(rows)} live procedure(s) with no embedding")
        deps = await _dependency_refs_by_procedure(pool, [r["procedure_id"] for r in rows])
        embedder = Embedder(rate_limit_pool=pool)
        for r in rows:
            proc = dict(r)
            doc = build_procedure_retrieval_document(
                proc, dependencies=deps.get(str(proc["procedure_id"])),
            )
            if dry_run:
                print(f"  [dry-run] would embed: {proc['name']}")
                continue
            try:
                vec, meta = await embedder.embed_one_with_metadata(doc, input_type="document")
                if len(vec) != embedder.dimension:
                    raise EmbeddingError(f"dimension {len(vec)} != {embedder.dimension}")
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                _record(_FAIL_LOG, {"ts": _now(), "id": str(proc["id"]),
                                    "name": proc["name"], "error": repr(exc)[:400]})
                print(f"  FAILED: {proc['name']} -- {str(exc)[:120]}")
                continue
            await pool.execute(
                "UPDATE procedures SET embedding = $2::vector, embedding_model_id = $3, "
                "embedding_dim = $4, embedding_provider = $5, embedding_input_type = $6, "
                "embedding_text_hash = $7, retrieval_document = $8, "
                "retrieval_document_version = $9, retrieval_document_sha256 = $10, "
                "updated_at = now() WHERE id = $1",
                proc["id"], to_pgvector(vec), meta.model_id, meta.dimension, meta.provider,
                meta.input_type, meta.text_sha256, doc, RETRIEVAL_DOCUMENT_VERSION,
                retrieval_document_sha256(doc),
            )
            stats["embedded"] += 1
        print(f"\nembed-missing: {json.dumps(stats)}")
        return stats
    finally:
        await pool.close()


# --------------------------------------------------------------------------
# --display-metadata
# --------------------------------------------------------------------------
async def backfill_display_metadata(
    *, dry_run: bool = False, limit: int | None = None, force: bool = False, pool=None,
) -> dict:
    owns_pool = pool is None
    pool = pool or await create_pool(min_size=1, max_size=4)
    stats = {"selected": 0, "updated": 0, "fallback": 0, "skipped_dry": 0}
    try:
        where = "t_invalid IS NULL"
        if not force:
            where += (
                " AND (display_metadata_version IS NULL OR display_name IS NULL "
                f"OR display_metadata_version NOT IN "
                f"('{DISPLAY_METADATA_VERSION}', '{DISPLAY_METADATA_FALLBACK_VERSION}'))"
            )
        sql = (
            "SELECT id, name, goal, capability_statement, display_name, "
            f"display_metadata_version FROM procedures WHERE {where} ORDER BY t_created"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = await pool.fetch(sql)
        stats["selected"] = len(rows)
        print(f"{len(rows)} live procedure(s) selected for display-metadata backfill")

        for r in rows:
            proc = dict(r)
            name, desc, quality = build_display_metadata(proc)
            version = (
                DISPLAY_METADATA_VERSION if quality is None
                else DISPLAY_METADATA_FALLBACK_VERSION
            )
            if quality is not None:
                stats["fallback"] += 1
                _record(_DISPLAY_REPORT, {
                    "ts": _now(), "id": str(proc["id"]), "name": proc["name"],
                    "quality_error": quality, "display_name": name,
                    "display_description": desc,
                })
            if dry_run:
                stats["skipped_dry"] += 1
                continue
            await pool.execute(
                "UPDATE procedures SET display_name = $2, display_description = $3, "
                "display_metadata_version = $4, updated_at = now() WHERE id = $1",
                proc["id"], name, desc, version,
            )
            stats["updated"] += 1

        print(f"\ndisplay-metadata backfill: {json.dumps(stats)}")
        if stats["fallback"]:
            print(f"{stats['fallback']} row(s) fell back to de-slug only -- "
                  f"listed in {_DISPLAY_REPORT} for source repair")
        return stats
    finally:
        if owns_pool:
            await pool.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--representation", action="store_true",
                      help="re-embed live rows off the current canonical retrieval document (default)")
    mode.add_argument("--embed-missing", action="store_true",
                      help="embed only live rows that have no embedding at all")
    mode.add_argument("--display-metadata", action="store_true",
                      help="rebuild display_name/display_description (no embedding, no spend)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, help="bound this invocation (worker-safe chunks)")
    p.add_argument("--force", action="store_true",
                   help="reprocess every live row even if already on the current version")
    p.add_argument("--provider", choices=("voyage", "gemini", "local"),
                   help="pin the embedding provider for this run (default: configured chain)")
    args = p.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set")
        return 1

    if args.embed_missing:
        result = asyncio.run(backfill_missing_embeddings(dry_run=args.dry_run, limit=args.limit))
        return 1 if result["failed"] else 0
    if args.display_metadata:
        asyncio.run(backfill_display_metadata(
            dry_run=args.dry_run, limit=args.limit, force=args.force))
        return 0
    # default
    result = asyncio.run(backfill_representation(
        dry_run=args.dry_run, limit=args.limit, force=args.force,
        provider=args.provider))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
