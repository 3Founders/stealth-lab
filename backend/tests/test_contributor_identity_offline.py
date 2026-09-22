"""
DB-free proving tests for the V1 contributor identity extension
(migration 106 + app/services/contributors.py's username/avatar/onboarding
functions + app/services/username_generator.py + the profile/contributors
API layers).

Deliberately a separate FakeExec from test_contributor_profiles_offline.py
(repo convention: fakes are not shared across test files) tuned for the
INSERT ... ON CONFLICT / UPDATE ... RETURNING shapes the new functions use.
"""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.services import contributors
from app.services.username_generator import InvalidUsername


def run(coro):
    return asyncio.run(coro)


class FakeProfileExec:
    """A tiny in-memory contributor_profiles + username_history, enough to
    exercise the real SQL text shapes (INSERT...ON CONFLICT...RETURNING,
    UPDATE...RETURNING) without a database. `fail_next_claim` simulates a
    concurrent collision on the NEXT username-claiming statement only."""

    def __init__(self):
        self.profiles: dict[str, dict] = {}   # user_id -> row
        self.history: set[str] = set()        # lowercased old usernames
        self.fail_next_claim = False
        self.calls: list[str] = []

    def _row(self, user_id: str) -> dict:
        p = self.profiles[user_id]
        return {
            "user_id": user_id, "visibility": p.get("visibility", "private"),
            "disclosed_at": p.get("disclosed_at"), "tagline": p.get("tagline"),
            "username": p.get("username"), "avatar_locator": p.get("avatar_locator"),
            "onboarding_complete": p.get("onboarding_complete", False),
            "t_created": "t0", "t_updated": "t1",
        }

    async def fetchrow(self, sql, *params):
        s = " ".join(sql.split())
        self.calls.append(s)

        if "FROM contributor_profiles WHERE user_id" in s and "INSERT" not in s and "UPDATE" not in s:
            uid = params[0]
            return self._row(uid) if uid in self.profiles else None

        if "INSERT INTO contributor_profiles (user_id, username)" in s:
            uid, username = params[0], params[1]
            only_if_unset = "WHERE contributor_profiles.username IS NULL" in s
            existing = self.profiles.get(uid)
            if only_if_unset and existing and existing.get("username"):
                return None  # ON CONFLICT ... WHERE guard blocked the write
            if self.fail_next_claim:
                self.fail_next_claim = False
                raise asyncpg.exceptions.UniqueViolationError("duplicate username")
            if username.lower() in self.history:
                raise asyncpg.exceptions.UniqueViolationError("username is reserved (previously used)")
            for other_uid, row in self.profiles.items():
                if other_uid != uid and (row.get("username") or "").lower() == username.lower():
                    raise asyncpg.exceptions.UniqueViolationError("duplicate username")
            self.profiles.setdefault(uid, {})["username"] = username
            return self._row(uid)

        if "UPDATE contributor_profiles SET avatar_locator" in s:
            uid, locator = params[0], params[1]
            if uid not in self.profiles:
                return None
            self.profiles[uid]["avatar_locator"] = locator
            return self._row(uid)

        if "UPDATE contributor_profiles SET onboarding_complete" in s:
            uid = params[0]
            if uid not in self.profiles:
                return None
            self.profiles[uid]["onboarding_complete"] = True
            return self._row(uid)

        if "SELECT user_id FROM username_history WHERE lower(old_username)" in s:
            name = params[0].lower()
            for uid, row in self.profiles.items():
                if name in row.get("_history", set()):
                    return {"user_id": uid}
            return None

        if "FROM contributor_profiles WHERE lower(username) = lower($1)" in s:
            name = params[0].lower()
            for uid, row in self.profiles.items():
                if (row.get("username") or "").lower() == name:
                    return self._row(uid)
            return None

        if "SELECT 1 FROM contributor_profiles WHERE lower(username)" in s:
            name = params[0].lower()
            for row in self.profiles.values():
                if (row.get("username") or "").lower() == name:
                    return {"?column?": 1}
            if name in self.history:
                return {"?column?": 1}
            return None

        return None

    async def execute(self, sql, *params):
        s = " ".join(sql.split())
        self.calls.append(s)
        if "INSERT INTO username_history" in s:
            uid, old = params[0], params[1]
            self.history.add(old.lower())
            self.profiles.setdefault(uid, {}).setdefault("_history", set()).add(old.lower())
        return "OK"

    async def fetch(self, sql, *params):
        return []


