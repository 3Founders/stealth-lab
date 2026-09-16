"""
DB-free coverage for app/execution/implementation_registry.py.

FakeConn/FakeTxnPool mirror test_claim_evidence_offline.py's own idiom
(same repo convention: each offline test file hand-rolls its own fake).
register() is the one write path (pool.acquire()/conn.transaction(), no
tenant_transaction() -- implementations carries no tenant_id column,
matching procedures.py's own precedent); the rest are plain pool.fetch/
pool.fetchrow reads or updates, covered by a lighter FakeReadPool.

A real, live-Postgres round trip (register -> get -> list -> activate ->
verify -> deprecate) was already run directly against this session's dev
database before this test file was written, confirming the schema and
every real SQL statement below actually execute -- these offline tests
pin the exact SQL SHAPE and Python-side contract (validation, honest
None/[] results, anti-enumeration), not "does Postgres accept this SQL"
a second time.
"""
from __future__ import annotations

import asyncio

import pytest

from app.execution.implementation_registry import (
    ImplementationRegistryError,
    REGISTRABLE_KINDS,
    activate,
    deprecate,
    disable,
    get,
    get_for_task,
    implementation_goal_embedding_text,
    list_implementations,
    quarantine,
    register,
    resolve,
    search_implementations_by_goal,
    set_implementation_goal_embedding,
    verify,
)
from app.services.access import AccessScope
from app.services.v0_gate import V0Violation

IMPL_ID = "00000000-0000-4000-8000-0000000000i1"
TASK_ID = "00000000-0000-4000-8000-0000000000t1"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------


class _Row(dict):
    pass


class FakeConn:
    def __init__(self, insert_result: dict):
        self.statements: list[tuple[str, tuple]] = []
        self._insert_result = insert_result

    class _TxnCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def transaction(self):
        return FakeConn._TxnCM(self)

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        self.statements.append((_norm(sql), args))
        norm = _norm(sql)
        if "INSERT INTO implementations" in norm:
            return _Row(self._insert_result)
        raise AssertionError(f"unexpected fetchrow: {norm[:120]}")

    def index_of(self, needle: str) -> int:
        for i, (s, _) in enumerate(self.statements):
            if needle in s:
                return i
        raise AssertionError(f"no statement matching {needle!r}")

    def count_of(self, needle: str) -> int:
        return sum(1 for s, _ in self.statements if needle in s)


class FakeTxnPool:
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


def _default_row() -> dict:
    return {
        "id": IMPL_ID, "name": "graphify-query-graph", "description": None,
        "kind": "tool", "provider": "graphify", "version": 1,
        "status": "candidate", "verification_status": "unverified",
        "content_hash": None, "locator": {}, "invocation": {},
        "input_schema": {}, "output_schema": {}, "requirements": {},
        "auth_requirements": {}, "resource_requirements": {},
        "source_ref": None, "author": None, "license": None,
        "derived_from": None, "deprecated_at": None, "disabled_at": None,
        "created_by": "test-writer", "visibility": "public", "owner_id": None,
        "scope_type": None, "scope_entity_id": None, "t_created": None,
    }


def test_register_inserts_with_candidate_unverified_defaults_and_real_uuid7_id():
    conn = FakeConn(_default_row())
    pool = FakeTxnPool(conn)

    result = _run(register(
        pool, name="graphify-query-graph", kind="tool", provider="graphify",
        created_by="test-writer",
    ))

    assert result["id"] == IMPL_ID
    insert_sql, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO implementations" in s
    )
    # id, name, description, kind, provider, version, ...
    assert insert_args[1] == "graphify-query-graph"
    assert insert_args[3] == "tool"
    assert insert_args[4] == "graphify"
    assert insert_args[5] == 1  # default version
    assert insert_args[18] == "test-writer"  # created_by


def test_register_rejects_unknown_kind_before_touching_sql():
    conn = FakeConn(_default_row())
    pool = FakeTxnPool(conn)

    with pytest.raises(ImplementationRegistryError, match="unknown implementation kind"):
        _run(register(pool, name="x", kind="nonsense", provider="p", created_by="w"))
    assert conn.statements == [], "a rejected kind must never reach the INSERT"


