"""The Way page is public, and the public view leaks nothing.

A Way page is a sales page: someone with no account has to be able to read the output and the price, or they can
never decide to buy. The risk that comes with that is obvious — "make it public" is how an owner's identity, their
unpublished drafts and their reviewer's private feedback end up on the open web.

So these tests assert both halves together: a stranger gets a useful page, and does not get what is not theirs.
They drive `api.get_public` through a fake store, because the rule under test is the projection, not the SQL.
"""
from __future__ import annotations

import pytest

from app.submissions import api, store

OWNER = "owner-subject-1234"


def _row(status: str = "approved") -> dict:
    """A submission row shaped exactly like store.get() returns one, including the fields a stranger must not see."""
    return {
        "id": "8f2c0000-0000-4000-8000-000000000001",
        "owner_id": OWNER,                       # private: identifies the author
        "goal_id": "9a1b0000-0000-4000-8000-000000000002",   # private: internal handle
        "goal_name": "Refund a captured payment",
        "description": "Reverse a charge and reconcile the ledger.",
        "mode": "human_ai",
        "mode_label": "Human+AI",
        "status": status,
        "parent_procedure_id": "b21d8e04-11c7-4a3e-9f55-6c8d2e7a1b30",
        "created_at": "2026-09-14T09:12:00+00:00",
        "updated_at": "2026-11-02T16:40:00+00:00",
        "notified_at": None,
        "steps": [
            {
                "order": 1, "kind": "automated", "title": "Find the payment", "description": "Look it up.",
                "prompt": "Read the order.", "locator": {"type": "tool", "value": "stripe.x"},
                "goal_id": "9a1b...", "procedure_row_id": "c0ffee00-0000-4000-8000-000000000003",
                "goal_match": {"decision": "match"}, "status": "approved", "reason": "",
                "reviewer": "kell-reviewer", "revision": 1, "reviewed_at": "2026-09-14T09:30:00+00:00",
            }
        ],
    }


@pytest.fixture
def fake_store(monkeypatch):
    """A store whose answer depends on the owner filter, exactly like the real one (owner_id=None = reviewer view).

    `store.get` is async in production, so the fake must be too — a sync fake would let a missing `await` in the
    code under test pass unnoticed, which is precisely the bug a fake is supposed to catch.
    """
    async def fake_get(pool, submission_id, *, owner_id=None):
        row = _row()
        if owner_id is not None and owner_id != OWNER:
            return None            # someone else's Way does not exist for this viewer
        return row

    monkeypatch.setattr(store, "get", fake_get)
    return fake_get


def _set_row(monkeypatch, row: dict) -> None:
    """Point the fake store at a specific row, keeping it async."""
    async def fake_get(pool, submission_id, *, owner_id=None):
        return row

    monkeypatch.setattr(store, "get", fake_get)


@pytest.mark.asyncio
async def test_a_stranger_can_read_a_published_way(fake_store):
    out = await api.get_public(None, None, "8f2c0000-0000-4000-8000-000000000001")
    assert out is not None
    assert out["goal_name"] == "Refund a captured payment"
    assert out["steps"][0]["title"] == "Find the payment"


@pytest.mark.asyncio
async def test_the_public_view_never_leaks_the_owner_or_internal_ids(fake_store):
    out = await api.get_public(None, None, "8f2c0000-0000-4000-8000-000000000001")
    # The author's identity, the internal goal handle and the procedure row id are not the public's business.
    assert "owner_id" not in out
    assert "goal_id" not in out
    assert "notified_at" not in out
    assert "goal_match" not in out
    step = out["steps"][0]
    assert "procedure_row_id" not in step
    assert "goal_id" not in step
    assert "reviewer" not in step
    assert "goal_match" not in step


@pytest.mark.asyncio
async def test_an_unpublished_way_is_not_public(fake_store, monkeypatch):
    for status in ("reviewing", "needs_changes", "withdrawn"):
        _set_row(monkeypatch, _row(status=status))
        out = await api.get_public(None, None, "any")
        # A draft is the author's business: it is not readable by a stranger, and the page says so (404).
        assert out is None, f"{status} must not be publicly readable"


@pytest.mark.asyncio
async def test_the_owner_still_gets_their_full_record(fake_store):
    out = await api.get_public(OWNER, OWNER, "8f2c0000-0000-4000-8000-000000000001")
    # Signing in must not lose information: the author sees the same record as before this change.
    assert out is not None
    assert out["owner_id"] == OWNER
    assert out["steps"][0]["reviewer"] == "kell-reviewer"


@pytest.mark.asyncio
async def test_a_published_way_is_public_even_to_a_signed_in_stranger(fake_store):
    """Signed in as somebody else: you get the public view, not the owner's record."""
    other = "some-other-person"
    out = await api.get_public(other, other, "8f2c0000-0000-4000-8000-000000000001")
    assert out is not None
    assert "owner_id" not in out


@pytest.mark.asyncio
async def test_a_stranger_always_reads_licence_status_none(fake_store):
    out = await api.get_public(None, None, "x")
    # The page needs the field to decide whether to draw the buy button; a stranger has bought nothing.
    assert out["licence_status"] == "none"


@pytest.mark.asyncio
async def test_an_ai_approved_way_is_also_public(fake_store, monkeypatch):
    _set_row(monkeypatch, _row(status="ai_approved"))
    out = await api.get_public(None, None, "x")
    assert out is not None
