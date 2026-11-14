"""An embedding failure in search is logged with the provider's error, at most once a minute per kind of failure
(app/services/retrieval_service._embedding_failed)."""
from __future__ import annotations

import logging

from app.services import retrieval_service as rs


class Embedder:
    def embedding_model_id(self):
        return "vertex:gemini-embedding-2"


def test_a_failure_is_logged_once_a_minute_with_the_held_back_count(caplog, monkeypatch):
    rs._embed_failures.clear()
    clock = [1000.0]
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock[0])
    err = RuntimeError("OpenRouter embedding failed: 402 Payment Required")
    with caplog.at_level(logging.WARNING, logger=rs.log.name):
        rs._embedding_failed("goal search", err, Embedder())
        rs._embedding_failed("goal search", err, Embedder())          # held back
        rs._embedding_failed("goal search", err, Embedder())          # held back
        rs._embedding_failed("procedure search", err, Embedder())     # another place: its own line
        clock[0] += 61
        rs._embedding_failed("goal search", err, Embedder())
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 3
    assert "goal search (vertex:gemini-embedding-2): RuntimeError: OpenRouter embedding failed: 402" in lines[0]
    assert lines[1].startswith("embedding failed in procedure search")
    assert lines[2].endswith("(2 more since the last line)")