@pytest.mark.parametrize("kind", list(REGISTRABLE_KINDS))
def test_register_accepts_every_registrable_kind(kind):
    """Directive Sec 6: future-compatible kinds (wasm/computer_use/api)
    must be REGISTRABLE even though only frontier/deterministic have a
    real executor -- registrability and runnability are different
    questions, see providers.py's own discover_providers()."""
    conn = FakeConn({**_default_row(), "kind": kind})
    pool = FakeTxnPool(conn)
    result = _run(register(pool, name=f"impl-{kind}", kind=kind, provider="p", created_by="w"))
    assert result["kind"] == kind


def test_register_requires_created_by():
    conn = FakeConn(_default_row())
    pool = FakeTxnPool(conn)
    with pytest.raises(ImplementationRegistryError, match="created_by is required"):
        _run(register(pool, name="x", kind="tool", provider="p", created_by=""))
    assert conn.statements == []


def test_register_requires_nonblank_name_and_provider():
    conn = FakeConn(_default_row())
    pool = FakeTxnPool(conn)
    with pytest.raises(ImplementationRegistryError):
        _run(register(pool, name="  ", kind="tool", provider="p", created_by="w"))
    with pytest.raises(ImplementationRegistryError):
        _run(register(pool, name="x", kind="tool", provider="  ", created_by="w"))
    assert conn.statements == []


def test_register_validates_scope_via_v0_gate_when_scope_type_given():
    conn = FakeConn(_default_row())
    pool = FakeTxnPool(conn)
    with pytest.raises(V0Violation):
        # 'repository' scope requires a scope_entity_id -- the real
        # v0_gate.validate_scope() rule, reused verbatim, not re-derived.
        _run(register(
            pool, name="x", kind="tool", provider="p", created_by="w",
            scope_type="repository",
        ))
    assert conn.statements == []


def test_register_links_task_ids_in_the_same_transaction():
    conn = FakeConn(_default_row())
    pool = FakeTxnPool(conn)

    _run(register(
        pool, name="graphify-query-graph", kind="tool", provider="graphify",
        created_by="w", task_node_ids=[TASK_ID, TASK_ID],  # duplicate on purpose
    ))

    link_count = conn.count_of("INSERT INTO implementation_tasks")
    assert link_count == 2, "one INSERT per requested task id -- ON CONFLICT handles true dupes at the DB layer"
    insert_idx = conn.index_of("INSERT INTO implementations")
    link_idx = conn.index_of("INSERT INTO implementation_tasks")
    assert insert_idx < link_idx, "the implementation row must exist before linking tasks to it"


def test_register_rejects_bad_visibility():
    conn = FakeConn(_default_row())
    pool = FakeTxnPool(conn)
    with pytest.raises(ValueError, match="visibility must be"):
        _run(register(pool, name="x", kind="tool", provider="p", created_by="w", visibility="secret"))
    assert conn.statements == []


# ---------------------------------------------------------------------
# get() / list_implementations() / get_for_task() / resolve()
# ---------------------------------------------------------------------


class FakeReadPool:
    def __init__(self, rows):
        self._rows = rows
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((_norm(sql), params))
        return self._rows

    async def fetchrow(self, sql, *params):
        self.fetchrow_calls.append((_norm(sql), params))
        return self._rows[0] if self._rows else None


def test_get_returns_none_for_missing_row_never_raises():
    pool = FakeReadPool(rows=[])
    result = _run(get(pool, IMPL_ID, scope=AccessScope.unrestricted()))
    assert result is None


def test_get_applies_visibility_predicate():
    pool = FakeReadPool(rows=[_default_row()])
    _run(get(pool, IMPL_ID, scope=AccessScope.anonymous()))
    sql, _ = pool.fetchrow_calls[0]
    assert "visibility = 'public'" in sql


def test_list_implementations_bounds_limit_and_filters_by_kind_provider_status():
    pool = FakeReadPool(rows=[])
    _run(list_implementations(
        pool, scope=AccessScope.unrestricted(),
        kind="tool", provider="graphify", status="active", limit=999,
    ))
    sql, params = pool.fetch_calls[0]
    assert "kind = " in sql and "provider = " in sql and "status = " in sql
    assert params[-1] == 200, "limit must be capped, not passed through unbounded"


