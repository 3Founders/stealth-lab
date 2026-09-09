"""
Retrieval abstention regression (final pre-score remediation, section A).

Proves the real production retrieval path (`applicability.py::
find_applicable_procedures`, the same function `search_procedures`'s MCP
tool wraps) correctly ABSTAINS on a genuinely unrelated query, rather than
returning the least-bad option regardless of true relevance -- the exact
gap this pass's calibration (retrieval-calibration.md) found and fixed
with the measured relevance gate (services/relevance_gate.py::
RELEVANCE_GATE_MIN_SIMILARITY), applied inside find_applicable_procedures
via passes_relevance_gate().

Before the fix: an irrigation-scheduling query returned all 3 admitted
procedures at similarity 0.27-0.31 (real, measured, see
retrieval-calibration.md). After: the same class of query returns nothing.

Companion tests: test_retrieval_fixture_isolation_e2e.py (a fixture must
not surface) and test_retrieval_admitted_knowledge_positive_e2e.py (a real
relevant procedure must still surface) -- all three must pass together to
prove the eligibility+relevance boundary is principled, not merely
"exclude everything" or "exclude nothing".
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.applicability import find_applicable_procedures
from app.services.embeddings import Embedder

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


@pytest.mark.asyncio
async def test_genuinely_unrelated_query_abstains_rather_than_returning_the_least_bad_option():
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        run_id = uuid.uuid4().hex[:8]
        embedder = Embedder()

        # Same real-world-unrelated-domain shape as retrieval-calibration.md's
        # own irrelevant set (agriculture) -- deliberately not reusing that
        # file's exact wording verbatim, but the same genuinely-unrelated
        # topic class that this pass's real calibration measured at
        # similarity 0.31-0.34 against the corpus's 3 real procedures.
        query_vec = await embedder.embed_one(
            f"Recommend a crop rotation schedule for a small organic vegetable "
            f"farm to minimize soil nitrogen depletion ({run_id})",
            input_type="query",
        )

        for require_verified in (True, False):
            candidates = await find_applicable_procedures(
                pool, goal_embedding=query_vec, require_verified=require_verified, limit=10,
            )
            assert candidates == [], (
                f"require_verified={require_verified}: a genuinely unrelated query "
                f"returned {len(candidates)} candidate(s) instead of abstaining -- "
                "the measured relevance gate (relevance_gate.py::"
                "RELEVANCE_GATE_MIN_SIMILARITY) should have excluded them."
            )
    finally:
        await pool.close()
