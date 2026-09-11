"""
Offline tests for applicability.verified_procedure_candidates() and its
wiring into the MCP server's retrieve_precedent tool (backend/app/
mcp_server/server.py).

THE GAP THIS CLOSES: retrieve_precedent's candidate set --
reuse_detection._vector_candidates -- only ever queried task_nodes and
knowledge_nodes; `procedures` was never in it at all, even though the
embedding column, HNSW index, and a real vector-similarity query over
procedures already existed (applicability.py's find_applicable_procedures,
right above the new function tested here). bootstrap_demo.py's own
"HONEST FINDING" / Question #7 named this exact gap (see demo.md C4).

2026-08-27 founder ruling (see .scratch/build-board.md CORE-B queue):
keep the cold-start gate intact -- retrieve_precedent surfaces procedures
using the SAME verified-only rule applicability.py enforces everywhere
else. Surfacing unverified/candidate procedures too was considered and
explicitly rejected (false-reuse risk: an agent could read "similar
precedent found" as an implicit signal without ever going through
check_procedure's real cascade). Both directions pinned below, not just
the happy path.
"""
import asyncio
import os

_ENV_BEFORE_MCP_SERVER_IMPORT = dict(os.environ)

from app.services.access import AccessScope
from app.services.applicability import verified_procedure_candidates
import app.mcp_server.server as server_module

# Same real, found-the-hard-way reason test_mcp_check_procedure_offline.py
# does this: importing app.mcp_server.server runs its module-level
# load_dotenv(), a process-wide os.environ mutation that would otherwise
# leak DATABASE_URL into every *_e2e.py module pytest collects afterward.
for _key in set(os.environ) - set(_ENV_BEFORE_MCP_SERVER_IMPORT):
    del os.environ[_key]
for _key, _val in _ENV_BEFORE_MCP_SERVER_IMPORT.items():
    if os.environ.get(_key) != _val:
        os.environ[_key] = _val


# --- applicability.verified_procedure_candidates() ---------------------


def _procedure(
    id_, similarity, *,
    verification_state="verified", approval_status="approved",
    availability="active", staleness="fresh", t_invalid=None,
    embedding="present", name="pagination fix", goal="paginate a large result set",
):
    return {
        "id": id_, "name": name, "goal": goal, "similarity": similarity,
        "verification_state": verification_state, "approval_status": approval_status,
        "availability": availability, "staleness": staleness, "t_invalid": t_invalid,
        "embedding": embedding,
    }


class FakeProceduresPool:
    """
    Simulates the real WHERE-clause semantics of verified_procedure_
    candidates' query in Python -- the only honest way to prove offline
    that an unverified/unapproved/stale/etc. procedure is actually
    excluded, since no real Postgres WHERE clause runs against this data
    otherwise (a FakePool that just echoes back whatever rows were
    configured, like test_applicability_cascade_offline.py's, can't
    distinguish "the code asked correctly" from "the code asked for
    everything").
    """

    def __init__(self, procedures, verified_count=None):
        self._procedures = procedures
        self._verified_count = (
            verified_count if verified_count is not None else
            sum(
                1 for p in procedures
                if p["verification_state"] == "verified"
                and p["availability"] == "active" and p["t_invalid"] is None
            )
        )
        self.fetch_calls = []
        self.fetchval_calls = []

    async def fetchval(self, sql, *params):
        self.fetchval_calls.append((" ".join(sql.split()), params))
        return self._verified_count

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        assert "FROM procedures" in sql
        limit = params[-1]
        matched = [
            p for p in self._procedures
            if p["verification_state"] == "verified"
            and p["approval_status"] == "approved"
            and p["availability"] == "active"
            and p["staleness"] != "stale"
            and p["t_invalid"] is None
            and p["embedding"] is not None
        ]
        matched.sort(key=lambda p: p["similarity"], reverse=True)
        return [
            {"id": p["id"], "name": p["name"], "goal": p["goal"], "similarity": p["similarity"]}
            for p in matched[:limit]
        ]


def test_verified_procedure_with_a_real_embedding_is_returned():
    verified = _procedure("proc-verified", similarity=0.91)
    pool = FakeProceduresPool([verified])

    results = asyncio.run(verified_procedure_candidates(pool, [0.1, 0.2], limit=5))

    assert [r["id"] for r in results] == ["proc-verified"]
    assert results[0]["similarity"] == 0.91


def test_candidate_unverified_procedure_never_shows_up_even_with_higher_similarity():
    """Pins the founder ruling itself: a CANDIDATE procedure with a
    HIGHER similarity than a verified one must still be excluded."""
    candidate = _procedure("proc-candidate", similarity=0.99, verification_state="candidate")
    verified = _procedure("proc-verified", similarity=0.80)
    pool = FakeProceduresPool([candidate, verified])

    results = asyncio.run(verified_procedure_candidates(pool, [0.1, 0.2], limit=5))

    assert [r["id"] for r in results] == ["proc-verified"]


def test_verified_but_unapproved_procedure_is_also_excluded():
    """verification_state='verified' alone isn't the real gate --
    check_hard_constraints' require_verified branch also requires
    approval_status='approved' (the REAL GAP CLOSED note in this same
    module), and this query mirrors that, not just verification_state."""
    unapproved = _procedure("proc-unapproved", similarity=0.95, approval_status="proposed")
    pool = FakeProceduresPool([unapproved])

    results = asyncio.run(verified_procedure_candidates(pool, [0.1, 0.2], limit=5))

    assert results == []