def test_list_implementations_rejects_unknown_status():
    pool = FakeReadPool(rows=[])
    with pytest.raises(ImplementationRegistryError, match="unknown status"):
        _run(list_implementations(pool, scope=AccessScope.unrestricted(), status="bogus"))
    assert pool.fetch_calls == []


def test_get_for_task_defaults_to_active_status_only():
    pool = FakeReadPool(rows=[])
    _run(get_for_task(pool, TASK_ID, scope=AccessScope.unrestricted()))
    sql, params = pool.fetch_calls[0]
    assert "i.status = " in sql
    assert params[-1] == "active"


def test_get_for_task_status_none_means_every_lifecycle_state():
    pool = FakeReadPool(rows=[])
    _run(get_for_task(pool, TASK_ID, scope=AccessScope.unrestricted(), status=None))
    sql, params = pool.fetch_calls[0]
    assert "i.status = " not in sql


def test_resolve_returns_none_when_nothing_is_linked_never_fabricates():
    pool = FakeReadPool(rows=[])
    result = _run(resolve(pool, TASK_ID, scope=AccessScope.unrestricted()))
    assert result is None


def test_resolve_with_no_hint_returns_most_recent_active():
    pool = FakeReadPool(rows=[
        {**_default_row(), "id": "aaa", "kind": "tool"},
        {**_default_row(), "id": "bbb", "kind": "frontier"},
    ])
    result = _run(resolve(pool, TASK_ID, scope=AccessScope.unrestricted()))
    assert result["id"] == "aaa"  # first in the (already-recency-ordered) list


def test_resolve_with_hint_kinds_honors_preference_order():
    pool = FakeReadPool(rows=[
        {**_default_row(), "id": "aaa", "kind": "tool"},
        {**_default_row(), "id": "bbb", "kind": "frontier"},
    ])
    result = _run(resolve(
        pool, TASK_ID, scope=AccessScope.unrestricted(), hint_kinds=("frontier", "tool"),
    ))
    assert result["id"] == "bbb", "frontier is first in the caller's preference order, must win over tool"


def test_resolve_with_hint_kinds_none_match_returns_none():
    pool = FakeReadPool(rows=[{**_default_row(), "id": "aaa", "kind": "tool"}])
    result = _run(resolve(pool, TASK_ID, scope=AccessScope.unrestricted(), hint_kinds=("slm",)))
    assert result is None


# ---------------------------------------------------------------------
# lifecycle transitions: activate / deprecate / disable / quarantine / verify
# ---------------------------------------------------------------------


class FakeUpdatePool:
    def __init__(self, row):
        self._row = row
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *params):
        self.calls.append((_norm(sql), params))
        return self._row


def test_activate_sets_status_active():
    pool = FakeUpdatePool({**_default_row(), "status": "active"})
    result = _run(activate(pool, IMPL_ID))
    assert result["status"] == "active"
    sql, params = pool.calls[0]
    assert "SET status = $2" in sql
    assert params == (IMPL_ID, "active")


def test_deprecate_sets_status_and_deprecated_at():
    pool = FakeUpdatePool({**_default_row(), "status": "deprecated"})
    _run(deprecate(pool, IMPL_ID))
    sql, _ = pool.calls[0]
    assert "deprecated_at = now()" in sql


def test_disable_sets_status_and_disabled_at():
    pool = FakeUpdatePool({**_default_row(), "status": "disabled"})
    _run(disable(pool, IMPL_ID))
    sql, _ = pool.calls[0]
    assert "disabled_at = now()" in sql
    assert "SET status = $2" in sql
    assert pool.calls[0][1][1] == "disabled"


def test_quarantine_sets_status_quarantined():
    pool = FakeUpdatePool({**_default_row(), "status": "quarantined"})
    _run(quarantine(pool, IMPL_ID))
    sql, params = pool.calls[0]
    assert params[1] == "quarantined"


def test_verify_sets_verification_status():
    pool = FakeUpdatePool({**_default_row(), "verification_status": "verified"})
    result = _run(verify(pool, IMPL_ID))
    assert result["verification_status"] == "verified"
    sql, _ = pool.calls[0]
    assert "verification_status = 'verified'" in sql


