"""
Real end-to-end retrieval (task spec §17), against real PostgreSQL AND a
real embedding provider (Voyage, via the real `Embedder`) -- NOT the
`_FakeEmbedder()` stand-in this suite uses everywhere else.

Deliberately distinct from `test_gold_retrieval_offline.py` in this same
directory, which that file's own docstring already documents as testing
ONLY `fuse_rrf`'s rank-fusion arithmetic against hand-picked hit lists --
"genuine paraphrase-quality Recall@k/MRR/nDCG against real embeddings
needs a DATABASE_URL-gated e2e companion this pass does not include,"
tracked there as a documented limitation. THIS file is that companion.
Read together: the offline file proves the fusion math is correct in
isolation; this file proves the real embedding model actually produces
useful rankings when driven through the real `HybridRetriever.retrieve()`
path end to end (real embed call -> real pgvector `<=>` search -> real
`ts_rank` lexical search -> the same `fuse_rrf` -> ranked results). Do NOT
read this file's numbers as "fusion quality" (that's the offline file's
job) or this file's absence as ARCHITECTURE.md's gap remaining open.

`HybridRetriever` retrieves over `task_nodes`/`knowledge_nodes` (its only
two supported tables -- see its own `_vector_search`/`_lexical_search`),
NOT the `procedures` table directly (that table has its own separate real
vector-search path in `app/services/applicability.py::
find_applicable_procedures`/`verified_procedure_candidates`, out of scope
for this file, which targets the specific gap the existing offline test's
docstring names). Fixture task_nodes below stand in for retrievable
"procedure-shaped" content the same way `test_applicability_hard_
constraints_offline.py`'s `_procedure()` helper does for the offline gold
set -- direct SQL fixture setup, not the thing under test (the retrieval
call is the real, unmocked production code).

Rate-limit discipline (real, not hypothetical -- confirmed by two earlier
phases hitting this): this worktree's Voyage key is capped at roughly 3
RPM with no payment method. `retrieve(query, query_vec=...)` accepts a
precomputed vector specifically to let a caller embed once and reuse it
(see retrieval.py's own docstring: `decompose()` does exactly this) --
this file uses that same precomputed-vector path to make exactly TWO real
embedding API calls total for the whole file (one batched `embed()` call
for every fixture document's text, one for every query's text), never a
per-case `embed_one()`/`retrieve()`-without-`query_vec` loop.

Skips itself when DATABASE_URL is unset, matching every other _e2e.py file.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.services.embeddings import Embedder, to_pgvector  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402
from tests.evaluation.harness import metrics  # noqa: E402

PREFIX = "live-retrieval-gold"


def _tag() -> str:
    return uuid.uuid4().hex[:8]


# Four genuinely distinct topics (the "relevant" targets), each with one
# document text (what gets embedded + stored) and two real query
# paraphrases (exact-ish wording, then a looser paraphrase using different
# vocabulary) -- proving paraphrase-quality, not keyword overlap.
_TOPICS = [
    (
        "restart-pool",
        "Restart a stuck Postgres connection pool: drain in-flight queries, "
        "close idle connections, then recreate the pool with the same DSN.",
        [
            "how do I restart a stuck postgres connection pool",
            "the database connections seem frozen, what's the fix for reviving them",
        ],
    ),
    (
        "rotate-tls",
        "Rotate an expiring TLS certificate on the load balancer without "
        "downtime: provision the new cert, reload the listener, then revoke the old one.",
        [
            "rotate an expiring tls certificate with zero downtime",
            "our HTTPS cert is about to expire, how do we swap it without an outage",
        ],
    ),
    (
        "canary-deploy",
        "Deploy a canary release behind a feature flag: ship to five percent "
        "of traffic, watch error rates, then ramp up or roll back.",
        [
            "deploy a canary release using a feature flag",
            "we want to gradually roll out a new build and watch for errors before going full traffic",
        ],
    ),
    (
        "flaky-fixture",
        "Debug a flaky pytest fixture that fails intermittently in CI: "
        "isolate shared state between tests, add explicit teardown, rerun in isolation.",
        [
            "debug a flaky pytest fixture failing intermittently in ci",
            "one of our tests only fails sometimes on the CI runner, how do we track down the flakiness",
        ],
    ),
]

# Three distractors -- unrelated topics, used only for false-positive-rate.
_DISTRACTORS = [
    "Bake a loaf of sourdough bread using a room-temperature starter.",
    "Plan a two-week backpacking route through the Pacific Crest Trail.",
    "Transpose a jazz lead sheet from concert pitch to B-flat instruments.",
]


async def _cleanup(pool, name_like: str) -> None:
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{name_like}%")


@pytest.mark.asyncio
async def test_live_hybrid_retrieval_recall_mrr_ndcg_and_false_positive_rate():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    name_like = f"{PREFIX}-{tag}"
    try:
        await _cleanup(pool, name_like)

        embedder = Embedder()
        doc_texts = [text for _slug, text, _queries in _TOPICS] + list(_DISTRACTORS)
        doc_vecs = await embedder.embed(doc_texts, input_type="document")  # ONE real API call

        node_ids: dict[str, uuid.UUID] = {}
        for i, (slug, text, _queries) in enumerate(_TOPICS):
            row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, description, embedding) "
                "VALUES ($1, $2, $3::vector) RETURNING id",
                f"{name_like}-{slug}", text, to_pgvector(doc_vecs[i]),
            )
            node_ids[slug] = row["id"]
        distractor_ids = []
        for j, text in enumerate(_DISTRACTORS):
            row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, description, embedding) "
                "VALUES ($1, $2, $3::vector) RETURNING id",
                f"{name_like}-distractor-{j}", text, to_pgvector(doc_vecs[len(_TOPICS) + j]),
            )
            distractor_ids.append(row["id"])

        all_queries = [q for _slug, _text, queries in _TOPICS for q in queries]
        query_vecs = await embedder.embed(all_queries, input_type="query")  # ONE real API call

        retriever = HybridRetriever(pool, embedder=embedder)
        recalls_1, recalls_5, mrrs, ndcgs, fprs = [], [], [], [], []
        qi = 0
        for slug, _text, queries in _TOPICS:
            relevant = {node_ids[slug]}
            irrelevant = set(distractor_ids)
            for query in queries:
                result = await retriever.retrieve(query, top_k=10, query_vec=query_vecs[qi])
                qi += 1
                ranked_ids = [n for n in result.entrypoint_ids]

                assert relevant & set(ranked_ids), (
                    f"query {query!r} did not retrieve its own real target node at all "
                    f"(ranked_ids={ranked_ids}) -- real embedding-level retrieval failure, "
                    "not a fusion-arithmetic issue (that's the offline file's job)"
                )
                recalls_1.append(metrics.recall_at_k(ranked_ids, relevant, 1))
                recalls_5.append(metrics.recall_at_k(ranked_ids, relevant, 5))
                mrrs.append(metrics.mrr(ranked_ids, relevant))
                ndcgs.append(metrics.ndcg(ranked_ids, relevant, k=10))
                fprs.append(metrics.false_positive_rate(ranked_ids, irrelevant, 5))

        n = len(mrrs)
        avg_recall_1 = sum(recalls_1) / n
        avg_recall_5 = sum(recalls_5) / n
        avg_mrr = sum(mrrs) / n
        avg_ndcg = sum(ndcgs) / n
        avg_fpr = sum(fprs) / n

        print(
            f"\n[live retrieval, n={n} real queries against real Voyage embeddings] "
            f"Recall@1={avg_recall_1:.3f} Recall@5={avg_recall_5:.3f} "
            f"MRR={avg_mrr:.3f} nDCG@10={avg_ndcg:.3f} FPR@5={avg_fpr:.3f}"
        )

        # A real, own-created distractor must never outrank a real, own-created
        # relevant target at rank 1 for the majority of these genuinely distinct
        # topics -- the actual embedding-quality bar this file exists to check,
        # not fusion arithmetic (already proven separately, offline).
        assert avg_recall_1 >= 0.5, (
            f"real embedding-level Recall@1 was only {avg_recall_1:.3f} across {n} paraphrase "
            "queries against 4 genuinely distinct real topics -- below the bar this test sets "
            "for the real embedding model actually distinguishing unrelated content"
        )
        assert avg_fpr < 0.5, (
            f"real embedding-level false-positive-rate@5 was {avg_fpr:.3f} -- distractors are "
            "dominating top-5 results more than they should"
        )
    finally:
        await _cleanup(pool, name_like)
        await pool.close()
