"""
WAVE-3 / HARDENING adoption sweep -- proving tests (Lane CORE-A).

Two halves, matching the board assignment:

1.  The evidence WRITER (cross-lane request #1 landed):
    services/procedures.py::record_execution_outcome is the first
    tenant_transaction() caller. One outcome -> one execution_result
    evidence row + (failures only) one classify_and_route() row, all in
    the SAME transaction, with app.tenant_id bound as the FIRST
    statement so db/29's RLS backstop is keyed, not permissive. The
    evidence INSERT precedes the procedures UPDATE because db/30's
    verified-requires-evidence engine trigger reads
    procedure_evidence_stats at UPDATE time and must count THIS run's
    row.

2.  The query-path SWEEP (cross-lane request #2 / Question #5):
    every remaining tenant-bearing read path threads BOTH axes through
    access.scope_predicates(). Under the sweep's default posture
    (TenantScope.unrestricted()) the tenancy half renders the visible
    literal TRUE with zero bindings -- behavior byte-identical, axis
    present in text. Passing a real TenantScope must place the
    fragment at the CORRECT placeholder index with the param appended
    after the visibility params.

All offline: FakePool/FakeConn capture SQL text + params. No live DB.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from app.services.access import AccessScope, TenantScope


BACKEND_COMMON_TENANT = "00000000-0000-0000-0000-000000000001"

PROCEDURE_ID = "00000000-0000-4000-8000-000000000001"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


class FakeConn:
    """Captures every statement; serves the three statements
    record_execution_outcome issues (plus set_config)."""

    def __init__(self, proc_row: dict, fail_fetchval: bool = False):
        self.proc_row = proc_row
        self.statements: list[tuple[str, tuple]] = []
        self.committed = False
        self.rolled_back = False
        self._fail_fetchval = fail_fetchval

    class _TxnCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            if exc_type is None:
                self._conn.committed = True
            else:
                self._conn.rolled_back = True
            return False

    def transaction(self):
        return FakeConn._TxnCM(self)

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "SET"

    async def fetchrow(self, sql, *args):
        self.statements.append((_norm(sql), args))
        norm = _norm(sql)
        if "FOR UPDATE" in norm:
            if self.proc_row is None:
                return None
            return _Row(self.proc_row)
        if "INSERT INTO evidence" in norm:
            cols = (
                "id", "target_type", "target_id", "target_version",
                "outcome_status", "failure_class", "context_key",
                "independence_group",
            )
            # positional params: id=$1 ... context/independence $9/$10,
            # status $11, class $13
            values = {
                "id": args[0],
                "target_type": args[2],
                "target_id": args[3],
                "target_version": args[4],
                "outcome_status": args[10],
                "failure_class": args[12],
                "context_key": args[9],
                "independence_group": args[8],
            }
            return _Row({c: values[c] for c in cols})
        if "UPDATE procedures" in norm:
            return _Row({"id": UUID(PROCEDURE_ID), "verification_state": "candidate"})
        raise AssertionError(f"unexpected fetchrow: {norm[:120]}")

    async def fetchval(self, sql, *args):
        self.statements.append((_norm(sql), args))
        if self._fail_fetchval:
            raise AssertionError("fetchval should not be needed here")
        return "routing-row-id"

    def statements_of(self, needle: str) -> list[tuple[str, tuple]]:
        return [(s, p) for s, p in self.statements if needle in s]

    def index_of(self, needle: str) -> int:
        for i, (s, _) in enumerate(self.statements):
            if needle in s:
                return i
        raise AssertionError(f"no statement matching {needle!r}")


class _Row(dict):
    """asyncpg Record stand-in: mapping access by key."""

    def __getitem__(self, key):
        return super().__getitem__(key)


class FakeTxnPool:
    """pool.acquire() -> FakeConn (which carries its own transaction CM)."""

    def __init__(self, conn: FakeConn):
        self._conn = conn

    class _AcquireCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return FakeTxnPool._AcquireCM(self._conn)


def _proc_row(
    verification_state: str = "candidate",
    availability: str = "active",
) -> dict:
    return {
        "id": UUID(PROCEDURE_ID),
        "procedure_id": UUID("00000000-0000-4000-8000-0000000000aa"),
        "version": 3,
        "verification_state": verification_state,
        "availability": availability,
        "verification_stats": {
            "attempts": 0,
            "successes": 0,
            "context_keys_seen": [],
            "consecutive_failures": 0,
        },
    }


def _run_writer(pool, **kw):
    from app.services.procedures import record_execution_outcome

    return asyncio.run(
        record_execution_outcome(
            pool,
            procedure_row_id=PROCEDURE_ID,
            success=kw.pop("success"),
            context_key=kw.pop("context_key", "ctx-a"),
            **kw,
        )
    )


# ---------------------------------------------------------------------
# Part 1 -- the writer (cross-lane request #1)
# ---------------------------------------------------------------------


def test_writer_binds_tenant_setting_as_first_statement():
    from app.services.access import TENANT_SETTING

    conn = FakeConn(_proc_row())
    _run_writer(FakeTxnPool(conn), success=True)
    first_sql, first_args = conn.statements[0]
    assert "set_config" in first_sql
    assert first_args == (TENANT_SETTING, BACKEND_COMMON_TENANT)
    # default posture is Commons, not unrestricted: writes are KEYED.
    assert conn.index_of("set_config") < conn.index_of("FOR UPDATE")


def test_writer_accepts_explicit_tenant_scope_and_stamps_the_row():
    conn = FakeConn(_proc_row())
    _run_writer(
        FakeTxnPool(conn), success=True, tenant_scope=TenantScope.for_tenant("org-zz")
    )
    _, set_args = conn.statements[0]
    assert set_args == ("app.tenant_id", "org-zz")
    ins_sql, ins_args = conn.statements_of("INSERT INTO evidence")[0]
    assert "$17::uuid" in ins_sql  # tenant rides an explicit bound param
    assert ins_args[-1] == "org-zz"


def test_success_writes_exactly_one_evidence_row_and_never_routes():
    conn = FakeConn(_proc_row())
    out = _run_writer(FakeTxnPool(conn), success=True, steps_used=4)
    assert len(conn.statements_of("INSERT INTO evidence")) == 1
    assert conn.statements_of("INSERT INTO failure_routes") == []

    ins_sql, ins_args = conn.statements_of("INSERT INTO evidence")[0]
    # execution_result, procedure target pinned to its EXACT version
    assert ins_args[1] == "execution_result"
    assert ins_args[2] == "procedure"
    assert str(ins_args[3]) == PROCEDURE_ID
    assert ins_args[4] == 3
    # success supports; terminal status recorded; success carries criteria
    assert ins_args[5] == "supports"
    assert ins_args[10] == "success"
    criteria = ins_args[11]
    assert criteria.get("predicate")
    assert criteria.get("metrics")  # never a bare model-asserted success
    assert ins_args[12] is None  # failures classify; successes carry no cause
    assert out["verification_state"] == "candidate"


def test_writer_evidence_precedes_counters_update_for_engine_trigger():
    conn = FakeConn(_proc_row())
    _run_writer(FakeTxnPool(conn), success=True)
    assert conn.index_of("INSERT INTO evidence") < conn.index_of("UPDATE procedures")


def test_failure_routes_in_the_same_transaction():
    conn = FakeConn(_proc_row())
    _run_writer(FakeTxnPool(conn), success=False, failure_class="environment_changed")
    routed = conn.statements_of("INSERT INTO failure_routes")
    assert len(routed) == 1
    sql, args = routed[0]
    assert "ON CONFLICT (evidence_id, route) DO NOTHING" in sql
    # environment_changed -> dependency_queue (the §36 pairing), same conn
    assert args[2] == "dependency_queue"
    # atomicity: routing sits between its evidence row and the counters
    assert (
        conn.index_of("INSERT INTO evidence")
        < conn.index_of("INSERT INTO failure_routes")
        < conn.index_of("UPDATE procedures")
    )


def test_unclassified_failure_lands_in_requires_review_not_silence():
    conn = FakeConn(_proc_row())
    _run_writer(FakeTxnPool(conn), success=False)
    _, args = conn.statements_of("INSERT INTO failure_routes")[0]
    assert args[1] is None  # nobody classified this
    assert args[2] == "requires_review"


def test_unknown_procedure_raises_and_writes_nothing():
    from app.services.procedures import ProcedureNotFound

    conn = FakeConn(None)
    with pytest.raises(ProcedureNotFound):
        _run_writer(FakeTxnPool(conn), success=True)
    assert conn.statements_of("INSERT INTO evidence") == []
    assert conn.statements_of("UPDATE procedures") == []


def test_writer_rolls_the_whole_transaction_back_on_routing_failure():
    class Boom(Exception):
        pass

    conn = FakeConn(_proc_row())

    async def boom_fetchval(sql, *args):
        conn.statements.append((_norm(sql), args))
        raise Boom("rls refused the routing row")

    conn.fetchval = boom_fetchval
    with pytest.raises(Boom):
        _run_writer(FakeTxnPool(conn), success=False)
    assert conn.rolled_back is True and conn.committed is False
    # the evidence row that caused the refused routing never lands either:
    # everything after BEGIN died with the transaction, by construction of
    # the tenant_transaction() bracket (recorded via the CM flags above).


# ---------------------------------------------------------------------
# Part 1b -- migration 30 static checks
# ---------------------------------------------------------------------

BACKEND = Path(__file__).resolve().parents[1]
DB30 = (BACKEND / "db" / "30_verified_requires_evidence.sql").read_text(encoding="utf-8")


def test_migration30_gates_only_the_transition_into_verified():
    assert "NEW.verification_state = 'verified'" in DB30
    assert "OLD.verification_state IS DISTINCT FROM 'verified'" in DB30


def test_migration30_requires_independent_supporting_evidence_via_the_view():
    assert "procedure_evidence_stats" in DB30
    assert "independent_supporting_required >= 1" in DB30


def test_migration30_is_additive_idempotent_and_guarded():
    assert "CREATE OR REPLACE FUNCTION sl_verified_requires_evidence" in DB30
    assert "tg_procedures_verified_requires_evidence" in DB30
    assert "SELECT 1 FROM pg_trigger" in DB30  # guarded CREATE TRIGGER
    for banned in ("DROP TABLE", "DROP COLUMN", "DELETE FROM", "TRUNCATE"):
        assert banned not in DB30, banned


# ---------------------------------------------------------------------
# Part 2 -- the sweep (cross-lane request #2 / Question #5)
# ---------------------------------------------------------------------


class CapturePool:
    """Records (normalized sql, params) for fetch/fetchrow/fetchval;
    returns empty results everywhere. Doubles as its own connection:
    acquire() yields self so conn-style callers work offline too."""

    def __init__(self, rows=None):
        self.queries: list[tuple[str, tuple]] = []
        self._rows = rows or {}

    class _NoopCM:
        def __init__(self, outer):
            self._outer = outer

        async def __aenter__(self):
            return self._outer

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return CapturePool._NoopCM(self)

    def transaction(self):
        return CapturePool._NoopCM(self)

    async def fetch(self, sql, *params):
        self.queries.append((" ".join(sql.split()), tuple(params)))
        key = next((k for k in self._rows if k in sql), None)
        return [dict(r) for r in self._rows.get(key, [])]

    async def fetchrow(self, sql, *params):
        rows = await self.fetch(sql, *params)
        return dict(rows[0]) if rows else None

    async def fetchval(self, sql, *params):
        await self.fetch(sql, *params)
        return None

    def all_sql(self) -> list[str]:
        return [s for s, _ in self.queries]


def test_sweep_state_project_state_default_renders_visible_true():
    from app.services.state import project_state

    pool = CapturePool()
    as_of = datetime.now(timezone.utc)
    asyncio.run(project_state(pool, subjects=["s"], as_of=as_of))
    sql, params = pool.queries[0]
    assert "(TRUE) AND (TRUE)" in sql
    assert params == (["s"], as_of)  # zero stray bindings from TRUE axes


def test_sweep_state_project_state_scoped_params_are_correctly_sequenced():
    from app.services.state import project_state

    pool = CapturePool()
    as_of = datetime.now(timezone.utc)
    asyncio.run(project_state(
        pool, subjects=["s"], as_of=as_of,
        scope=AccessScope.for_user("u-1"), tenant_scope=TenantScope.for_tenant("org-9"),
    ))
    sql, params = pool.queries[0]
    assert "owner_id = $3" in sql and "tenant_id = $4::uuid" in sql
    assert params == (["s"], as_of, "u-1", "org-9")


def test_sweep_dedup_both_axes_on_base_and_pair_queries():
    from app.services.dedup import find_duplicate_clusters

    seed = [
        {"id": "00000000-0000-4000-8000-0000000000d1", "name": "Alpha Node",
         "full_text": "Alpha Node", "has_embedding": True, "properties": None},
        {"id": "00000000-0000-4000-8000-0000000000d2", "name": "Beta Node",
         "full_text": "Beta Node", "has_embedding": True, "properties": None},
    ]
    pool = CapturePool(rows={"AS has_embedding": seed})
    asyncio.run(find_duplicate_clusters(
        pool, "knowledge_nodes", AccessScope.unrestricted(),
        tenant_scope=TenantScope.for_tenant("org-d"),
    ))
    base_sql, base_params = pool.queries[0]
    assert "tenant_id = $1::uuid" in base_sql and base_params == ("org-d",)
    pair_sql, pair_params = next(
        (s, p) for s, p in pool.queries if "<=>" in s
    )
    assert "a.tenant_id = $1::uuid" in pair_sql
    assert "b.tenant_id = $1::uuid" in pair_sql  # aliased legs share bindings
    assert pair_params == ("org-d",)  # bound ONCE, not once per alias


def test_sweep_reuse_detection_lexical_candidates_carry_both_axes():
    from app.services.reuse_detection import _lexical_candidates

    pool = CapturePool()
    asyncio.run(_lexical_candidates(
        pool, "problem text", AccessScope.anonymous(),
        tenant_scope=TenantScope.for_tenant("org-r"),
    ))
    for sql, params in pool.queries:
        assert "(visibility = 'public') AND (tenant_id = $1::uuid)" in sql
        assert params == ("org-r",)


def test_sweep_hierarchy_fetch_roots_aliases_thread_both_axes():
    from app.services.hierarchy import _fetch_roots

    pool = CapturePool()
    asyncio.run(_fetch_roots(
        pool, "task_nodes", AccessScope.unrestricted(),
        tenant_scope=TenantScope.for_tenant("org-h"),
    ))
    sql, params = pool.queries[0]
    assert "(TRUE) AND (n.tenant_id = $1::uuid)" in sql
    assert params == ("org-h",)


def test_sweep_knowledge_conflict_candidate_side_carries_both_axes():
    from app.services.knowledge_conflict import find_conflicting_knowledge

    pool = CapturePool()
    got = asyncio.run(find_conflicting_knowledge(
        pool, "00000000-0000-4000-8000-0000000000ff",
        scope=AccessScope.unrestricted(), tenant_scope=TenantScope.for_tenant("org-k"),
    ))
    assert got is None
    sql, params = pool.queries[0]
    assert "(TRUE) AND (b.tenant_id = $2::uuid)" in sql  # $1 is the pivot id
    assert params == (UUID("00000000-0000-4000-8000-0000000000ff"), "org-k")


def test_sweep_claim_family_blocking_query_carries_both_axes():
    from app.services.claim_family import resolve_claim_family

    pool = CapturePool()
    res = asyncio.run(resolve_claim_family(
        pool, claim_id="00000000-0000-4000-8000-000000000123",
        tenant_scope=TenantScope.for_tenant("org-cf"),
    ))
    assert res is None  # subject not found under fake pool
    subj_sql, subj_params = pool.queries[0]
    assert "(TRUE) AND (tenant_id = $2::uuid)" in subj_sql
    assert subj_params == ("00000000-0000-4000-8000-000000000123", "org-cf")


class _Hit(dict):
    pass


def test_sweep_hybrid_retriever_vector_and_lexical_legs_carry_both_axes():
    from app.services.retrieval import HybridRetriever

    pool = CapturePool(rows={"FROM task_nodes": [], "FROM knowledge_nodes": []})
    r = HybridRetriever(
        pool, embedder=_NoEmbedder(), tables=("task_nodes",),
        tenant_scope=TenantScope.for_tenant("org-tv"),
    )
    hits = asyncio.run(r._vector_search([0.1, 0.2], 5))
    assert hits == []
    sql, params = pool.queries[0]
    assert "(TRUE) AND (tenant_id = $3::uuid)" in sql
    assert params[2:] == ("org-tv",)

    pool2 = CapturePool()
    r2 = HybridRetriever(
        pool2, embedder=_NoEmbedder(), tables=("knowledge_nodes",),
        tenant_scope=TenantScope.for_tenant("org-tv"),
    )
    asyncio.run(r2._lexical_search("find me", 5))
    sql2, params2 = pool2.queries[0]
    assert "(TRUE) AND (tenant_id = $3::uuid)" in sql2
    assert params2[-1] == "org-tv"


class _NoEmbedder:
    async def embed_one(self, text, input_type="query"):  # pragma: no cover
        raise RuntimeError("embedder must not be needed in these proofs")


def test_sweep_graph_store_neighbors_traverse_node_exists_all_scoped():
    from app.db.graph_store import GraphStore

    nid = UUID("00000000-0000-4000-8000-000000000777")
    store = GraphStore(CapturePool(), tenant_scope=TenantScope.for_tenant("org-g"))

    asyncio.run(store.get_neighbors(nid, "task_nodes"))
    sql, params = store._pool.queries[0]
    assert "(TRUE) AND (tenant_id = $5::uuid)" in sql
    assert params[4:] == ("org-g",)

    asyncio.run(store.traverse_from([nid], "task_nodes"))
    tsql, tparams = store._pool.queries[-1]
    assert "(TRUE) AND (e.tenant_id = $6::uuid)" in tsql  # aliased recursion
    assert tparams[-1] == "org-g"

    exists = asyncio.run(store.node_exists(nid, "task_nodes"))
    assert exists is False
    nsql, nparams = store._pool.queries[-1]
    assert "(TRUE) AND (tenant_id = $2::uuid)" in nsql
    assert nparams == (nid, "org-g")


def test_sweep_local_retrieval_tier_legs_carry_both_axes():
    from app.services.local_retrieval import _tier_candidates_by_path

    pool = CapturePool()
    nodes = asyncio.run(_tier_candidates_by_path(
        pool, ["src/app.py"], scope=AccessScope.unrestricted(),
        matched_by_label="structural", tenant_scope=TenantScope.for_tenant("org-l"),
    ))
    assert nodes == []
    legs = [s for s, _ in pool.queries]
    assert len(legs) == 2  # task_nodes leg + knowledge_nodes leg
    for sql in legs:
        assert "(TRUE) AND (tenant_id = $2::uuid)" in sql
    for (_, params) in pool.queries:
        assert params[-1] == "org-l"


def test_sweep_api_graph_subgraph_reads_are_dual_axis():
    from app.api.graph import get_subgraph

    nid = UUID("00000000-0000-4000-8000-000000000999")

    class SubgraphPool(CapturePool):
        async def fetchrow(self, sql, *params):
            self.queries.append((" ".join(sql.split()), tuple(params)))
            if "SELECT 1 FROM task_nodes" in sql:
                return {"?column?": 1}  # center node lives here
            if sql.startswith("SELECT name FROM"):
                return {"name": "center node"}
            return None

        async def fetch(self, sql, *params):
            self.queries.append((" ".join(sql.split()), tuple(params)))
            return []  # traversal ends immediately

    pool = SubgraphPool()
    resp = asyncio.run(get_subgraph(nid, depth=1, pool=pool, scope=AccessScope.anonymous()))
    hydrates = [(s, p) for s, p in pool.queries if s.startswith("SELECT name FROM")]
    assert len(hydrates) == 1
    sql, params = hydrates[0]
    # anonymous viewer -> public-only visibility half; the endpoint's
    # unrestricted tenant posture renders as visible TRUE (no binding)
    assert "(visibility = 'public') AND (TRUE)" in sql
    assert params == (nid,)
    assert resp.center == nid