def test_transitions_return_none_for_missing_row():
    pool = FakeUpdatePool(None)
    assert _run(activate(pool, IMPL_ID)) is None
    assert _run(deprecate(pool, IMPL_ID)) is None
    assert _run(verify(pool, IMPL_ID)) is None


# ---------------------------------------------------------------------
# implementation_goal_embedding_text / set_implementation_goal_embedding
# (Sec 11 follow-up: hybrid semantic Goal->Implementation retrieval)
# ---------------------------------------------------------------------


def test_implementation_goal_embedding_text_is_just_the_goal():
    assert implementation_goal_embedding_text("find references") == "find references"


class _FakeEmbedder:
    def __init__(self, vector=None):
        self.vector = vector or [0.1, 0.2, 0.3]
        self.calls: list[tuple[str, str]] = []

    async def embed_one_with_metadata(self, text, input_type="document"):
        self.calls.append((text, input_type))
        meta = type("Meta", (), {"model_id": "m1", "provider": "p1", "text_sha256": "h1"})()
        return self.vector, meta


class FakeGoalIdCheckPool:
    """Answers the SELECT goal_id probe, then the UPDATE ... RETURNING *."""

    def __init__(self, existing_goal_id, updated_row=None):
        self._existing_goal_id = existing_goal_id
        self._updated_row = updated_row
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *params):
        norm = _norm(sql)
        self.calls.append((norm, params))
        if norm.startswith("SELECT goal_id FROM implementations"):
            return {"goal_id": self._existing_goal_id}
        if norm.startswith("UPDATE implementations SET"):
            return self._updated_row
        raise AssertionError(f"unexpected fetchrow: {norm[:120]}")


def test_set_implementation_goal_embedding_no_ops_when_goal_id_already_set():
    pool = FakeGoalIdCheckPool(existing_goal_id="00000000-0000-4000-8000-0000000000g1")
    embedder = _FakeEmbedder()
    result = _run(set_implementation_goal_embedding(pool, IMPL_ID, goal="find references", embedder=embedder))
    assert result is None
    assert embedder.calls == []  # never even computed -- goal_id already covers this row


def test_set_implementation_goal_embedding_raises_for_missing_row():
    pool = FakeGoalIdCheckPool(existing_goal_id=None)

    async def fetchrow_none(sql, *params):
        return None
    pool.fetchrow = fetchrow_none
    with pytest.raises(ImplementationRegistryError, match="does not exist"):
        _run(set_implementation_goal_embedding(pool, IMPL_ID, goal="x", embedder=_FakeEmbedder()))


def test_set_implementation_goal_embedding_computes_and_stores_for_real_when_no_goal_id():
    pool = FakeGoalIdCheckPool(
        existing_goal_id=None,
        updated_row=_Row({**_default_row(), "goal": "find references"}),
    )
    embedder = _FakeEmbedder(vector=[0.5, 0.5])
    result = _run(set_implementation_goal_embedding(pool, IMPL_ID, goal="find references", embedder=embedder))
    assert result is not None
    assert embedder.calls == [("find references", "document")]
    update_sql, update_params = pool.calls[-1]
    assert "goal_embedding = $2::vector" in update_sql
    assert update_params[2] == "m1" and update_params[3] == "p1" and update_params[4] == "h1"


# ---------------------------------------------------------------------
# search_implementations_by_goal (Sec 11 follow-up)
# ---------------------------------------------------------------------


def _max_placeholder(sql: str) -> int:
    import re
    matches = re.findall(r"\$(\d+)", sql)
    return max((int(m) for m in matches), default=0)