def _seed(ex: FakeProfileExec, user_id: str, **fields) -> None:
    ex.profiles[user_id] = dict(fields)


# --- generation / ensure_profile -----------------------------------------


def test_ensure_profile_generates_a_username_for_a_new_user():
    ex = FakeProfileExec()
    profile = run(contributors.ensure_profile(ex, "u1"))
    assert profile["username"]
    assert profile["onboarding_complete"] is False


def test_ensure_profile_is_idempotent_for_a_returning_user():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox", onboarding_complete=True)
    profile = run(contributors.ensure_profile(ex, "u1"))
    assert profile["username"] == "CopperFox"
    assert profile["onboarding_complete"] is True
    # No username-claiming INSERT was issued -- the existing name was kept.
    assert not any("INSERT INTO contributor_profiles" in c for c in ex.calls)


def test_ensure_profile_retries_past_a_collision():
    ex = FakeProfileExec()
    ex.fail_next_claim = True  # first candidate collides
    profile = run(contributors.ensure_profile(ex, "u1"))
    assert profile["username"]  # a later candidate succeeded


# --- suggestions (no reservation) -----------------------------------------


def test_suggest_username_does_not_reserve_anything():
    ex = FakeProfileExec()
    names = run(contributors.suggest_username(ex, limit=2))
    assert len(names) == 2
    assert ex.profiles == {}  # nothing claimed


def test_suggest_username_excludes_taken_names(monkeypatch):
    from app.services import username_generator as ug

    monkeypatch.setattr(ug, "generate_candidates", lambda n=8: iter(["TakenName", "FreeName"]))
    ex = FakeProfileExec()
    _seed(ex, "someone-else", username="TakenName")
    names = run(contributors.suggest_username(ex, limit=5))
    assert "TakenName" not in names
    assert "FreeName" in names


# --- rename / history / redirect ------------------------------------------


def test_rename_username_succeeds_and_records_history():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    updated = run(contributors.rename_username(ex, "u1", "LogicCrane"))
    assert updated["username"] == "LogicCrane"
    assert "copperfox" in ex.history


def test_rename_preserves_user_id_ownership():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    updated = run(contributors.rename_username(ex, "u1", "LogicCrane"))
    assert updated["user_id"] == "u1"


def test_rename_to_an_already_taken_name_is_rejected():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    _seed(ex, "u2", username="LogicCrane")
    with pytest.raises(ValueError):
        run(contributors.rename_username(ex, "u1", "LogicCrane"))


def test_rename_to_a_reserved_old_name_is_rejected():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    run(contributors.rename_username(ex, "u1", "LogicCrane"))  # CopperFox -> history
    _seed(ex, "u2", username="Someone")
    with pytest.raises(ValueError):
        run(contributors.rename_username(ex, "u2", "CopperFox"))


def test_rename_rejects_invalid_manual_names():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    with pytest.raises(InvalidUsername):
        run(contributors.rename_username(ex, "u1", "ab"))
    with pytest.raises(InvalidUsername):
        run(contributors.rename_username(ex, "u1", "admin"))


def test_old_username_resolves_via_history_and_flags_renamed_to():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    run(contributors.rename_username(ex, "u1", "LogicCrane"))
    resolved = run(contributors.get_profile_by_username(ex, "CopperFox"))
    assert resolved is not None
    assert resolved["renamed_to"] == "LogicCrane"
    assert resolved["user_id"] == "u1"


def test_current_username_resolves_without_renamed_to():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    resolved = run(contributors.get_profile_by_username(ex, "CopperFox"))
    assert resolved["renamed_to"] is None


def test_unknown_username_resolves_to_none():
    ex = FakeProfileExec()
    assert run(contributors.get_profile_by_username(ex, "NoSuchName")) is None


# --- onboarding -------------------------------------------------------


def test_complete_onboarding_flips_the_flag():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox", onboarding_complete=False)
    updated = run(contributors.complete_onboarding(ex, "u1"))
    assert updated["onboarding_complete"] is True


