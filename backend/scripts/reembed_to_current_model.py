"""Re-embed everything stored in an OLDER vector space into the CURRENT embedding model.

    python scripts/reembed_to_current_model.py                                  # PLAN: counts per kind and model, writes nothing
    python scripts/reembed_to_current_model.py --apply --expect-model vertex:gemini-embedding-2
    python scripts/reembed_to_current_model.py --apply --expect-model ... --only goals --limit 200

WHY THIS EXISTS
    A different embedding model is a different vector space: pgvector will compare two vectors from different models
    without complaint and return meaningless distances. Switching GEMINI_EMBEDDING_MODEL therefore owes a re-embed of
    every stored vector. Every row records `embedding_model_id`, so "what still owes a re-embed" is a query, not a guess.

WHAT IT COVERS (live rows only -- t_invalid IS NULL; tombstoned rows are left alone)
    goals            text = goals.goal_embedding_text(canonical_name, description), input_type=document
    claims           text = knowledge_nodes.name (node_type='claim'), input_type=document
    procedures       delegated to backfill_procedure_embeddings.backfill_representation: the canonical retrieval document
                     (procdoc_v*), model-aware, atomic per row, keeps the old vector on failure

SAFETY
    - The default is a PLAN. Writing needs --apply AND --expect-model <id>; the id must equal what the environment
      resolves to, so a mis-set GEMINI_EMBEDDING_MODEL / EMBEDDING_PROVIDER_CHAIN cannot re-embed into the wrong space.
    - RESUMABLE and idempotent: a row already on the target model is never selected, so an interrupted run is finished
      by running it again.
    - A row whose embedding fails keeps its OLD vector and OLD model id (never half-converted) and is written to
      scripts/.backfill_state/reembed_to_current_model.failed.jsonl. Re-running retries only those.
    - Each UPDATE is guarded by `embedding_model_id IS NOT DISTINCT FROM <the old id>`: a row another process changed
      meanwhile is skipped, not overwritten. A statement that touches 0 rows is counted and reported, not ignored
      (row-level security or a concurrent change).
    - The DSN is never printed; the host is.

NOT COVERED (stated, not implied)
    - Rows with NO embedding (never embedded) -- use backfill_procedure_embeddings --embed-missing for procedures.
    - Tombstoned rows (they keep their old vector; they are not served).
    - Vectors held OUTSIDE the primary tables (the search index projection, Project B): refresh it after a run.
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
from typing import Any, Optional
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

BATCH = 64
_STATE_DIR = Path(__file__).resolve().parent / ".backfill_state"
_FAIL_LOG = _STATE_DIR / "reembed_to_current_model.failed.jsonl"


def _goal_text(row: Any) -> str:
    from app.services.goals import goal_embedding_text

    return goal_embedding_text(row["canonical_name"], row["description"])


# kind -> (select of live rows still on another model, text builder, UPDATE guarded on the old model id)
KINDS: dict[str, dict[str, Any]] = {
    "goals": {
        "count": ("SELECT coalesce(embedding_model_id, '(none)') AS model, count(*) AS n FROM goals "
                  "WHERE t_invalid IS NULL AND embedding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"),
        "select": ("SELECT id, canonical_name, description, embedding_model_id FROM goals "
                   "WHERE t_invalid IS NULL AND embedding IS NOT NULL AND embedding_model_id IS DISTINCT FROM $1 "
                   "ORDER BY t_valid"),
        "text": _goal_text,
        "update": ("UPDATE goals SET embedding = $2::vector, embedding_model_id = $3, embedding_provider = $4, "
                   "embedding_text_hash = $5 WHERE id = $1 AND embedding_model_id IS NOT DISTINCT FROM $6"),
        "label": lambda r: str(r["canonical_name"])[:60],
    },
    "claims": {
        "count": ("SELECT coalesce(embedding_model_id, '(none)') AS model, count(*) AS n FROM knowledge_nodes "
                  "WHERE node_type = 'claim' AND t_invalid IS NULL AND embedding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"),
        "select": ("SELECT id, name, embedding_model_id FROM knowledge_nodes "
                   "WHERE node_type = 'claim' AND t_invalid IS NULL AND embedding IS NOT NULL "
                   "AND embedding_model_id IS DISTINCT FROM $1 ORDER BY t_valid"),
        "text": lambda r: str(r["name"]),
        "update": ("UPDATE knowledge_nodes SET embedding = $2::vector, embedding_model_id = $3, embedding_dim = $4 "
                   "WHERE id = $1 AND embedding_model_id IS NOT DISTINCT FROM $5"),
        "label": lambda r: str(r["name"])[:60],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_failure(kind: str, row: Any, error: str) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_FAIL_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now(), "kind": kind, "id": str(row["id"]), "error": error[:400]}) + "\n")


def _update_args(kind: str, row: Any, vector: list[float], text: str, embedder: Any, model_id: str, provider: str) -> tuple:
    from app.services.embeddings import to_pgvector

    old = row["embedding_model_id"]
    if kind == "goals":
        return (row["id"], to_pgvector(vector), model_id, provider, hashlib.sha256(text.encode("utf-8")).hexdigest(), old)
    return (row["id"], to_pgvector(vector), model_id, embedder.dimension, old)


async def plan(pool: Any) -> dict[str, list[tuple[str, int]]]:
    """Live embedded rows per model, per kind. Writes nothing."""
    out: dict[str, list[tuple[str, int]]] = {}
    for kind, spec in KINDS.items():
        out[kind] = [(r["model"], int(r["n"])) for r in await pool.fetch(spec["count"])]
    # procedures are converted by backfill_procedure_embeddings (canonical retrieval document); counted here for the picture
    out["procedures"] = [(r["model"], int(r["n"])) for r in await pool.fetch(
        "SELECT coalesce(embedding_model_id, '(none)') AS model, count(*) AS n FROM procedures "
        "WHERE t_invalid IS NULL AND embedding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")]
    return out


async def reembed_kind(pool: Any, embedder: Any, kind: str, *, limit: Optional[int] = None, batch: int = BATCH) -> dict:
    spec = KINDS[kind]
    target = embedder.embedding_model_id()
    provider = embedder._configured_provider()  # noqa: SLF001 -- same accessor the procedure backfill uses
    rows = list(await pool.fetch(spec["select"], target))
    if limit is not None:
        rows = rows[: int(limit)]
    stats = {"selected": len(rows), "reembedded": 0, "failed": 0, "skipped_changed": 0}
    print(f"{kind}: {len(rows)} live row(s) not on {target}", flush=True)
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        texts = [spec["text"](r) for r in chunk]
        try:
            vectors = await embedder.embed(texts, input_type="document")
            if len(vectors) != len(texts) or any(len(v) != embedder.dimension for v in vectors):
                raise ValueError(f"batch shape mismatch: {len(vectors)} vectors for {len(texts)} texts, dim {embedder.dimension}")
        except Exception as exc:  # noqa: BLE001 -- isolate the culprit row by row
            print(f"  batch {start}-{start + len(chunk)} failed ({str(exc)[:80]}); retrying row-by-row", flush=True)
            vectors = []
            for row, text in zip(chunk, texts):
                try:
                    one = await embedder.embed([text], input_type="document")
                    if not one or len(one[0]) != embedder.dimension:
                        raise ValueError(f"dimension {len(one[0]) if one else 0} != {embedder.dimension}")
                    vectors.append(one[0])
                except Exception as row_exc:  # noqa: BLE001
                    vectors.append(None)
                    stats["failed"] += 1
                    _log_failure(kind, row, repr(row_exc))
                    print(f"    FAILED (kept old vector): {spec['label'](row)} -- {str(row_exc)[:100]}", flush=True)
        for row, text, vector in zip(chunk, texts, vectors):
            if vector is None:
                continue
            tag = await pool.execute(spec["update"], *_update_args(kind, row, vector, text, embedder, target, provider))
            touched = int(str(tag).rsplit(" ", 1)[-1]) if str(tag).rsplit(" ", 1)[-1].isdigit() else 0
            if touched:
                stats["reembedded"] += 1
            else:
                stats["skipped_changed"] += 1     # changed under us, or not visible to this role (row-level security)
        print(f"  {kind}: {stats['reembedded']}/{len(rows)} re-embedded", flush=True)
    return stats


def _host(dsn: Optional[str]) -> str:
    try:
        return urlparse(dsn or "").hostname or "(unknown)"
    except ValueError:
        return "(unknown)"


async def run(args: argparse.Namespace, *, pool: Any = None, embedder: Any = None) -> int:
    from app.db.session import create_pool
    from app.services.embeddings import Embedder

    owns_pool = pool is None
    pool = pool or await create_pool(min_size=1, max_size=4)
    try:
        embedder = embedder or Embedder(rate_limit_pool=pool)
        target = embedder.embedding_model_id()
        print(f"database host : {_host(os.environ.get('DATABASE_URL'))}")
        print(f"target model  : {target}  (dimension {embedder.dimension})")
        current = await plan(pool)
        for kind, rows in current.items():
            print(f"{kind:8}: " + (", ".join(f"{m} x{n}" for m, n in rows) or "(no embedded rows)"))
        if not args.apply:
            print("\nPLAN ONLY -- nothing written. To convert: --apply --expect-model " + target)
            return 0
        if args.expect_model != target:
            print(f"\nREFUSED: --expect-model {args.expect_model!r} != the model this environment resolves to ({target!r}).\n"
                  "Set GEMINI_EMBEDDING_MODEL / EMBEDDING_PROVIDER_CHAIN, or correct --expect-model.")
            return 2
        failed = 0
        for kind in KINDS:
            if args.only and kind != args.only:
                continue
            failed += (await reembed_kind(pool, embedder, kind, limit=args.limit))["failed"]
        if not args.only or args.only == "procedures":
            from backfill_procedure_embeddings import backfill_representation

            failed += (await backfill_representation(limit=args.limit, pool=pool, embedder=embedder)).get("failed", 0)
        print("\nRefresh the search index projection for the touched objects, then re-run this script's PLAN: "
              "every kind should now list only " + target + ".")
        return 1 if failed else 0
    finally:
        if owns_pool:
            await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--apply", action="store_true", help="write (default: plan only)")
    p.add_argument("--expect-model", help="the embedding_model_id you mean to convert TO; required with --apply")
    p.add_argument("--only", choices=(*KINDS, "procedures"), help="one kind only")
    p.add_argument("--limit", type=int, help="bound each kind (worker-safe chunks)")
    args = p.parse_args(argv)
    if args.apply and not args.expect_model:
        p.error("--apply requires --expect-model <embedding_model_id>")
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set")
        return 1
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