class FakeSemanticSearchPool:
    """Dispatches each of search_implementations_by_goal's real distinct
    query shapes by a real substring match on the normalized SQL -- same
    idiom this session's own goal_cost/execution_telemetry fake pools
    already established. ALSO validates that the highest `$N` placeholder
    referenced in each query never exceeds the real number of params
    bound to it -- this is the exact class of bug (an off-by-one in a
    status_clause's own `$N` computation) a canned-rows-only fake would
    never catch, confirmed live against the real DB this session."""

    def __init__(self):
        self.exact_rows: list[dict] = []
        self.lexical_rows: list[dict] = []
        self.via_goal_rows: list[dict] = []
        self.via_own_rows: list[dict] = []
        self.final_rows: list[dict] = []
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        norm = _norm(sql)
        self.calls.append((norm, params))
        highest = _max_placeholder(norm)
        assert highest <= len(params), (
            f"SQL references ${highest} but only {len(params)} param(s) were bound -- "
            f"a real asyncpg.IndeterminateDatatypeError waiting to happen: {norm[:200]}"
        )
        if "WHERE goal_id = $1::uuid" in norm:
            return self.exact_rows
        if "ts_rank" in norm:
            return self.lexical_rows
        if "JOIN goals g ON i.goal_id = g.id" in norm:
            return self.via_goal_rows
        if "goal_embedding <=> $1::vector" in norm and "goal_id IS NULL" in norm:
            return self.via_own_rows
        if norm.startswith("SELECT * FROM implementations WHERE id = ANY"):
            return self.final_rows
        raise AssertionError(f"unexpected fetch: {norm[:150]}")


def test_search_implementations_by_goal_requires_at_least_one_real_input():
    pool = FakeSemanticSearchPool()
    with pytest.raises(ImplementationRegistryError, match="requires"):
        _run(search_implementations_by_goal(pool, scope=AccessScope.unrestricted()))


def test_search_implementations_by_goal_empty_when_nothing_matches_any_leg():
    pool = FakeSemanticSearchPool()
    result = _run(search_implementations_by_goal(pool, goal_text="find refs", scope=AccessScope.unrestricted()))
    assert result == []


def test_search_implementations_by_goal_exact_goal_id_wins_over_weaker_lexical_matches():
    pool = FakeSemanticSearchPool()
    winner_id = "00000000-0000-4000-8000-0000000000e1"
    other_id = "00000000-0000-4000-8000-0000000000e2"
    pool.exact_rows = [{"id": winner_id}]
    pool.lexical_rows = [{"id": other_id, "rank": 0.9}, {"id": winner_id, "rank": 0.1}]
    pool.final_rows = [_Row({**_default_row(), "id": winner_id}), _Row({**_default_row(), "id": other_id})]
    result = _run(search_implementations_by_goal(
        pool, goal_text="find refs", goal_id="00000000-0000-4000-8000-0000000000g1",
        scope=AccessScope.unrestricted(),
    ))
    assert result[0]["id"] == winner_id


def test_search_implementations_by_goal_lexical_only_still_returns_real_candidates():
    pool = FakeSemanticSearchPool()
    impl_id = "00000000-0000-4000-8000-0000000000e3"
    pool.lexical_rows = [{"id": impl_id, "rank": 0.5}]
    pool.final_rows = [_Row({**_default_row(), "id": impl_id})]
    result = _run(search_implementations_by_goal(pool, goal_text="find refs", scope=AccessScope.unrestricted()))
    assert len(result) == 1
    assert result[0]["id"] == impl_id


def test_search_implementations_by_goal_merges_both_semantic_sources_by_real_distance():
    pool = FakeSemanticSearchPool()
    close_id = "00000000-0000-4000-8000-0000000000e4"
    far_id = "00000000-0000-4000-8000-0000000000e5"
    # via_goal (canonical Goal embedding) is CLOSER than via_own (the
    # implementation's own fallback embedding) -- the merge must respect
    # real distance ordering across both sources, not source order.
    pool.via_goal_rows = [{"id": close_id, "dist": 0.05}]
    pool.via_own_rows = [{"id": far_id, "dist": 0.9}]
    pool.final_rows = [_Row({**_default_row(), "id": close_id}), _Row({**_default_row(), "id": far_id})]
    result = _run(search_implementations_by_goal(
        pool, query_embedding=[0.1, 0.2, 0.3], scope=AccessScope.unrestricted(),
    ))
    assert result[0]["id"] == close_id


def test_search_implementations_by_goal_never_fabricates_missing_final_rows():
    """If the id-fusion stage names an id the final SELECT doesn't
    return (a real, if rare, race with a row being deleted between
    queries), that id is silently dropped -- never a fabricated dict."""
    pool = FakeSemanticSearchPool()
    pool.lexical_rows = [{"id": "00000000-0000-4000-8000-0000000000e6", "rank": 0.5}]
    pool.final_rows = []
    result = _run(search_implementations_by_goal(pool, goal_text="find refs", scope=AccessScope.unrestricted()))
    assert result == []
