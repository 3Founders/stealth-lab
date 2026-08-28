"""Band 1.11 proving tests -- server-side redaction chokepoint.

Contract under test (ROADMAP Band 1.11): trace_redaction runs BEFORE any
payload persists on the ingestion path. The collector redacts client-side,
but _insert_event is the last stop before the one irreversible step, and a
hand-crafted collector file from any source reaches it unredacted. These
tests prove the chokepoint at the worker boundary:

1. A record whose tool_output carries known-token secrets (AWS key,
   Anthropic key, bearer) persists ONLY redacted forms -- the raw secret
   appears in no SQL argument.
2. A record whose tool_input references a sensitive path (.ssh/id_rsa)
   gets wholesale path exclusion (tool_input AND tool_output become the
   placeholder), the cross-field case a per-leaf check cannot decide.
3. The caller-supplied dedup_key passes through untouched (ON CONFLICT
   semantics unchanged by redaction).
4. Idempotency: redact_event is stable over already-redacted content --
   double-processing a collector file must not mutate payloads further.
5. Clean events gain no _redaction metadata and are byte-identical.
"""
from __future__ import annotations

import json

import pytest

from app.services.trace_redaction import redact_event
from app.services.trace_worker import _insert_event


class FakeConn:
    """Captures the fetchval call; returns a fixed id like a real insert."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        self.calls.append((sql, args))
        return "evt-1"


def _record(event_overrides: dict | None = None) -> dict:
    event = {
        "timestamp": "2026-08-25T12:00:00Z",
        "actor_id": "agent_7",
        "event_type": "tool_call",
        "success": True,
        "tool_name": "Read",
        "tool_call_id": "call_123",
    }
    event.update(event_overrides or {})
    return {
        "trace_id": "trace_abc",
        "session_id": "sess_1",
        "sequence": 1,
        "event_type": "tool_call",
        "dedup_key": "dedup-constant-value",
        "event": event,
    }


@pytest.mark.asyncio
async def test_known_token_secrets_persist_only_redacted():
    conn = FakeConn()
    record = _record({
        "tool_name": "Bash",
        "tool_input": {"command": "export KEY=AKIAABCDEFGHIJKLMNOP"},
        "tool_output": {"content": "ok sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 done"},
    })
    returned = await _insert_event(conn, record)

    assert returned == "evt-1"
    sql, args = conn.calls[0]
    # zero-indexed args: $9 tool_input -> args[8], $10 tool_output -> args[9]
    tool_input_arg, tool_output_arg = args[8], args[9]
    assert "AKIAABCDEFGHIJKLMNOP" not in tool_input_arg
    assert "[REDACTED:aws_access_key]" in tool_input_arg
    assert "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in tool_output_arg
    assert "[REDACTED:anthropic_key]" in tool_output_arg
    # the raw secret appears in NO argument at all
    for arg in args:
        assert "AKIAABCDEFGHIJKLMNOP" not in str(arg)


@pytest.mark.asyncio
async def test_sensitive_path_gets_wholesale_exclusion():
    conn = FakeConn()
    record = _record({
        "tool_name": "Read",
        "tool_input": {"file_path": "/home/dev/.ssh/id_rsa"},
        "tool_output": {"content": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"},
    })
    await _insert_event(conn, record)

    _, args = conn.calls[0]
    tool_input_arg, tool_output_arg = args[8], args[9]
    assert "[EXCLUDED: sensitive path]" in tool_input_arg
    assert "[EXCLUDED: sensitive path]" in tool_output_arg
    assert "id_rsa" not in tool_input_arg
    assert "OPENSSH PRIVATE KEY" not in tool_output_arg


@pytest.mark.asyncio
async def test_dedup_key_passes_through_untouched():
    conn = FakeConn()
    record = _record({
        "tool_output": {"content": "token xoxb-ABCDEFGHIJ1234567890"},
    })
    await _insert_event(conn, record)

    _, args = conn.calls[0]
    assert args[11] == "dedup-constant-value"  # column $12 dedup_key


def test_idempotent_over_already_redacted_content():
    ev = {
        "tool_name": "Bash",
        "tool_input": {"command": "export KEY=AKIAABCDEFGHIJKLMNOP"},
        "tool_output": {"content": "bearer ABCDEFGHIJKLMNOPQRSTUVWX done"},
    }
    once = redact_event(ev)
    twice = redact_event(once)
    assert once == twice
    # and the metadata signal does not duplicate or grow
    assert once["_redaction"] == twice.get("_redaction", once["_redaction"])


def test_clean_event_unchanged_and_no_metadata():
    ev = {
        "tool_name": "Read",
        "tool_input": {"file_path": "src/app/main.py"},
        "tool_output": {"content": "print('hello world')"},
    }
    out = redact_event(ev)
    assert "_redaction" not in out
    assert out["tool_input"] == ev["tool_input"]
    assert out["tool_output"] == ev["tool_output"]


@pytest.mark.asyncio
async def test_tool_response_field_normalized_and_redacted():
    """A6 companion: hooks may send the result as tool_response; the
    chokepoint must normalize + redact whichever field arrives."""
    conn = FakeConn()
    record = _record({
        "tool_response": {"content": "failed with stripe key sk_test_ABCDEFGHIJKLMNOPQRSTUV"},
    })
    await _insert_event(conn, record)

    _, args = conn.calls[0]
    tool_output_arg = args[9]
    assert "sk_test_ABCDEFGHIJKLMNOPQRSTUV" not in tool_output_arg
    assert "[REDACTED:stripe_key]" in tool_output_arg
