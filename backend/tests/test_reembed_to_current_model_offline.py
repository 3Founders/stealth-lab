"""Offline: the re-embed to the current model is model-aware, guarded, resumable and honest about failures.
Hand-rolled fake pool/embedder (the repo's convention): no database, no provider."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
rec = importlib.import_module("reembed_to_current_model")

TARGET = "vertex:gemini-embedding-2"
DIM = 4


def _run(coro):
    return asyncio.run(coro)


class _Embedder:
    dimension = DIM

    def __init__(self, fail_text=None, batch_fails=False):
        self.calls = []
        self.fail_text, self.batch_fails = fail_text, batch_fails

    def embedding_model_id(self):
        return TARGET

    def _configured_provider(self):
        return "vertex"

    async def embed(self, texts, input_type="document"):
        self.calls.append(list(texts))
        assert input_type == "document"
        if self.batch_fails and len(texts) > 1:
            raise RuntimeError("batch blew up")
        if self.fail_text and self.fail_text in texts:
            raise RuntimeError("row blew up")
        return [[0.1] * DIM for _ in texts]


class _Pool:
    def __init__(self, rows, touched="UPDATE 1"):
        self.rows, self.touched = rows, touched
        self.selects = []
        self.updates = []

    async def fetch(self, sql, *args):
        self.selects.append((" ".join(sql.split()), args))
        return self.rows

    async def execute(self, sql, *args):
        self.updates.append((" ".join(sql.split()), args))
        return self.touched


GOALS = [
    {"id": "g1", "canonical_name": "Deploy a service", "description": None, "embedding_model_id": "vertex:gemini-embedding-001"},
    {"id": "g2", "canonical_name": "Rotate a key", "description": "safely", "embedding_model_id": "gemini:gemini-embedding-001"},
]
CLAIMS = [{"id": "c1", "name": "Repo uses pnpm", "embedding_model_id": None}]


def test_selection_is_model_aware_and_live_only():
    pool = _Pool(GOALS)
    _run(rec.reembed_kind(pool, _Embedder(), "goals"))
    sql, args = pool.selects[0]
    assert "t_invalid IS NULL" in sql and "embedding IS NOT NULL" in sql
    assert "embedding_model_id IS DISTINCT FROM $1" in sql and args == (TARGET,), "rows already on the target are never selected"


def test_goals_use_the_canonical_goal_text_and_stamp_model_provider_and_hash():
    pool, emb = _Pool(GOALS), _Embedder()
    stats = _run(rec.reembed_kind(pool, emb, "goals"))
    assert emb.calls == [["Deploy a service", "Rotate a key\nsafely"]], "name, plus description when present -- one batch"
    assert stats == {"selected": 2, "reembedded": 2, "failed": 0, "skipped_changed": 0}
    sql, args = pool.updates[0]
    assert "embedding_model_id = $3" in sql and "embedding_provider = $4" in sql and "embedding_text_hash = $5" in sql
    assert args[2] == TARGET and args[3] == "vertex" and len(args[4]) == 64


def test_the_update_only_overwrites_a_row_still_on_the_old_model():
    pool = _Pool(GOALS)
    _run(rec.reembed_kind(pool, _Embedder(), "goals"))
    sql, args = pool.updates[0]
    assert "embedding_model_id IS NOT DISTINCT FROM $6" in sql and args[5] == "vertex:gemini-embedding-001"


def test_claims_embed_their_statement_and_record_the_dimension():
    pool, emb = _Pool(CLAIMS), _Embedder()
    _run(rec.reembed_kind(pool, emb, "claims"))
    assert emb.calls == [["Repo uses pnpm"]]
    sql, args = pool.updates[0]
    assert "knowledge_nodes" in sql and "embedding_dim = $4" in sql and args[3] == DIM and args[4] is None
    assert "IS NOT DISTINCT FROM $5" in sql, "a claim with no recorded model (None) is matched safely"


def test_a_failed_row_keeps_its_old_vector_and_is_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(rec, "_FAIL_LOG", tmp_path / "failed.jsonl")
    pool, emb = _Pool(GOALS), _Embedder(fail_text="Rotate a key\nsafely", batch_fails=True)
    stats = _run(rec.reembed_kind(pool, emb, "goals"))
    assert stats["reembedded"] == 1 and stats["failed"] == 1
    assert len(pool.updates) == 1 and pool.updates[0][1][0] == "g1", "the failed row was NOT updated"
    assert '"id": "g2"' in (tmp_path / "failed.jsonl").read_text(encoding="utf-8")


def test_a_statement_that_touches_no_row_is_counted_not_ignored():
    stats = _run(rec.reembed_kind(_Pool(GOALS, touched="UPDATE 0"), _Embedder(), "goals"))
    assert stats["reembedded"] == 0 and stats["skipped_changed"] == 2


def test_rows_are_batched():
    rows = [{"id": f"g{i}", "canonical_name": f"goal {i}", "description": None, "embedding_model_id": "old"} for i in range(150)]
    emb = _Embedder()
    _run(rec.reembed_kind(_Pool(rows), emb, "goals", batch=64))
    assert [len(c) for c in emb.calls] == [64, 64, 22]


def test_limit_bounds_a_run():
    rows = [{"id": f"g{i}", "canonical_name": f"goal {i}", "description": None, "embedding_model_id": "old"} for i in range(10)]
    stats = _run(rec.reembed_kind(_Pool(rows), _Embedder(), "goals", limit=3))
    assert stats["selected"] == 3


# ------------------------------------------------------------------------------------------------ the CLI contract

class _CountPool(_Pool):
    async def fetch(self, sql, *args):
        if "GROUP BY" in sql:
            return [{"model": "vertex:gemini-embedding-001", "n": 5}]
        return await super().fetch(sql, *args)


def _args(**kw):
    base = dict(apply=False, expect_model=None, only=None, limit=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_the_default_is_a_plan_and_writes_nothing(capsys):
    pool = _CountPool([])
    code = _run(rec.run(_args(), pool=pool, embedder=_Embedder()))
    assert code == 0 and pool.updates == []
    out = capsys.readouterr().out
    assert "PLAN ONLY" in out and "vertex:gemini-embedding-001 x5" in out and TARGET in out
    assert "procedures:" in out, "the plan shows procedures too, though another job converts them"


def test_apply_refuses_when_expect_model_is_not_what_the_environment_resolves_to(capsys):
    pool = _CountPool(GOALS)
    code = _run(rec.run(_args(apply=True, expect_model="vertex:gemini-embedding-001"), pool=pool, embedder=_Embedder()))
    assert code == 2 and pool.updates == [] and "REFUSED" in capsys.readouterr().out


def test_apply_without_expect_model_is_a_usage_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    with pytest.raises(SystemExit):
        rec.main(["--apply"])


def test_the_host_is_printed_and_the_dsn_never_is(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:s3cret@db.example.internal/prod")
    _run(rec.run(_args(), pool=_CountPool([]), embedder=_Embedder()))
    out = capsys.readouterr().out
    assert "db.example.internal" in out and "s3cret" not in out and "user:" not in out
