import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

from app.local_agent.local_store import LocalProcedureStore, LocalProcedureNotFound
from personal_library import format_list, format_search, format_show, format_publish_result


@pytest.fixture
def store(tmp_path):
    return LocalProcedureStore(db_path=str(tmp_path / "local_procedures.db"))


def _capture(store, name, goal, **kw):
    kw.setdefault("provenance", "prior_library")
    kw.setdefault("scope_type", "user")
    kw.setdefault("scope_entity_id", "me")
    return store.capture_local_procedure(name=name, goal=goal, **kw)


def test_format_list_empty(store):
    assert format_list(store) == "(no local procedures captured yet)"


def test_format_list_with_data(store):
    _capture(store, "pandas append rows", "append rows to a dataframe", steps=["a", "b"])
    _capture(store, "git rebase cleanup", "squash local commits", steps=["c"])
    out = format_list(store)
    assert "pandas append rows" in out
    assert "append rows to a dataframe" in out
    assert "git rebase cleanup" in out
    assert "candidate" in out
    assert "fresh" in out
    assert "no" in out  # unpublished


def test_format_list_reflects_recorded_outcomes(store):
    row = _capture(store, "pandas append rows", "append rows to a dataframe")
    store.record_local_execution_outcome(
        row_id=row["id"], success=True, context_key="ctx-1", steps_used=3,
    )
    store.record_local_execution_outcome(
        row_id=row["id"], success=True, context_key="ctx-2", steps_used=5,
    )
    out = format_list(store)
    lines = [l for l in out.splitlines() if "pandas append rows" in l]
    assert len(lines) == 1
    assert "2" in lines[0]  # attempts
    # distinct_contexts column also 2
    parts = lines[0].split()
    assert "2" in parts


def test_format_search_matches(store):
    _capture(store, "pandas append rows", "append rows to a dataframe")
    _capture(store, "git rebase cleanup", "squash local commits")
    out = format_search(store, "pandas append")
    assert "pandas append rows" in out
    assert "append rows to a dataframe" in out
    assert "git rebase cleanup" not in out


def test_format_search_no_match(store):
    _capture(store, "pandas append rows", "append rows to a dataframe")
    out = format_search(store, "kubernetes")
    assert "no local procedures match" in out
    assert "kubernetes" in out


def test_format_show_real_row(store):
    row = _capture(
        store, "pandas append rows", "append rows to a dataframe",
        steps=["read csv", "append row", "write csv"],
    )
    store.record_local_execution_outcome(
        row_id=row["id"], success=True, context_key="ctx-1", steps_used=3,
    )
    store.record_local_execution_outcome(
        row_id=row["id"], success=False, context_key="ctx-1",
    )
    out = format_show(store, row["id"])
    assert "pandas append rows" in out
    assert "append rows to a dataframe" in out
    assert "candidate" in out
    assert "fresh" in out
    assert "attempts=2" in out
    assert "successes=1" in out
    assert "read csv" in out
    assert "append row" in out
    assert "write csv" in out
    assert "published:         no" in out


def test_format_show_missing_raises(store):
    with pytest.raises(LocalProcedureNotFound):
        format_show(store, "does-not-exist")


def test_format_show_published_row(store):
    row = _capture(store, "pandas append rows", "append rows to a dataframe")
    store.mark_local_procedure_published(
        row["id"],
        global_procedure_id="global-proc-1",
        global_procedure_row_id="global-row-1",
        published_by="chaitanyad3shkar@gmail.com",
    )
    out = format_show(store, row["id"])
    assert "published:         yes" in out
    assert "global-proc-1" in out
    assert "global-row-1" in out
    assert "chaitanyad3shkar@gmail.com" in out


def test_format_publish_result():
    out = format_publish_result(
        "local-row-1", {"procedure_id": "global-proc-1", "id": "global-row-1"},
    )
    assert "local-row-1" in out
    assert "global-proc-1" in out
    assert "global-row-1" in out
    assert "pending independent validation" in out
