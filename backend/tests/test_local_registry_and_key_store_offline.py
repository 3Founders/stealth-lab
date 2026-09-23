"""
DB-free coverage for:
  - app.stealth.local_registry: the small cross-project discovery registry
  - app.stealth.local_key_store: the OS-keychain wrapper (mocked keyring)
"""
from __future__ import annotations

from app.stealth import local_registry


def test_upsert_and_list_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "registry.json"))
    local_registry.upsert_local_project(str(tmp_path / "my-project"), stable_project_id="pid-1")

    rows = local_registry.list_local_projects()
    assert len(rows) == 1
    assert rows[0]["stable_project_id"] == "pid-1"
    assert rows[0]["display_hint"] == "my-project"
    assert "repo_path" in rows[0]
    assert "last_local_activity_at" in rows[0]


def test_registry_never_stores_project_content(tmp_path, monkeypatch):
    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "registry.json"))
    local_registry.upsert_local_project(str(tmp_path / "proj"), stable_project_id="pid-1")

    raw = (tmp_path / "registry.json").read_text()
    # exactly the three documented fields per entry, nothing else
    import json
    entry = json.loads(raw)["pid-1"]
    assert set(entry.keys()) == {"repo_path", "display_hint", "last_local_activity_at"}


def test_display_hint_is_basename_only_never_full_path(tmp_path, monkeypatch):
    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "registry.json"))
    nested = tmp_path / "a" / "b" / "c" / "my-real-project"
    local_registry.upsert_local_project(str(nested), stable_project_id="pid-1")

    rows = local_registry.list_local_projects()
    assert rows[0]["display_hint"] == "my-real-project"
    assert str(tmp_path) not in rows[0]["display_hint"]


def test_upsert_updates_existing_entry_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "registry.json"))
    local_registry.upsert_local_project(str(tmp_path / "proj"), stable_project_id="pid-1")
    first = local_registry.list_local_projects()[0]["last_local_activity_at"]

    local_registry.upsert_local_project(str(tmp_path / "proj"), stable_project_id="pid-1")
    rows = local_registry.list_local_projects()
    assert len(rows) == 1  # not duplicated
    assert rows[0]["last_local_activity_at"] >= first


def test_list_local_projects_sorted_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "registry.json"))
    import json

    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "old": {"repo_path": "/a", "display_hint": "a", "last_local_activity_at": "2020-01-01T00:00:00+00:00"},
        "new": {"repo_path": "/b", "display_hint": "b", "last_local_activity_at": "2026-01-01T00:00:00+00:00"},
    }))
    rows = local_registry.list_local_projects()
    assert [r["stable_project_id"] for r in rows] == ["new", "old"]


def test_missing_registry_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(tmp_path / "does-not-exist.json"))
    assert local_registry.list_local_projects() == []


def test_corrupt_registry_file_returns_empty_list_not_a_crash(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(local_registry, "_registry_path", lambda: str(path))
    assert local_registry.list_local_projects() == []


# ------------------------------------------------------------- key store


def test_key_store_round_trips_via_mocked_keyring(monkeypatch):
    from app.stealth import local_key_store

    store: dict[tuple[str, str], str] = {}

    class _FakeKeyring:
        def get_keyring(self):
            return object()

        def set_password(self, service, key, value):
            store[(service, key)] = value

        def get_password(self, service, key):
            return store.get((service, key))

        def delete_password(self, service, key):
            store.pop((service, key), None)

    fake = _FakeKeyring()
    monkeypatch.setattr("keyring.get_keyring", fake.get_keyring)
    monkeypatch.setattr("keyring.set_password", fake.set_password)
    monkeypatch.setattr("keyring.get_password", fake.get_password)
    monkeypatch.setattr("keyring.delete_password", fake.delete_password)

    local_key_store.store_p_dek("pid-1", "raw-key-base64")
    assert local_key_store.read_p_dek("pid-1") == "raw-key-base64"

    local_key_store.store_device_token("pid-1", "device-token")
    assert local_key_store.read_device_token("pid-1") == "device-token"

    local_key_store.delete_p_dek("pid-1")
    assert local_key_store.read_p_dek("pid-1") is None
    # deleting the P-DEK must never delete the device token entry too
    assert local_key_store.read_device_token("pid-1") == "device-token"


def test_key_store_fails_closed_when_no_keychain_backend(monkeypatch):
    """No plaintext fallback -- store_p_dek must raise, never write
    anywhere else, when the OS keychain is unavailable."""
    from app.stealth import local_key_store
    from keyring.errors import NoKeyringError

    def _raise(*a, **k):
        raise NoKeyringError("no backend available")

    monkeypatch.setattr("keyring.get_keyring", _raise)

    import pytest as _pytest
    with _pytest.raises(local_key_store.LocalKeyStoreUnavailable):
        local_key_store.store_p_dek("pid-1", "raw-key-base64")

    assert local_key_store.is_available() is False
    # read degrades to None rather than raising -- a caller checking "do I
    # have a cached key" should get a clean negative, not an exception
    assert local_key_store.read_p_dek("pid-1") is None
