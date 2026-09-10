"""claim -> procedure candidate: the last manual hop, and its cost gate.

Every job this sweep creates is ONE real grounded_hybrid_v1 LLM call, so
the gate is the security-relevant part, not an optimisation. Three
clauses, each pinned here because dropping any one silently turns the
sweep into a money burner over a corpus the audit already showed is 79%
shell history:

  1. a completion signal (test_run / commit_made) must exist in the
     episode -- CORRECTNESS, not taste: the handler asserts
     outcome="success" and V5_evidence_sufficiency is the rule that
     assertion has to earn.
  2. >= MIN_OBSERVATIONS_TO_EXTRACT observations (p25 of the real corpus
     is 4, so this drops the bottom quartile).
  3. >= MIN_OBSERVATION_TYPES_TO_EXTRACT distinct types -- the clause
     that excludes "Modified check3.py"-shaped, file_touched-only
     episodes.

Fully offline: a FakePool captures the emitted SQL and params. Same
per-file-fake convention as test_requeue_after_episode_assembly_offline.py.
"""
from __future__ import annotations

import json

import pytest

import app.services.ingestion_jobs as ij


class FakePool:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.fetched: list[tuple] = []
        self.executed: list[tuple] = []

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self._rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchrow(self, sql, *args):
        # The handler reads the episode row before gathering evidence.
        return {"session_id": "s1", "start_ts": "T0", "end_ts": "T1",
                "project_id": None}


def _ep(eid="ep-1", sess="sess-1", n_obs=12, n_types=3,
        passing_tests=2, failing_tests=0, unknown_tests=0,
        goal_text="Fix the failing login test"):
    return {"episode_id": eid, "session_id": sess, "n_obs": n_obs,
            "n_types": n_types, "passing_tests": passing_tests,
            "failing_tests": failing_tests, "unknown_tests": unknown_tests,
            "goal_text": goal_text}


def _payload(**overrides):
    """A durable job payload selected by the real enqueue gate."""
    payload = {
        "episode_id": "11111111-1111-1111-1111-111111111111",
        "session_id": "s1",
        "goal_text": "Fix the failing login test",
        "outcome": "success",
    }
    payload.update(overrides)
    return payload


def _inserts(pool):
    return [c for c in pool.executed if "INSERT INTO ingestion_jobs" in c[0]]


# ------------------------------------------------------- registration

def test_extraction_handler_is_registered():
    assert "extract_procedure_from_episode" in ij.JOB_HANDLERS
    assert (ij.JOB_HANDLERS["extract_procedure_from_episode"]
            is ij.handle_extract_procedure_from_episode)


def test_existing_handlers_are_not_displaced():
    assert ij.JOB_HANDLERS["normalize_trace_event"] is ij.handle_normalize_trace_event
    assert (ij.JOB_HANDLERS["promote_observation_to_claim"]
            is ij.handle_promote_observation_to_claim)


# ------------------------------------------------------------ enqueue

@pytest.mark.asyncio
async def test_gated_episode_is_enqueued_once():
    pool = FakePool([_ep()])
    out = await ij.enqueue_pending_procedure_extractions(pool, limit=5)
    assert out == {"examined": 1, "enqueued": 1}

    ins = _inserts(pool)
    assert len(ins) == 1
    _, args = ins[0]
    assert args[0] == "extract_procedure_from_episode"
    payload = json.loads(args[1])
    assert payload["episode_id"] == "ep-1"
    assert payload["session_id"] == "sess-1"
    assert payload["goal_text"] == "Fix the failing login test"
    assert payload["outcome"] == "success"


@pytest.mark.asyncio
async def test_payload_records_why_the_gate_let_it_through():
    """An auditor reading ingestion_jobs later must be able to see the
    numbers that justified spending a call, not just that one was spent."""
    pool = FakePool([_ep(n_obs=17, n_types=3, passing_tests=4)])
    await ij.enqueue_pending_procedure_extractions(pool, limit=5)
    gate = json.loads(_inserts(pool)[0][1][1])["gate"]
    assert gate == {
        "n_obs": 17, "n_types": 3,
        "passing_tests": 4, "failing_tests": 0, "unknown_tests": 0,
    }


# -------------------------------------------------- the four clauses

@pytest.mark.asyncio
async def test_thresholds_are_passed_as_query_params():
    """The gate must be applied by the DATABASE. Fetching everything and
    filtering in Python would still scan the whole corpus, and a future
    edit could drop the Python filter without any test noticing."""
    pool = FakePool([])
    await ij.enqueue_pending_procedure_extractions(pool, limit=9)
    sql, args = pool.fetched[0]
    assert args == (9, ij.MIN_OBSERVATIONS_TO_EXTRACT,
                    ij.MIN_OBSERVATION_TYPES_TO_EXTRACT)
    assert "LIMIT $1" in sql
    assert "p.n_obs   >= $2" in sql
    assert "p.n_types >= $3" in sql