def test_stale_or_quarantined_verified_procedures_are_excluded_too():
    stale = _procedure("proc-stale", similarity=0.95, staleness="stale")
    quarantined = _procedure("proc-quarantined", similarity=0.95, availability="quarantined")
    pool = FakeProceduresPool([stale, quarantined])

    results = asyncio.run(verified_procedure_candidates(pool, [0.1, 0.2], limit=5))

    assert results == []


def test_cold_start_gate_still_applies_even_with_a_verified_row_present():
    """The gate is a SYSTEM-WIDE count, not "does a matching row exist
    for this query" -- forcing verified_count=0 must short-circuit
    before the candidate query even runs, regardless of what fetch would
    otherwise have returned."""
    verified = _procedure("proc-verified", similarity=0.95)
    pool = FakeProceduresPool([verified], verified_count=0)

    results = asyncio.run(verified_procedure_candidates(pool, [0.1, 0.2]))

    assert results == []
    assert pool.fetch_calls == []


def test_query_pins_the_verified_only_where_clause():
    pool = FakeProceduresPool([_procedure("p1", 0.9)])

    asyncio.run(verified_procedure_candidates(pool, [0.1, 0.2]))

    sql, _params = pool.fetch_calls[0]
    assert "verification_state = 'verified'" in sql
    assert "approval_status = 'approved'" in sql
    assert "availability = 'active'" in sql
    assert "staleness != 'stale'" in sql
    assert "t_invalid IS NULL" in sql
    assert "embedding IS NOT NULL" in sql


def test_visibility_scope_threads_through_like_the_cold_start_gate():
    pool = FakeProceduresPool([_procedure("p1", 0.9)])

    asyncio.run(verified_procedure_candidates(pool, [0.1, 0.2], AccessScope.for_user("tenant-7")))

    sql, params = pool.fetch_calls[0]
    assert "owner_id" in sql and "visibility" in sql
    assert "tenant-7" in params


# --- retrieve_precedent wiring (MCP server) -----------------------------


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


class FakeEmbedder:
    def __init__(self, vec):
        self._vec = vec

    async def embed_one(self, text, input_type="document"):
        return self._vec


def _run(coro):
    return asyncio.run(coro)


def test_retrieve_precedent_fuses_a_verified_procedure_into_the_output(monkeypatch):
    async def fake_vector_candidates(pool, query_vec, scope):
        return []

    async def fake_verified_procedure_candidates(pool, query_vec, access_scope=None):
        return [{"id": "proc-verified", "name": "pagination fix", "goal": "paginate", "similarity": 0.91}]

    monkeypatch.setattr(server_module, "Embedder", lambda: FakeEmbedder([0.1, 0.2]))
    monkeypatch.setattr(server_module, "_vector_candidates", fake_vector_candidates)
    monkeypatch.setattr(server_module, "verified_procedure_candidates", fake_verified_procedure_candidates)

    ctx = FakeContext(pool="fake-pool-sentinel")
    result = _run(server_module.retrieve_precedent("how do I paginate a large result set?", ctx))

    assert "[procedures] 'pagination fix'" in result
    assert "proc-verified" in result


def test_retrieve_precedent_threshold_still_applies_to_fused_procedure_candidates():
    """The RETRIEVE_PRECEDENT_THRESHOLD filter must apply AFTER fusing --
    a procedure candidate below threshold must not leak through just
    because it came from the new path."""
    async def fake_vector_candidates(pool, query_vec, scope):
        return []

    async def fake_low_similarity_procedure(pool, query_vec, access_scope=None):
        assert server_module.RETRIEVE_PRECEDENT_THRESHOLD > 0.10
        return [{"id": "proc-weak", "name": "weak match", "goal": "unrelated", "similarity": 0.10}]

    import app.mcp_server.server as sm
    orig_embedder, orig_vc, orig_vpc = sm.Embedder, sm._vector_candidates, sm.verified_procedure_candidates
    sm.Embedder = lambda: FakeEmbedder([0.1, 0.2])
    sm._vector_candidates = fake_vector_candidates
    sm.verified_procedure_candidates = fake_low_similarity_procedure
    try:
        ctx = FakeContext(pool="fake-pool-sentinel")
        result = _run(sm.retrieve_precedent("irrelevant query", ctx))
    finally:
        sm.Embedder, sm._vector_candidates, sm.verified_procedure_candidates = orig_embedder, orig_vc, orig_vpc

    assert result.startswith("No precedent found")
    assert "proc-weak" not in result


def test_retrieve_precedent_falls_back_when_both_candidate_sources_are_empty(monkeypatch):
    async def fake_empty(pool, query_vec, access_scope=None):
        return []

    monkeypatch.setattr(server_module, "Embedder", lambda: FakeEmbedder([0.1, 0.2]))
    monkeypatch.setattr(server_module, "_vector_candidates", fake_empty)
    monkeypatch.setattr(server_module, "verified_procedure_candidates", fake_empty)

    ctx = FakeContext(pool="fake-pool-sentinel")
    result = _run(server_module.retrieve_precedent("nothing matches this", ctx))

    assert result.startswith("No precedent found")
