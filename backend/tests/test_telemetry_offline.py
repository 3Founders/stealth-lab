"""OpenTelemetry layer: coarse spans, canonical-event correlation, payload
hygiene, failure codes, sampling, and total inertness when disabled.

Fully offline: an in-memory exporter stands in for any backend; no DB, no
network, no Phoenix.
"""
from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app import telemetry as tel
from app.config import get_settings
from app.execution import durable_run as dr
from app.execution import recorder


@pytest.fixture
def mem():
    exporter = InMemorySpanExporter()
    tel.configure("test", exporter=exporter, sample_rate=1.0, benchmark=False,
                  batch=False, settings=get_settings())
    yield exporter
    tel.shutdown()


class FakeConn:
    def __init__(self):
        self.calls: list[tuple] = []

    async def execute(self, *args):
        self.calls.append(args)


def _by_name(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


# --------------------------------------------------------------- disabled

def test_disabled_is_inert_and_canonical_events_still_written():
    tel.shutdown()
    assert tel.is_enabled() is False
    with tel.span("anything", kind="CHAIN", run_id="r") as sp:
        tel.set_attrs(sp, x=1)
        tel.add_event("e")
    assert tel.current_ids() == (None, None)

    conn = FakeConn()
    asyncio.run(recorder.record_event(conn, execution_run_id="r1", event_type="run_started"))
    args = conn.calls[0]
    assert args[-2:] == (None, None)  # trace_id, span_id NULL; the row is still written


def test_init_without_enable_flag_does_nothing(monkeypatch):
    tel.shutdown()
    monkeypatch.setattr(get_settings(), "observability_enabled", False, raising=False)
    assert tel.init("api") is False
    assert tel.is_enabled() is False


# ------------------------------------------------------------ trace shape

def test_run_trace_has_children_and_correlates_with_canonical_events(mem, monkeypatch):
    conn = FakeConn()

    async def fake_drive(pool, run_id, *, worker_id, **kw):
        with tel.span("retrieval", kind="RETRIEVER", retrieval_stage="hybrid"):
            pass
        with tel.span("execution", kind="TOOL", node_id=0):
            await recorder.record_event(conn, execution_run_id=run_id, event_type="node_started",
                                        node_order=0)
        with tel.span("verification", kind="EVALUATOR"):
            pass
        return {"run_id": run_id, "status": "succeeded"}

    monkeypatch.setattr(dr, "_drive", fake_drive)
    asyncio.run(dr._traced_drive(None, "run-1", resumed=False, worker_id="w1", deps={}, run_node=None))

    spans = _by_name(mem)
    root = spans["stealth.run"]
    assert root.parent is None
    assert root.attributes["stealth.run_id"] == "run-1"
    assert root.attributes["openinference.span.kind"] == "AGENT"
    for child in ("retrieval", "execution", "verification"):
        assert spans[child].parent.span_id == root.context.span_id
        assert spans[child].context.trace_id == root.context.trace_id

    # canonical event carries the ids of the span it was written under
    *_, trace_id, span_id = conn.calls[0]
    assert trace_id == format(root.context.trace_id, "032x")
    assert span_id == format(spans["execution"].context.span_id, "016x")


def test_failed_run_is_marked_and_searchable(mem, monkeypatch):
    async def fake_drive(pool, run_id, *, worker_id, **kw):
        return {"run_id": run_id, "status": "failed"}

    monkeypatch.setattr(dr, "_drive", fake_drive)
    asyncio.run(dr._traced_drive(None, "run-2", resumed=True, worker_id="w", deps={}, run_node=None))
    root = _by_name(mem)["stealth.run"]
    assert root.status.status_code == StatusCode.ERROR
    assert root.attributes["stealth.failure_code"] == tel.FailureCode.EXECUTION_ERROR
    assert root.attributes["stealth.resumed"] is True


def test_exception_sets_stable_failure_code_and_propagates(mem):
    with pytest.raises(RuntimeError):
        with tel.span("retrieval.lexical", on_error=tel.FailureCode.RETRIEVAL_ERROR):
            raise RuntimeError("boom secret-detail")
    s = _by_name(mem)["retrieval.lexical"]
    assert s.status.status_code == StatusCode.ERROR
    assert s.attributes["stealth.failure_code"] == "RETRIEVAL_ERROR"
    assert "secret-detail" not in str(dict(s.attributes)) and "secret-detail" not in (s.status.description or "")


def test_model_timeout_maps_to_model_timeout(mem):
    with pytest.raises(TimeoutError):
        with tel.span("llm.chat", on_error=tel.FailureCode.MODEL_ERROR):
            raise TimeoutError()
    assert _by_name(mem)["llm.chat"].attributes["stealth.failure_code"] == "MODEL_TIMEOUT"


def test_verification_failure_marked_without_exception(mem):
    with tel.span("verification", kind="EVALUATOR") as sp:
        tel.fail(sp, tel.FailureCode.VERIFICATION_FAILED)
    s = _by_name(mem)["verification"]
    assert s.status.status_code == StatusCode.ERROR
    assert s.attributes["stealth.force_keep"] is True


# ----------------------------------------------------------- payload hygiene

def test_large_payloads_never_land_in_span_attributes(mem):
    with tel.span("retrieval", attributes={
        "prompt": "x" * 50_000, "docs": ["doc"] * 500, "candidates": [{"id": 1}],
        "blob": b"\x00" * 1000, "candidate_count": 7,
    }, claim_text="y" * 10_000):
        pass
    attrs = dict(_by_name(mem)["retrieval"].attributes)
    assert attrs["stealth.candidate_count"] == 7
    assert "stealth.docs" not in attrs and "stealth.candidates" not in attrs and "stealth.blob" not in attrs
    for v in attrs.values():
        assert not isinstance(v, str) or len(v) <= 210


def test_artifact_reference_carries_pointer_not_payload(mem):
    with tel.span("execution") as sp:
        tel.add_event("artifact_recorded", **tel.artifact_attrs(
            artifact_id="a1", uri="s3://b/k", content_hash="abc", size=123, content_type="text/plain"))
    ev = _by_name(mem)["execution"].events[0]
    assert ev.attributes["artifact.uri"] == "s3://b/k"
    assert ev.attributes["artifact.size"] == 123


# ------------------------------------------------------------ retrieval spans

def test_retrieval_spans_carry_counts_and_rank_changes(mem):
    from app.services import applicability as app_

    async def go():
        async def rows():
            return [1, 2, 3]
        assert await app_._leg("lexical", rows()) == [1, 2, 3]
        before = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        after = [{"id": "c", "_similarity_score": 0.9}, {"id": "a"}]
        with tel.span("retrieval.rerank", kind="RERANKER") as sp:
            app_._emit_rank_changes(sp, before, after)

    asyncio.run(go())
    spans = _by_name(mem)
    lex = spans["retrieval.lexical"]
    assert lex.attributes["stealth.candidate_count"] == 3
    assert lex.attributes["stealth.retrieval_stage"] == "lexical"
    assert lex.attributes["stealth.shard_id"]
    assert lex.attributes["openinference.span.kind"] == "RETRIEVER"
    rr = spans["retrieval.rerank"]
    assert rr.attributes["stealth.rank_changed_count"] == 2
    first = rr.events[0].attributes
    assert (first["stealth.candidate_rank_before"], first["stealth.candidate_rank_after"]) == (2, 0)
    assert "similarity" not in "".join(first.keys()) or first["stealth.similarity"] == 0.9


# -------------------------------------------------------------- model calls

class _Usage:
    prompt_tokens, completion_tokens = 1000, 500


class _Resp:
    usage = _Usage()


class _FakeCompletions:
    def create(self, *, model, messages):
        return _Resp()


class _FakeAsyncCompletions:
    async def create(self, *, model, messages):
        return _Resp()


def test_llm_calls_record_tokens_and_cost(mem, monkeypatch):
    monkeypatch.setattr(get_settings(), "stealth_model_prices", '{"gemma": [1.0, 2.0]}', raising=False)
    sync_cls, async_cls = _FakeCompletions, _FakeAsyncCompletions
    sync_create = tel.instrument_callable(sync_cls.create, provider_of=lambda _s: "google")
    async_create = tel.instrument_callable(async_cls.create, provider_of=lambda _s: "google")
    sync_create(sync_cls(), model="gemma-4-31B-it", messages=[{"role": "user", "content": "SECRET PROMPT"}])
    asyncio.run(async_create(async_cls(), model="gemma-4-31B-it", messages=[]))

    spans = mem.get_finished_spans()
    assert len(spans) == 2
    for s in spans:
        a = s.attributes
        assert a["stealth.tokens_input"] == 1000 and a["stealth.tokens_output"] == 500
        assert a["stealth.model"] == "gemma-4-31B-it" and a["llm.provider"] == "google"
        assert a["stealth.cost_usd"] == pytest.approx((1000 * 1.0 + 500 * 2.0) / 1e6)
        assert "SECRET PROMPT" not in str(dict(a))


def test_cost_is_omitted_not_guessed_without_a_price(mem, monkeypatch):
    monkeypatch.setattr(get_settings(), "stealth_model_prices", None, raising=False)
    create = tel.instrument_callable(_FakeCompletions.create)
    create(_FakeCompletions(), model="unpriced", messages=[])
    a = mem.get_finished_spans()[0].attributes
    assert a["stealth.tokens_input"] == 1000 and "stealth.cost_usd" not in a


def test_llm_error_is_marked(mem):
    class Boom:
        def create(self, **_k):
            raise TimeoutError()

    create = tel.instrument_callable(Boom.create)
    with pytest.raises(TimeoutError):
        create(Boom(), model="m")
    a = mem.get_finished_spans()[0].attributes
    assert a["stealth.failure_code"] == "MODEL_TIMEOUT"


# ----------------------------------------------------------- exporter failure

class _ExplodingExporter(SpanExporter):
    def export(self, spans):
        raise ConnectionError("collector down")

    def shutdown(self):
        raise ConnectionError("collector down")


def test_exporter_failure_never_breaks_the_application():
    tel.configure("test", exporter=_ExplodingExporter(), sample_rate=1.0, batch=False,
                  settings=get_settings())
    try:
        with tel.span("stealth.run"):
            pass  # must not raise even though export raises on span end
    finally:
        tel.shutdown()


def test_tail_keep_exporter_swallows_inner_failure():
    exp = tel._TailKeepExporter(_ExplodingExporter(), 1.0)
    tel.configure("test", exporter=exp, sample_rate=1.0, batch=False, settings=get_settings())
    try:
        with tel.span("stealth.run"):
            pass
    finally:
        tel.shutdown()  # shutdown of a failing exporter must not raise either


# --------------------------------------------------------------- sampling

def _sampled(rate, benchmark=False):
    mem = InMemorySpanExporter()
    tel.configure("test", exporter=mem, sample_rate=rate, benchmark=benchmark, batch=False,
                  settings=get_settings())
    return mem


def test_routine_success_is_minimal_trace_failure_is_full():
    mem = _sampled(0.0)
    try:
        with tel.span("stealth.run"):
            with tel.span("retrieval"):
                pass
        assert [s.name for s in mem.get_finished_spans()] == ["stealth.run"]

        mem.clear()
        with pytest.raises(ValueError):
            with tel.span("stealth.run"):
                with tel.span("retrieval"):
                    pass
                with tel.span("execution"):
                    raise ValueError("x")
        assert {s.name for s in mem.get_finished_spans()} == {"stealth.run", "retrieval", "execution"}

        mem.clear()
        with tel.span("stealth.run"):
            with tel.span("verification") as sp:
                tel.fail(sp, tel.FailureCode.VERIFICATION_FAILED)
        assert len(mem.get_finished_spans()) == 2  # verification failure keeps the full trace
    finally:
        tel.shutdown()


def test_benchmark_runs_keep_full_trace_and_rate_is_honoured():
    mem = _sampled(0.0, benchmark=True)
    try:
        with tel.span("stealth.run"):
            with tel.span("retrieval"):
                pass
        assert len(mem.get_finished_spans()) == 2
    finally:
        tel.shutdown()

    exp = InMemorySpanExporter()
    tail = tel._TailKeepExporter(exp, 0.5, rng=iter([0.1, 0.9]).__next__)
    tel.configure("test", exporter=tail, sample_rate=1.0, batch=False, settings=get_settings())
    try:
        for _ in range(2):
            with tel.span("stealth.run"):
                with tel.span("retrieval"):
                    pass
        assert len(exp.get_finished_spans()) == 3  # first trace full (2), second root only (1)
    finally:
        tel.shutdown()


def test_sampling_never_affects_canonical_events():
    _sampled(0.0)
    conn = FakeConn()
    try:
        async def go():
            with tel.span("stealth.run"):
                for et in ("run_started", "candidates_reranked", "run_finalized"):
                    await recorder.record_event(conn, execution_run_id="r", event_type=et)
        asyncio.run(go())
    finally:
        tel.shutdown()
    assert [c[3] for c in conn.calls] == ["run_started", "candidates_reranked", "run_finalized"]
    assert all(c[-2] for c in conn.calls)  # trace_id present even for a sampled-out trace


def test_new_retrieval_event_types_are_recordable_and_versioned():
    conn = FakeConn()

    async def go():
        await recorder.record_claims_retrieved(conn, "r", candidate_claim_counts={"p1": 2, "p2": 3})
        await recorder.record_candidates_reranked(
            conn, "r", stage="nli_jev_rerank",
            ranking=[{"candidate_id": "p1", "rank_before": 1, "rank_after": 0, "score": 0.8}])
        await recorder.record_candidate_rejected(conn, "r", candidate_id="p2", stage="nli_jev",
                                                 verdict="CONTRADICTED", contradiction_probability=0.95)
    asyncio.run(go())
    types = [c[3] for c in conn.calls]
    assert types == ["claims_retrieved", "candidates_reranked", "candidate_rejected"]
    assert all(c[5] == recorder.EVENT_VERSION for c in conn.calls)
    assert conn.calls[0][4]["claim_count"] == 5
    with pytest.raises(ValueError):
        asyncio.run(recorder.record_event(conn, execution_run_id="r", event_type="not_a_type"))