# --- avatar -------------------------------------------------------------


def test_set_avatar_updates_locator():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox")
    updated = run(contributors.set_avatar(ex, "u1", "s3://bucket/ab/cd/hash"))
    assert updated["avatar_locator"] == "s3://bucket/ab/cd/hash"


def test_clear_avatar_via_set_avatar_none():
    ex = FakeProfileExec()
    _seed(ex, "u1", username="CopperFox", avatar_locator="s3://bucket/x")
    updated = run(contributors.set_avatar(ex, "u1", None))
    assert updated["avatar_locator"] is None


# --- no internal-identity leakage on the public API layer -----------------


def test_public_view_strips_internal_user_id():
    from app.api.contributors import _public_view

    row = {"user_id": "uuid-123", "username": "CopperFox", "tagline": "t"}
    out = _public_view(row)
    assert "user_id" not in out
    assert out["username"] == "CopperFox"


def test_get_contributor_by_username_route_never_leaks_email_or_uuid(monkeypatch):
    from app.api import contributors as contributors_api

    async def fake_resolve(pool, username):
        return {"user_id": "uuid-123", "renamed_to": None}

    async def fake_public(pool, user_id):
        return {
            "user_id": user_id, "display_name": "Ada L", "username": "AdaLovelace",
            "tagline": "kernels", "profile_since": "t0", "counts": {},
        }

    monkeypatch.setattr(contributors_api.contributors, "get_profile_by_username", fake_resolve)
    monkeypatch.setattr(contributors_api.contributors, "public_profile", fake_public)

    out = run(contributors_api.get_contributor_by_username("AdaLovelace", pool=object()))
    assert "user_id" not in out
    assert "email" not in out
    assert "issuer" not in out
    assert "external_subject" not in out
    assert out["username"] == "AdaLovelace"


# --- API layer: identity is always server-derived, never from the body ---


def test_upload_avatar_uses_principal_identity_not_request_body(monkeypatch):
    from app.api import profile as profile_api

    class FakePool:
        pass

    class FakeStore:
        backend = "memory"

        async def put(self, data, *, content_type="application/octet-stream"):
            return "deadbeef", "memory://ab/cd/deadbeef"

    class FakeUpload:
        async def read(self, n):
            import io

            from PIL import Image

            buf = io.BytesIO()
            Image.new("RGB", (64, 64), color=(1, 2, 3)).save(buf, format="PNG")
            return buf.getvalue()

    class Principal:
        user_id = "user-uuid-1"
        subject = "sub-real"
        name = "Ada L"
        rate_key = "viewer:sub-real"

    captured = {}

    async def fake_ensure_profile(pool, user_id):
        captured["ensure_for"] = user_id
        return {"user_id": user_id, "username": "CopperFox"}

    async def fake_set_avatar(pool, user_id, locator):
        captured["set_for"] = user_id
        captured["locator"] = locator
        return {"user_id": user_id, "username": "CopperFox", "avatar_locator": locator}

    async def fake_audit(pool, **kw):
        captured["audit"] = kw
        return "evt-1"

    class NoLimiter:
        def __init__(self, pool):
            pass

        async def check_and_record(self, key, endpoint):
            captured["rate_key"] = key

    monkeypatch.setattr(profile_api.contributors, "ensure_profile", fake_ensure_profile)
    monkeypatch.setattr(profile_api.contributors, "set_avatar", fake_set_avatar)
    monkeypatch.setattr(profile_api, "record_audit_event", fake_audit)
    monkeypatch.setattr(profile_api, "RateLimiter", NoLimiter)
    monkeypatch.setattr(profile_api.object_storage, "get_store", lambda: FakeStore())

    class FakeRequest:
        url = type("U", (), {"path": "/v1/me/avatar"})()

    out = run(profile_api.upload_avatar(FakeRequest(), file=FakeUpload(), pool=FakePool(), principal=Principal()))

    assert captured["set_for"] == "user-uuid-1"   # from the PRINCIPAL, never a body field
    assert captured["rate_key"] == "viewer:sub-real"
    assert out["profile"]["avatar_locator"] == "memory://ab/cd/deadbeef"