@pytest.mark.asyncio
async def test_explicit_goal_and_verified_outcome_clauses_are_present():
    """A test command/commit is not proof of success. The gate requires a
    source-authored goal and explicit pass/fail observation evidence."""
    pool = FakePool([])
    await ij.enqueue_pending_procedure_extractions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert "p.goal_text IS NOT NULL" in sql
    assert "o.properties->>'passed' = 'true'" in sql
    assert "p.passing_tests > 0" in sql
    assert "p.failing_tests = 0" in sql
    assert "p.unknown_tests = 0" in sql


def test_handler_never_manufactures_goal_or_outcome():
    import inspect
    src = inspect.getsource(ij.handle_extract_procedure_from_episode)
    assert 'payload.get("goal_text")' in src
    assert 'payload.get("outcome")' in src
    assert 'Recurring engineering task observed' not in src


def test_thresholds_are_not_accidentally_zero():
    assert ij.MIN_OBSERVATIONS_TO_EXTRACT >= 5
    assert ij.MIN_OBSERVATION_TYPES_TO_EXTRACT >= 2


# --------------------------------------------------------- idempotency

@pytest.mark.asyncio
async def test_episode_with_an_existing_procedure_is_excluded():
    pool = FakePool([])
    await ij.enqueue_pending_procedure_extractions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert "FROM procedures pr" in sql
    assert "pr.source_episode_ids @> ARRAY[p.episode_id]" in sql


@pytest.mark.asyncio
async def test_episode_with_a_queued_extraction_is_excluded():
    pool = FakePool([])
    await ij.enqueue_pending_procedure_extractions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert "'pending', 'processing'" in sql
    assert "extract_procedure_from_episode" in sql


@pytest.mark.asyncio
async def test_only_claim_justified_episodes_are_candidates():
    """The trigger is a claim, not any episode -- this is the claim ->
    procedure hop, not an episode sweep."""
    pool = FakePool([])
    await ij.enqueue_pending_procedure_extractions(pool, limit=1)
    sql = pool.fetched[0][0]
    assert "episode_links" in sql
    assert "node_type = 'claim'" in sql


@pytest.mark.asyncio
async def test_richest_episodes_first():
    """With a small budget, spend it where there is most to extract."""
    pool = FakePool([])
    await ij.enqueue_pending_procedure_extractions(pool, limit=1)
    assert "ORDER BY p.n_obs DESC" in pool.fetched[0][0]


# ---------------------------------------------------- off unless asked

@pytest.mark.asyncio
async def test_zero_limit_is_a_true_noop():
    # Non-empty rows on purpose: a missing guard would enqueue one.
    pool = FakePool([_ep()])
    out = await ij.enqueue_pending_procedure_extractions(pool, limit=0)
    assert out == {"examined": 0, "enqueued": 0}
    assert _inserts(pool) == []
    assert pool.fetched == [], "limit<=0 must not even run the sweep query"


def test_runner_extract_limit_defaults_to_disabled():
    """Same AST proof --promote-limit carries: a future edit must not be
    able to quietly turn real LLM spend on for every ordinary run."""
    import ast
    import pathlib

    src = pathlib.Path(ij.__file__).parents[2] / "scripts" / "run_ingestion.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "add_argument"
                and node.args
                and getattr(node.args[0], "value", None) == "--extract-limit"):
            for kw in node.keywords:
                if kw.arg == "default":
                    found.append(kw.value.value)
    assert found == [0], f"--extract-limit default must be 0, got {found}"


