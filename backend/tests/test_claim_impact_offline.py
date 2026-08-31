"""
Offline tests for app/services/claim_impact.py.

Deliberately thin, per task #35's own instructions: a real JSONB
containment query (`preconditions @> $1::jsonb`) is genuinely hard to
fake meaningfully -- a hand-rolled FakePool can assert what SQL string
and params were passed, but cannot itself evaluate Postgres JSONB
containment semantics, so it would prove only "the right-looking string
was sent", not "the query is correct". That correctness is what
`tests/test_claim_impact_e2e.py` proves for real, against real Postgres,
including a real negative case (unrelated claim_id, no-claim precondition)
and a real positive case (staleness column actually flips).

What IS worth proving offline, without a real database, is
`propagate_claim_change`'s own orchestration logic -- independent of
whether the underlying SQL is faked correctly: it calls
`find_procedures_referencing_claim` exactly once, then calls the real
`mark_procedure_stale` once per affected procedure with the right
keyword arguments, returns the list of processed ids, and does none of
that (no call to mark_procedure_stale, `[]` returned) when zero
procedures are affected. Proven here by monkeypatching
`claim_impact.find_procedures_referencing_claim` and
`claim_impact.mark_procedure_stale` directly rather than a fake pool --
those two functions are exactly this module's two real seams.
"""
from __future__ import annotations

import asyncio

from app.services import claim_impact


class _SentinelPool:
    """Never actually touched by propagate_claim_change once its two
    real DB-calling collaborators are monkeypatched below; exists only so
    call sites that expect a `pool` positional argument have something to
    pass."""


def test_propagate_claim_change_calls_mark_procedure_stale_once_per_affected_procedure(monkeypatch):
    calls: list[dict] = []

    async def fake_find(pool, claim_id):
        assert claim_id == "claim-123"
        return [{"id": "proc-a", "name": "Proc A"}, {"id": "proc-b", "name": "Proc B"}]

    async def fake_mark_stale(pool, *, procedure_row_id, reason, detected_by):
        calls.append({"procedure_row_id": procedure_row_id, "reason": reason, "detected_by": detected_by})
        return {"id": procedure_row_id, "staleness": "stale"}

    monkeypatch.setattr(claim_impact, "find_procedures_referencing_claim", fake_find)
    monkeypatch.setattr(claim_impact, "mark_procedure_stale", fake_mark_stale)

    async def _run():
        return await claim_impact.propagate_claim_change(
            _SentinelPool(), "claim-123",
            reason="claim contradicted", detected_by="orchestrator",
        )

    result = asyncio.run(_run())

    assert result == ["proc-a", "proc-b"]
    assert len(calls) == 2
    assert calls[0] == {"procedure_row_id": "proc-a", "reason": "claim contradicted", "detected_by": "orchestrator"}
    assert calls[1] == {"procedure_row_id": "proc-b", "reason": "claim contradicted", "detected_by": "orchestrator"}


def test_propagate_claim_change_is_a_noop_when_nothing_references_the_claim(monkeypatch):
    async def fake_find(pool, claim_id):
        return []

    async def fake_mark_stale(pool, **kwargs):
        raise AssertionError("mark_procedure_stale must not be called when nothing references the claim")

    monkeypatch.setattr(claim_impact, "find_procedures_referencing_claim", fake_find)
    monkeypatch.setattr(claim_impact, "mark_procedure_stale", fake_mark_stale)

    async def _run():
        return await claim_impact.propagate_claim_change(
            _SentinelPool(), "unreferenced-claim",
            reason="irrelevant", detected_by="orchestrator",
        )

    result = asyncio.run(_run())
    assert result == []


def test_propagate_claim_change_default_detected_by():
    """`detected_by` defaults to this module's own name when a caller
    doesn't pass one -- matches the signature's default value."""
    import inspect

    sig = inspect.signature(claim_impact.propagate_claim_change)
    assert sig.parameters["detected_by"].default == "claim_impact"
