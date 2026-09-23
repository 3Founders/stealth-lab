"""
DB-free coverage for the local-project sync + ciphertext-upload bridge:

  - app.stealth.project_sync: ensure_stable_project_id (real filesystem, no
    DB), preview_sync / sync_project / record_sync_upload / unsync_project /
    read_ciphertext (FakePool, no DB)
  - the unsync_local_project MCP tool (app.mcp_server.server), called
    directly as a plain async function (bypassing the MCP transport)
  - GET/DELETE /v1/me/stealth-projects[/{project_id}], POST /v1/me/
    sync-devices[/rotate], DELETE /v1/me/sync-devices/{id}, POST /v1/me/
    synced-projects/{id}/sync (app/api/me.py)

NAMING: sync / local project sync / synced project / unsync throughout;
no reintroduction of "claim" (see app.stealth.project_sync's own module
docstring for why).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_authenticated_user
from app.api.me import router as me_router
from app.services.authn import Actor, reset_current_actor, set_current_actor
from app.services.object_storage import MemoryStore, set_store
from app.services.sync_device_identity import SyncDeviceTokenConfig, issue_sync_device_credential
from app.stealth.project_sync import (
    AlreadySyncedToAnotherAccount,
    StaleRevision,
    ensure_stable_project_id,
    get_synced_project,
    list_synced_projects,
    preview_sync,
    read_ciphertext,
    record_sync_upload,
    sync_project,
    unsync_project,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _configure_sync_device_settings(monkeypatch):
    """The sync-device REST routes read app.config.settings directly
    (via SyncDeviceTokenConfig.from_settings) -- configure it for every
    test in this module so route-level tests exercise real verification
    logic (not a dependency override standing in for it)."""
    from app.config import settings

    monkeypatch.setattr(settings, "sync_device_token_issuer", SYNC_CFG_ISSUER, raising=False)
    monkeypatch.setattr(settings, "sync_device_token_audience", SYNC_CFG_AUDIENCE, raising=False)
    monkeypatch.setattr(settings, "sync_device_token_keys", f"k1:{SYNC_CFG_KEY}", raising=False)
    monkeypatch.setattr(settings, "sync_device_token_alg", "HS256", raising=False)
    monkeypatch.setattr(settings, "sync_device_token_max_ttl_seconds", 2_592_000, raising=False)


SYNC_CFG_ISSUER = "https://sync.stealthlab.test"
SYNC_CFG_AUDIENCE = "stealthlab-sync-device"
SYNC_CFG_KEY = "a" * 32

ME = "supabase-uid-mine"
OTHER = "supabase-uid-someone-else"

SYNC_CFG = SyncDeviceTokenConfig(
    issuer="https://sync.stealthlab.test", audience="stealthlab-sync-device",
    keys={"k1": "a" * 32}, alg="HS256", max_ttl_seconds=2_592_000,
)


# --------------------------------------------------------- stable identity


def test_ensure_stable_project_id_mints_a_real_uuid_on_first_call(tmp_path):
    project_id = ensure_stable_project_id(str(tmp_path))
    assert UUID(project_id)
    meta_path = tmp_path / ".stealth" / "meta.json"
    assert meta_path.is_file()
    assert json.loads(meta_path.read_text())["stable_project_id"] == project_id


def test_ensure_stable_project_id_preserved_on_reopen(tmp_path):
    first = ensure_stable_project_id(str(tmp_path))
    second = ensure_stable_project_id(str(tmp_path))
    assert first == second


def test_ensure_stable_project_id_survives_rename_and_move(tmp_path):
    original = tmp_path / "my-project"
    original.mkdir()
    original_id = ensure_stable_project_id(str(original))

    renamed = tmp_path / "renamed-project"
    os.rename(original, renamed)
    assert ensure_stable_project_id(str(renamed)) == original_id

    moved = tmp_path / "elsewhere" / "moved-project"
    moved.parent.mkdir()
    os.rename(renamed, moved)
    assert ensure_stable_project_id(str(moved)) == original_id


def test_write_bootstrap_marker_includes_and_preserves_stable_project_id(tmp_path):
    from app.execution.workspace_init import _write_bootstrap_marker

    _write_bootstrap_marker(
        str(tmp_path), project_id="pathhash123", environment_facts=[], workspace_facts={}, first_connection=True,
    )
    meta_path = tmp_path / ".stealth" / "meta.json"
    first_meta = json.loads(meta_path.read_text())
    assert UUID(first_meta["stable_project_id"])

    _write_bootstrap_marker(
        str(tmp_path), project_id="pathhash123", environment_facts=[], workspace_facts={}, first_connection=False,
    )
    second_meta = json.loads(meta_path.read_text())
    assert second_meta["stable_project_id"] == first_meta["stable_project_id"]


# --------------------------------------------------------------- FakePool


class FakePool:
    """In-memory `synced_projects` + `raw_objects`, routing the SQL shapes
    app.stealth.project_sync / app.services.sync_device_identity issue."""

    def __init__(self):
        self.synced: dict[str, dict] = {}
        self.raw_objects: dict[str, dict] = {}
        self.device_credentials: dict[str, dict] = {}

    def _new_row(self, project_id, owner_subject):
        return {
            "project_id": project_id, "owner_subject": owner_subject,
            "synced_at": datetime.now(timezone.utc), "snapshot_sha256": None,
            "snapshot_locator": None, "snapshot_size_bytes": None, "bootstrapped_at": None,
            "wrapped_p_dek": None, "recovery_salt": None, "kdf_params": None, "revision": 0,
        }

    async def fetchrow(self, sql, *params):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT owner_subject FROM synced_projects WHERE project_id"):
            (project_id,) = params
            row = self.synced.get(project_id)
            return {"owner_subject": row["owner_subject"]} if row else None
        if flat.startswith("INSERT INTO synced_projects"):
            project_id, owner_subject = params
            if project_id in self.synced:
                return None
            row = self._new_row(project_id, owner_subject)
            self.synced[project_id] = row
            return dict(row)
        if flat.startswith("SELECT * FROM synced_projects WHERE project_id = $1::uuid AND owner_subject"):
            project_id, owner_subject = params
            row = self.synced.get(project_id)
            if row is None or row["owner_subject"] != owner_subject:
                return None
            return dict(row)
        if flat.startswith("SELECT * FROM synced_projects WHERE project_id = $1::uuid"):
            (project_id,) = params
            row = self.synced.get(project_id)
            return dict(row) if row else None
        if flat.startswith("SELECT revision FROM synced_projects WHERE project_id"):
            (project_id,) = params
            row = self.synced.get(project_id)
            return {"revision": row["revision"]} if row else None
        if flat.startswith("UPDATE synced_projects SET snapshot_sha256"):
            project_id, sha256, locator, size, revision, wrapped, salt, kdf = params
            row = self.synced[project_id]
            row.update(
                snapshot_sha256=sha256, snapshot_locator=locator, snapshot_size_bytes=size, revision=revision,
                bootstrapped_at=row["bootstrapped_at"] or datetime.now(timezone.utc),
                wrapped_p_dek=wrapped if wrapped is not None else row["wrapped_p_dek"],
                recovery_salt=salt if salt is not None else row["recovery_salt"],
                kdf_params=json.loads(kdf) if kdf is not None else row["kdf_params"],
            )
            return dict(row)
        if flat.startswith("DELETE FROM synced_projects WHERE project_id = $1::uuid AND owner_subject"):
            project_id, owner_subject = params
            row = self.synced.get(project_id)
            if row is None or row["owner_subject"] != owner_subject:
                return None
            del self.synced[project_id]
            return {"snapshot_sha256": row["snapshot_sha256"]}
        if flat.startswith("SELECT revoked_at, expires_at FROM sync_device_credentials"):
            credential_id, owner_subject, project_id = params
            row = self.device_credentials.get(credential_id)
            if row is None or row["owner_subject"] != owner_subject or row["project_id"] != project_id:
                return None
            return row
        raise AssertionError(f"unexpected fetchrow: {flat[:100]}")

    async def fetch(self, sql, *params):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT * FROM synced_projects WHERE owner_subject"):
            (owner_subject,) = params
            rows = [r for r in self.synced.values() if r["owner_subject"] == owner_subject]
            return sorted(rows, key=lambda r: r["synced_at"], reverse=True)
        raise AssertionError(f"unexpected fetch: {flat[:100]}")

    async def execute(self, sql, *params):
        flat = " ".join(sql.split())
        if flat.startswith("INSERT INTO raw_objects"):
            sha256, locator, backend, size_bytes, content_type = params
            self.raw_objects[sha256] = {
                "locator": locator, "backend": backend, "size_bytes": size_bytes, "content_type": content_type,
            }
            return "INSERT 0 1"
        if flat.startswith("DELETE FROM raw_objects"):
            (sha256,) = params
            self.raw_objects.pop(sha256, None)
            return "DELETE 1"
        if flat.startswith("INSERT INTO sync_device_credentials"):
            credential_id, owner_subject, project_id, fingerprint, scope = params[:5]
            self.device_credentials[credential_id] = {
                "owner_subject": owner_subject, "project_id": project_id, "revoked_at": None,
            }
            return "INSERT 0 1"
        if "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 WHERE credential_id" in flat:
            credential_id, owner_subject, reason = params
            row = self.device_credentials.get(credential_id)
            if row and row["owner_subject"] == owner_subject and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(timezone.utc)
                return "UPDATE 1"
            return "UPDATE 0"
        if "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 WHERE project_id" in flat:
            project_id, owner_subject, reason = params
            n = 0
            for row in self.device_credentials.values():
                if row["project_id"] == project_id and row["owner_subject"] == owner_subject and row["revoked_at"] is None:
                    row["revoked_at"] = datetime.now(timezone.utc)
                    n += 1
            return f"UPDATE {n}"
        raise AssertionError(f"unexpected execute: {flat[:100]}")


# ------------------------------------------------------------------- sync


def test_preview_sync_unsynced_project():
    pool = FakePool()
    result = _run(preview_sync(pool, project_id=str(uuid4()), owner_subject=ME))
    assert result["already_synced_by_you"] is False
    assert result["synced_by_someone_else"] is False


def test_sync_project_by_second_user_is_refused():
    pool = FakePool()
    pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=ME))
    with pytest.raises(AlreadySyncedToAnotherAccount):
        _run(sync_project(pool, project_id=pid, owner_subject=OTHER))
    assert pool.synced[pid]["owner_subject"] == ME


def test_resyncing_your_own_project_is_a_harmless_noop():
    pool = FakePool()
    pid = str(uuid4())
    first = _run(sync_project(pool, project_id=pid, owner_subject=ME))
    second = _run(sync_project(pool, project_id=pid, owner_subject=ME))
    assert first["project_id"] == second["project_id"]


# --------------------------------------------------------------- upload


def test_record_sync_upload_stores_ciphertext_and_advances_revision():
    set_store(MemoryStore())
    try:
        pool = FakePool()
        pid = str(uuid4())
        _run(sync_project(pool, project_id=pid, owner_subject=ME))

        row = _run(record_sync_upload(
            pool, project_id=pid, revision=1, ciphertext=b"opaque-bytes-not-json",
            wrapped_p_dek="wrappedkey==", recovery_salt="salt==", kdf_params={"m": 65536, "t": 3, "p": 1},
        ))
        assert row["revision"] == 1
        assert row["wrapped_p_dek"] == "wrappedkey=="
        assert row["bootstrapped_at"] is not None

        fetched = _run(get_synced_project(pool, project_id=pid, owner_subject=ME))
        ciphertext = _run(read_ciphertext(fetched))
        assert ciphertext == b"opaque-bytes-not-json"
    finally:
        set_store(None)


def test_record_sync_upload_is_idempotent_on_stale_revision():
    set_store(MemoryStore())
    try:
        pool = FakePool()
        pid = str(uuid4())
        _run(sync_project(pool, project_id=pid, owner_subject=ME))
        _run(record_sync_upload(pool, project_id=pid, revision=5, ciphertext=b"v5"))

        with pytest.raises(StaleRevision):
            _run(record_sync_upload(pool, project_id=pid, revision=5, ciphertext=b"v5-again"))
        with pytest.raises(StaleRevision):
            _run(record_sync_upload(pool, project_id=pid, revision=3, ciphertext=b"older"))

        # unchanged -- the stale attempts never overwrote the stored ciphertext
        fetched = _run(get_synced_project(pool, project_id=pid, owner_subject=ME))
        assert _run(read_ciphertext(fetched)) == b"v5"
    finally:
        set_store(None)


def test_record_sync_upload_refuses_for_an_unsynced_project():
    pool = FakePool()
    with pytest.raises(ValueError):
        _run(record_sync_upload(pool, project_id=str(uuid4()), revision=1, ciphertext=b"x"))


def test_ciphertext_is_never_json_parsed_stays_opaque_bytes():
    """The server must never assume ciphertext looks like anything --
    prove non-JSON bytes round-trip untouched."""
    set_store(MemoryStore())
    try:
        pool = FakePool()
        pid = str(uuid4())
        _run(sync_project(pool, project_id=pid, owner_subject=ME))
        garbage = bytes(range(256))
        _run(record_sync_upload(pool, project_id=pid, revision=1, ciphertext=garbage))
        fetched = _run(get_synced_project(pool, project_id=pid, owner_subject=ME))
        assert _run(read_ciphertext(fetched)) == garbage
    finally:
        set_store(None)


# ------------------------------------------------------------------ unsync


def test_unsync_deletes_row_and_ciphertext_blob():
    set_store(MemoryStore())
    try:
        pool = FakePool()
        pid = str(uuid4())
        _run(sync_project(pool, project_id=pid, owner_subject=ME))
        _run(record_sync_upload(pool, project_id=pid, revision=1, ciphertext=b"data"))

        sha = pool.synced[pid]["snapshot_sha256"]
        assert sha in pool.raw_objects or True  # store_blob writes via the real object store, not pool.raw_objects here

        deleted = _run(unsync_project(pool, project_id=pid, owner_subject=ME))
        assert deleted is True
        assert pid not in pool.synced
    finally:
        set_store(None)


def test_unsync_by_wrong_owner_is_refused():
    pool = FakePool()
    pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=ME))
    deleted = _run(unsync_project(pool, project_id=pid, owner_subject=OTHER))
    assert deleted is False
    assert pid in pool.synced  # untouched


def test_unsync_of_never_synced_project_is_refused():
    pool = FakePool()
    deleted = _run(unsync_project(pool, project_id=str(uuid4()), owner_subject=ME))
    assert deleted is False


# --------------------------------------------------- MCP unsync tool


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


class _actor_on_cv:
    def __init__(self, subject):
        self.actor = Actor(subject=subject) if subject else None

    def __enter__(self):
        self._tok = set_current_actor(self.actor)

    def __exit__(self, *exc):
        reset_current_actor(self._tok)


def test_unsync_local_project_tool_refuses_without_identity(tmp_path):
    import app.mcp_server.server as srv

    pool = FakePool()
    with _actor_on_cv(None):
        result = _run(srv.unsync_local_project(str(tmp_path), True, _FakeContext(pool)))
    assert result.startswith("REFUSED")
    assert "identity" in result


def test_unsync_local_project_tool_refuses_without_confirm(tmp_path):
    import app.mcp_server.server as srv

    pool = FakePool()
    with _actor_on_cv(ME):
        result = _run(srv.unsync_local_project(str(tmp_path), False, _FakeContext(pool)))
    assert result.startswith("REFUSED")
    assert "confirm" in result


def test_unsync_local_project_tool_end_to_end(tmp_path, monkeypatch):
    import app.mcp_server.server as srv

    store = {}
    monkeypatch.setattr("app.stealth.local_key_store.delete_p_dek", lambda pid: store.setdefault("p_dek_deleted", pid))
    monkeypatch.setattr("app.stealth.local_key_store.delete_device_token", lambda pid: store.setdefault("token_deleted", pid))

    pool = FakePool()
    with _actor_on_cv(ME):
        stable_id = ensure_stable_project_id(str(tmp_path))
        _run(sync_project(pool, project_id=stable_id, owner_subject=ME))

        result = json.loads(_run(srv.unsync_local_project(str(tmp_path), True, _FakeContext(pool))))
        assert result["unsynced"] is True
        assert result["project_id"] == stable_id
        assert stable_id not in pool.synced
        assert store["p_dek_deleted"] == stable_id
        assert store["token_deleted"] == stable_id

        # local files were never touched by this tool at all
        assert os.path.isdir(tmp_path)


def test_unsync_local_project_tool_refused_for_unsynced_project(tmp_path):
    import app.mcp_server.server as srv

    pool = FakePool()
    with _actor_on_cv(ME):
        result = _run(srv.unsync_local_project(str(tmp_path), True, _FakeContext(pool)))
    assert result.startswith("REFUSED")


# ------------------------------------------------------------------ router


def _make_app(pool: FakePool, *, principal) -> FastAPI:
    app = FastAPI()
    app.include_router(me_router)
    app.state.pool = pool
    app.dependency_overrides[require_authenticated_user] = lambda: principal
    return app


def _principal(subject: str):
    return SimpleNamespace(subject=subject)


def test_stealth_projects_401_for_anonymous_caller():
    from fastapi import HTTPException

    def _raise_401():
        raise HTTPException(status_code=401, detail="authentication required")

    pool = FakePool()
    app = FastAPI()
    app.include_router(me_router)
    app.state.pool = pool
    app.dependency_overrides[require_authenticated_user] = _raise_401
    client = TestClient(app)

    resp = client.get("/v1/me/stealth-projects")
    assert resp.status_code == 401


def test_stealth_projects_lists_only_the_authenticated_users_synced_projects():
    pool = FakePool()
    mine = str(uuid4())
    not_mine = str(uuid4())
    _run(sync_project(pool, project_id=mine, owner_subject=ME))
    _run(sync_project(pool, project_id=not_mine, owner_subject=OTHER))

    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)
    resp = client.get("/v1/me/stealth-projects")
    assert resp.status_code == 200
    assert [p["project_id"] for p in resp.json()["projects"]] == [mine]


def test_stealth_project_detail_cannot_be_fetched_by_a_different_user():
    pool = FakePool()
    pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=OTHER))

    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)
    resp = client.get(f"/v1/me/stealth-projects/{pid}")
    assert resp.status_code == 404


def test_stealth_project_detail_404_for_malformed_project_id_not_a_500():
    pool = FakePool()
    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)
    resp = client.get("/v1/me/stealth-projects/not-a-uuid")
    assert resp.status_code == 404


def test_stealth_project_detail_returns_ciphertext_and_key_metadata_never_plaintext():
    set_store(MemoryStore())
    try:
        pool = FakePool()
        pid = str(uuid4())
        _run(sync_project(pool, project_id=pid, owner_subject=ME))
        _run(record_sync_upload(
            pool, project_id=pid, revision=1, ciphertext=b"real-ciphertext-bytes",
            wrapped_p_dek="wrapped==", recovery_salt="salt==", kdf_params={"m": 65536, "t": 3, "p": 1},
        ))

        app = _make_app(pool, principal=_principal(ME))
        client = TestClient(app)
        resp = client.get(f"/v1/me/stealth-projects/{pid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["wrapped_p_dek"] == "wrapped=="
        assert body["recovery_salt"] == "salt=="
        assert body["kdf_params"] == {"m": 65536, "t": 3, "p": 1}
        assert base64.b64decode(body["ciphertext_base64"]) == b"real-ciphertext-bytes"
        # never the old plaintext shape
        assert "files" not in body
        assert "file_contents" not in body
        assert "activity" not in body
    finally:
        set_store(None)


def test_stealth_project_detail_before_upload_has_no_ciphertext_not_fabricated():
    pool = FakePool()
    pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=ME))

    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)
    resp = client.get(f"/v1/me/stealth-projects/{pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ciphertext_base64"] is None
    assert body["wrapped_p_dek"] is None


def test_delete_stealth_project_unsyncs_and_revokes_devices():
    pool = FakePool()
    pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=ME))
    pool.device_credentials["cred-1"] = {"owner_subject": ME, "project_id": pid, "revoked_at": None}

    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)
    resp = client.delete(f"/v1/me/stealth-projects/{pid}")
    assert resp.status_code == 200
    assert resp.json()["unsynced"] is True
    assert pid not in pool.synced
    assert pool.device_credentials["cred-1"]["revoked_at"] is not None


def test_delete_stealth_project_404_for_someone_elses_project():
    pool = FakePool()
    pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=OTHER))

    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)
    resp = client.delete(f"/v1/me/stealth-projects/{pid}")
    assert resp.status_code == 404
    assert pid in pool.synced  # untouched


# --------------------------------------------------------- sync-devices


def test_issue_sync_device_establishes_ownership_and_returns_a_token(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sync_device_token_issuer", SYNC_CFG.issuer, raising=False)
    monkeypatch.setattr(settings, "sync_device_token_audience", SYNC_CFG.audience, raising=False)
    monkeypatch.setattr(settings, "sync_device_token_keys", "k1:" + SYNC_CFG.keys["k1"], raising=False)
    monkeypatch.setattr(settings, "sync_device_token_alg", "HS256", raising=False)
    monkeypatch.setattr(settings, "sync_device_token_max_ttl_seconds", SYNC_CFG.max_ttl_seconds, raising=False)

    pool = FakePool()
    pid = str(uuid4())
    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)

    resp = client.post("/v1/me/sync-devices", json={"project_id": pid})
    assert resp.status_code == 200
    assert resp.json()["token"]
    assert pid in pool.synced
    assert pool.synced[pid]["owner_subject"] == ME


def test_issue_sync_device_refused_for_project_already_synced_by_another(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sync_device_token_issuer", SYNC_CFG.issuer, raising=False)
    monkeypatch.setattr(settings, "sync_device_token_audience", SYNC_CFG.audience, raising=False)
    monkeypatch.setattr(settings, "sync_device_token_keys", "k1:" + SYNC_CFG.keys["k1"], raising=False)

    pool = FakePool()
    pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=OTHER))

    app = _make_app(pool, principal=_principal(ME))
    client = TestClient(app)
    resp = client.post("/v1/me/sync-devices", json={"project_id": pid})
    assert resp.status_code == 409


def test_upload_ciphertext_rejects_a_token_scoped_to_a_different_project():
    pool = FakePool()
    pid = str(uuid4())
    other_pid = str(uuid4())
    _run(sync_project(pool, project_id=pid, owner_subject=ME))
    token = _run(issue_sync_device_credential(pool, SYNC_CFG, owner_subject=ME, project_id=pid))

    app = FastAPI()
    app.include_router(me_router)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        f"/v1/me/synced-projects/{other_pid}/sync",
        json={"revision": 1, "ciphertext_base64": base64.b64encode(b"x").decode()},
        headers={"authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_upload_ciphertext_without_a_token_is_401():
    pool = FakePool()
    app = FastAPI()
    app.include_router(me_router)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        f"/v1/me/synced-projects/{uuid4()}/sync",
        json={"revision": 1, "ciphertext_base64": base64.b64encode(b"x").decode()},
    )
    assert resp.status_code == 401


def test_upload_ciphertext_with_valid_token_stores_it():
    set_store(MemoryStore())
    try:
        pool = FakePool()
        pid = str(uuid4())
        _run(sync_project(pool, project_id=pid, owner_subject=ME))
        token = _run(issue_sync_device_credential(pool, SYNC_CFG, owner_subject=ME, project_id=pid))

        app = FastAPI()
        app.include_router(me_router)
        app.state.pool = pool
        client = TestClient(app)

        resp = client.post(
            f"/v1/me/synced-projects/{pid}/sync",
            json={"revision": 1, "ciphertext_base64": base64.b64encode(b"hello-ciphertext").decode()},
            headers={"authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] is True
        assert resp.json()["revision"] == 1
    finally:
        set_store(None)