def test_run_once_keeps_extraction_opt_in():
    import importlib.util
    import inspect
    import pathlib

    src = pathlib.Path(ij.__file__).parents[2] / "scripts" / "run_ingestion.py"
    spec = importlib.util.spec_from_file_location("_run_ingestion_probe2", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sig = inspect.signature(mod._run_once)
    assert sig.parameters["extract_limit"].default == 0
    assert sig.parameters["promote_limit"].default == 0


# ------------------------------------------------------------ handler

@pytest.mark.asyncio
async def test_missing_ids_are_a_loud_error():
    with pytest.raises(ValueError, match="missing source-derived"):
        await ij.handle_extract_procedure_from_episode(FakePool(), {"session_id": "s"})
    with pytest.raises(ValueError, match="missing source-derived"):
        await ij.handle_extract_procedure_from_episode(FakePool(), {"episode_id": "e"})


@pytest.mark.asyncio
async def test_non_success_outcome_is_a_loud_error():
    with pytest.raises(ValueError, match="explicit successful outcome"):
        await ij.handle_extract_procedure_from_episode(
            FakePool(), _payload(outcome="failure"))


@pytest.mark.asyncio
async def test_validator_refusal_is_not_an_exception(monkeypatch):
    """The validators refusing a weak candidate is the system working,
    not a job failure -- a raise here would mark the job 'failed' and
    invite a retry that spends the call again."""
    from app.services.procedure_extraction.schema import ExtractionResult

    async def fake_extract(pool, source, **kw):
        return ExtractionResult(validation_failures=["V4_capability_abstraction: nope"])

    monkeypatch.setattr(
        "app.services.procedure_extraction.extract_procedure", fake_extract)
    monkeypatch.setattr(ij, "_extraction_client", lambda: None)

    await ij.handle_extract_procedure_from_episode(
        FakePool(), _payload(episode_id="ep-1", session_id="sess-1"))


@pytest.mark.asyncio
async def test_evidence_is_windowed_to_the_episode(monkeypatch):
    """REGRESSION GUARD, found by running it. The first cut used
    SessionEvidenceSource, which reads the WHOLE session and treats
    episode_id as a label -- three different gated episodes from one
    session produced three byte-identical procedures (178 steps each,
    same capability_statement). The evidence queries must be bounded by
    the episode's own start_ts/end_ts."""
    seen = {}

    class WindowPool(FakePool):
        async def fetchrow(self, sql, *a):
            return {"session_id": "s1", "start_ts": "T0", "end_ts": "T1",
                    "project_id": None}

        async def fetch(self, sql, *a):
            seen.setdefault("sqls", []).append(sql)
            seen.setdefault("args", []).append(a)
            return []

    from app.services.procedure_extraction.schema import ExtractionResult

    async def fake_extract(pool, source, **kw):
        seen["source"] = source
        return ExtractionResult(validation_failures=["stop here"])

    monkeypatch.setattr(
        "app.services.procedure_extraction.extract_procedure", fake_extract)
    monkeypatch.setattr(ij, "_extraction_client", lambda: None)

    await ij.handle_extract_procedure_from_episode(
        WindowPool(), _payload())

    assert seen["sqls"], "no evidence was gathered at all"
    for sql in seen["sqls"]:
        assert 'te."timestamp" >= $2' in sql, "evidence query is not time-bounded"
        assert "$3" in sql, "evidence query has no episode end bound"
    for a in seen["args"]:
        assert a == ("s1", "T0", "T1")


@pytest.mark.asyncio
async def test_abstained_extraction_is_retired_immediately(monkeypatch):
    """grounded_hybrid_v1 degrading to deterministic leaves
    capability_statement == the goal seed. That row is noise and must not
    stay live. Observed 1 of 3 on the real corpus."""
    from app.services.procedure_extraction.schema import ExtractionResult

    from app.services.procedure_extraction.schema import ExtractedProcedure
    stub = ExtractedProcedure(
        name="x", goal="x",
        capability_statement="Fix the failing login test")

    class P(FakePool):
        async def fetchrow(self, sql, *a):
            return {"session_id": "s1", "start_ts": "T0", "end_ts": "T1",
                    "project_id": None}

    async def fake_extract(pool, source, **kw):
        return ExtractionResult(
            procedure_id="p-1",
            version_row_id="22222222-2222-2222-2222-222222222222",
            extracted_by="grounded_hybrid_v1@1", extracted=stub)

    monkeypatch.setattr(
        "app.services.procedure_extraction.extract_procedure", fake_extract)
    monkeypatch.setattr(ij, "_extraction_client", lambda: None)

    pool = P()
    await ij.handle_extract_procedure_from_episode(
        pool, _payload())

    retires = [c for c in pool.executed
               if "UPDATE procedures" in c[0] and "t_invalid" in c[0]]
    assert len(retires) == 1, "an abstained row must be retired"
    assert "22222222-2222-2222-2222-222222222222" in retires[0][1]


@pytest.mark.asyncio
async def test_real_abstraction_is_kept(monkeypatch):
    """The other half: a genuinely abstracted statement must NOT be
    retired. Real example from the corpus."""
    from app.services.procedure_extraction.schema import ExtractionResult

    from app.services.procedure_extraction.schema import ExtractedProcedure
    stub = ExtractedProcedure(
        name="x", goal="x",
        capability_statement=(
            "Implement a new feature or fix a bug through an iterative cycle "
            "of exploration, implementation, and rigorous testing."))

    class P(FakePool):
        async def fetchrow(self, sql, *a):
            return {"session_id": "s1", "start_ts": "T0", "end_ts": "T1",
                    "project_id": None}

    async def fake_extract(pool, source, **kw):
        return ExtractionResult(
            procedure_id="p-2", version_row_id="33333333-3333-3333-3333-333333333333",
            extracted_by="grounded_hybrid_v1@1", extracted=stub)

    monkeypatch.setattr(
        "app.services.procedure_extraction.extract_procedure", fake_extract)
    monkeypatch.setattr(ij, "_extraction_client", lambda: None)

    pool = P()
    await ij.handle_extract_procedure_from_episode(
        pool, _payload())
    assert not [c for c in pool.executed if "UPDATE procedures" in c[0]]


def test_goal_seed_is_the_source_derived_payload_value():
    import inspect
    src = inspect.getsource(ij.handle_extract_procedure_from_episode)
    assert 'goal_text=goal_text.strip()' in src
