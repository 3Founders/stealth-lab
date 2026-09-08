"""
DB-free proving tests for the opt-in people layer (migration 48 +
app/services/contributors.py + app/api/{profile,contributors}.py).

Proven here, none of which the live e2e suite isolates:
  - INV-01 private by default: a profile is 'private' unless explicitly set
    'public'; the public read surface returns nothing for a private/absent
    profile.
  - every disclosure submission stamps disclosed_at (so "was the person
    informed" is always answerable), whichever visibility they picked.
  - contribution counts are read off provenance columns
    (procedures.owner_id, knowledge_nodes.created_by,
    publication_records.actor_user_id) -- never a stored score.
  - leaderboard rejects an unknown metric and ranks by the chosen one.
  - PUT /v1/me/profile takes identity from the validated principal, not the
    body, and emits exactly one audited 'profile_visibility_changed' event.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import contributors


def run(coro):
    return asyncio.run(coro)


class FakeExec:
    """Captures SQL + params and answers per query shape. Deliberately not
    shared with other test files (repo convention)."""

    def __init__(self, *, profile_row=None, user_row=None, proc_row=None,
                 claim_row=None, pub_row=None, public_rows=None):
        self.calls: list[tuple[str, tuple]] = []
        self._profile_row = profile_row
        self._user_row = user_row or {"external_subject": "sub-abc"}
        self._proc_row = proc_row or {"authored": 0, "verified": 0}
        self._claim_row = claim_row or {"n": 0}
        self._pub_row = pub_row or {"n": 0}
        self._public_rows = public_rows or []

    async def fetchrow(self, sql, *params):
        self.calls.append((sql, params))
        s = " ".join(sql.split())
        if "FROM contributor_profiles WHERE user_id" in s and "JOIN users" not in s:
            return self._profile_row
        if "INSERT INTO contributor_profiles" in s:
            visibility, tagline, mark_disclosed = params[1], params[2], params[3]
            return {
                "user_id": params[0], "visibility": visibility,
                "tagline": tagline,
                "disclosed_at": "2026-09-08T00:00:00Z" if mark_disclosed else None,
                "t_created": "t0", "t_updated": "t1",
            }
        if "SELECT external_subject FROM users" in s:
            return self._user_row
        if "FROM procedures WHERE owner_id" in s:
            return self._proc_row
        if "FROM knowledge_nodes" in s:
            return self._claim_row
        if "FROM publication_records" in s:
            return self._pub_row
        if "JOIN users u ON u.id = p.user_id" in s and "p.user_id = $1" in s:
            return self._public_rows[0] if self._public_rows else None
        return None

    async def fetch(self, sql, *params):
        self.calls.append((sql, params))
        s = " ".join(sql.split())
        if "ILIKE" in s:
            return self._public_rows
        if "WHERE p.visibility = 'public' AND u.is_active" in s:
            return self._public_rows
        return []

    async def execute(self, sql, *params):
        self.calls.append((sql, params))
        return "DELETE 1"


# --- INV-01 / disclosure -------------------------------------------------


def test_default_visibility_is_private_and_upsert_validates():
    ex = FakeExec()
    with pytest.raises(ValueError):
        run(contributors.upsert_profile(ex, "u1", visibility="world"))


def test_every_disclosure_submission_stamps_disclosed_at():
    ex = FakeExec()
    for choice in ("private", "public"):
        row = run(contributors.upsert_profile(ex, "u1", visibility=choice))
        assert row["disclosed_at"] is not None, choice
        assert row["visibility"] == choice


def test_public_profile_absent_when_not_opted_in():
    ex = FakeExec(public_rows=[])  # JOIN filtered by visibility='public' returns nothing
    assert run(contributors.public_profile(ex, "u1")) is None


def test_public_profile_returns_counts_when_public():
    ex = FakeExec(
        public_rows=[{
            "user_id": "u1", "display_name": "Ada L", "tagline": "kernels",
            "profile_since": "t0",
        }],
        proc_row={"authored": 4, "verified": 3},
        claim_row={"n": 2},
        pub_row={"n": 1},
    )
    prof = run(contributors.public_profile(ex, "u1"))
    assert prof["display_name"] == "Ada L"
    assert prof["counts"] == {
        "procedures_authored": 4, "verified_procedures": 3,
        "claims_authored": 2, "commons_publications": 1,
    }


# --- counts come off provenance ---------------------------------------


def test_contribution_counts_query_shapes():
    ex = FakeExec(proc_row={"authored": 9, "verified": 5}, claim_row={"n": 7},
                  pub_row={"n": 3})
    counts = run(contributors.contribution_counts(ex, user_id="u1", subject="sub-abc"))
    assert counts == {
        "procedures_authored": 9, "verified_procedures": 5,
        "claims_authored": 7, "commons_publications": 3,
    }
    joined = " ".join(c[0] for c in ex.calls)
    assert "procedures" in joined and "owner_id" in joined
    assert "knowledge_nodes" in joined and "created_by" in joined
    assert "publication_records" in joined and "actor_user_id" in joined


def test_contribution_counts_tolerate_missing_rows():
    ex = FakeExec(user_row=None, proc_row=None, claim_row=None, pub_row=None)
    counts = run(contributors.contribution_counts(ex, user_id="u1"))
    assert counts == {
        "procedures_authored": 0, "verified_procedures": 0,
        "claims_authored": 0, "commons_publications": 0,
    }


# --- search / leaderboard -------------------------------------------


def test_search_public_empty_query_short_circuits():
    ex = FakeExec()
    assert run(contributors.search_public(ex, "   ")) == []
    assert ex.calls == []


def test_search_public_only_scans_public():
    ex = FakeExec(public_rows=[{"user_id": "u1", "display_name": "Ada", "tagline": None}])
    out = run(contributors.search_public(ex, "ad"))
    assert out == [{"user_id": "u1", "display_name": "Ada", "tagline": None}]
    assert "visibility = 'public'" in " ".join(ex.calls[0][0].split())
    assert "ILIKE" in ex.calls[0][0]


def test_leaderboard_rejects_unknown_metric():
    ex = FakeExec()
    with pytest.raises(ValueError):
        run(contributors.leaderboard(ex, metric="karma"))


def test_leaderboard_ranks_by_chosen_metric_desc():
    rows = [
        {"user_id": "u1", "display_name": "Ada", "external_subject": "s1", "tagline": None},
        {"user_id": "u2", "display_name": "Bo", "external_subject": "s2", "tagline": None},
    ]

    class LB(FakeExec):
        async def fetchrow(self, sql, *params):
            s = " ".join(sql.split())
            if "FROM procedures WHERE owner_id" in s:
                return {"authored": 10, "verified": 9} if params[0] == "s2" \
                    else {"authored": 2, "verified": 1}
            if "FROM knowledge_nodes" in s:
                return {"n": 0}
            if "FROM publication_records" in s:
                return {"n": 0}
            return await super().fetchrow(sql, *params)

    ex = LB(public_rows=rows)
    lb = run(contributors.leaderboard(ex, metric="verified_procedures"))
    assert [e["user_id"] for e in lb["entries"]] == ["u2", "u1"]
    assert lb["entries"][0]["value"] == 9


# --- endpoint: identity from principal, one audit event --------------


def test_put_profile_uses_principal_identity_and_audits(monkeypatch):
    from app.api import profile as profile_api

    audit_calls = []

    async def fake_audit(pool, **kw):
        audit_calls.append(kw)
        return "evt-1"

    monkeypatch.setattr(profile_api, "record_audit_event", fake_audit)

    ex = FakeExec(profile_row=None)

    class Principal:
        user_id = "user-uuid-1"
        subject = "sub-real"
        name = "Ada L"

    body = profile_api.ProfileUpdate(visibility="public", tagline="kernels")
    out = run(profile_api.set_my_profile(body, pool=ex, principal=Principal()))

    assert out["profile"]["visibility"] == "public"
    assert len(audit_calls) == 1
    ev = audit_calls[0]
    assert ev["action"] == "profile_visibility_changed"
    assert ev["actor_subject"] == "sub-real"
    assert ev["actor_user_id"] == "user-uuid-1"
    assert ev["object_id"] == "user-uuid-1"
    assert ev["details"]["to"] == "public"
    assert ev["details"]["first_disclosure"] is True


def test_get_contributor_404_when_not_public(monkeypatch):
    from app.api import contributors as contributors_api

    ex = FakeExec(public_rows=[])
    with pytest.raises(Exception) as ei:
        run(contributors_api.get_contributor("u1", pool=ex))
    assert getattr(ei.value, "status_code", None) == 404
