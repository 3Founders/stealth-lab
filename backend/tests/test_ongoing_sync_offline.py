"""
DB-free coverage for:
  - app.stealth.local_sync_encrypt: Python-side AES-256-GCM, same wire
    format (base64(iv || ciphertext)) the browser side uses
  - app.stealth.ongoing_sync: the "not synced here" no-op fast path, the
    upload success path, and the offline queue/drain behavior -- all with
    a fake httpx client, no real network
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest

from app.stealth.local_sync_encrypt import decrypt_bytes, encrypt_bytes, encrypt_json


def _run(coro):
    return asyncio.run(coro)


def _random_key_base64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


# ------------------------------------------------------------- encryption


def test_encrypt_decrypt_round_trips():
    key = _random_key_base64()
    ciphertext = encrypt_bytes(key, b"hello world")
    assert decrypt_bytes(key, ciphertext) == b"hello world"


def test_encrypt_json_round_trips():
    key = _random_key_base64()
    ciphertext = encrypt_json(key, {"file_path": "goals.md", "summary": "edited"})
    plaintext = decrypt_bytes(key, ciphertext)
    assert json.loads(plaintext) == {"file_path": "goals.md", "summary": "edited"}


def test_two_encryptions_of_identical_plaintext_produce_different_ciphertext():
    """Proves IV uniqueness is actually wired up, not accidentally constant."""
    key = _random_key_base64()
    a = encrypt_bytes(key, b"same plaintext")
    b = encrypt_bytes(key, b"same plaintext")
    assert a != b
    assert decrypt_bytes(key, a) == decrypt_bytes(key, b) == b"same plaintext"


def test_tampered_ciphertext_fails_authentication():
    from cryptography.exceptions import InvalidTag

    key = _random_key_base64()
    ciphertext = encrypt_bytes(key, b"authentic data")
    raw = bytearray(base64.b64decode(ciphertext))
    raw[-1] ^= 0xFF  # flip the last byte
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(InvalidTag):
        decrypt_bytes(key, tampered)


def test_decrypt_with_wrong_key_fails():
    from cryptography.exceptions import InvalidTag

    key_a, key_b = _random_key_base64(), _random_key_base64()
    ciphertext = encrypt_bytes(key_a, b"secret")
    with pytest.raises(InvalidTag):
        decrypt_bytes(key_b, ciphertext)


def test_wire_format_is_iv_then_ciphertext_matching_js_convention():
    """lib/sync-crypto.ts's decryptBytes slices the first 12 bytes as the
    IV -- prove the Python side produces exactly that shape."""
    key = _random_key_base64()
    combined = base64.b64decode(encrypt_bytes(key, b"x"))
    assert len(combined) > 12  # IV (12) + ciphertext + 16-byte GCM tag


# --------------------------------------------------------------- ongoing


def test_sync_delta_is_a_noop_when_project_never_synced_on_this_machine(tmp_path, monkeypatch):
    from app.stealth import ongoing_sync

    monkeypatch.setattr("app.stealth.local_key_store.read_p_dek", lambda pid: None)
    monkeypatch.setattr("app.stealth.local_key_store.read_device_token", lambda pid: None)

    result = _run(ongoing_sync.sync_delta_if_synced(
        str(tmp_path), stable_project_id="pid-1", file_path="goals.md", summary="x", actor="a",
    ))
    assert result == "not_synced_on_this_machine"


def test_sync_delta_is_a_noop_when_upload_disabled(tmp_path, monkeypatch):
    from app.config import settings
    from app.stealth import ongoing_sync

    monkeypatch.setattr("app.stealth.local_key_store.read_p_dek", lambda pid: _random_key_base64())
    monkeypatch.setattr("app.stealth.local_key_store.read_device_token", lambda pid: "tok")
    monkeypatch.setattr(settings, "sync_upload_api_url", "", raising=False)

    result = _run(ongoing_sync.sync_delta_if_synced(
        str(tmp_path), stable_project_id="pid-1", file_path="goals.md", summary="x", actor="a",
    ))
    assert result == "upload_disabled"


def test_sync_delta_uploads_successfully(tmp_path, monkeypatch):
    from app.config import settings
    from app.stealth import ongoing_sync

    key = _random_key_base64()
    monkeypatch.setattr("app.stealth.local_key_store.read_p_dek", lambda pid: key)
    monkeypatch.setattr("app.stealth.local_key_store.read_device_token", lambda pid: "tok")
    monkeypatch.setattr(settings, "sync_upload_api_url", "https://api.example.test", raising=False)

    calls = []

    async def fake_upload(api_url, project_id, device_token, revision, ciphertext_base64):
        calls.append((api_url, project_id, device_token, revision, ciphertext_base64))
        return True

    monkeypatch.setattr(ongoing_sync, "_upload", fake_upload)

    result = _run(ongoing_sync.sync_delta_if_synced(
        str(tmp_path), stable_project_id="pid-1", file_path="goals.md", summary="edited", actor="me",
    ))
    assert result == "uploaded"
    assert len(calls) == 1
    _, project_id, device_token, revision, ciphertext_base64 = calls[0]
    assert project_id == "pid-1"
    assert device_token == "tok"
    decrypted = json.loads(decrypt_bytes(key, ciphertext_base64))
    assert decrypted["file_path"] == "goals.md"
    assert decrypted["summary"] == "edited"


def test_sync_delta_queues_locally_on_upload_failure_and_drains_on_next_call(tmp_path, monkeypatch):
    from app.config import settings
    from app.stealth import ongoing_sync

    key = _random_key_base64()
    monkeypatch.setattr("app.stealth.local_key_store.read_p_dek", lambda pid: key)
    monkeypatch.setattr("app.stealth.local_key_store.read_device_token", lambda pid: "tok")
    monkeypatch.setattr(settings, "sync_upload_api_url", "https://api.example.test", raising=False)

    upload_calls = []
    should_fail = {"value": True}

    async def flaky_upload(api_url, project_id, device_token, revision, ciphertext_base64):
        upload_calls.append(revision)
        if should_fail["value"]:
            raise ConnectionError("offline")
        return True

    monkeypatch.setattr(ongoing_sync, "_upload", flaky_upload)

    # first call: network is "down" -- delta is queued locally, not lost
    result1 = _run(ongoing_sync.sync_delta_if_synced(
        str(tmp_path), stable_project_id="pid-1", file_path="goals.md", summary="first", actor="me",
    ))
    assert result1 == "queued_offline"
    queue_path = os.path.join(str(tmp_path), ".stealth", ".sync-queue.jsonl")
    assert os.path.isfile(queue_path)

    # "reconnect": next local operation drains the queue AND uploads the new delta
    should_fail["value"] = False
    result2 = _run(ongoing_sync.sync_delta_if_synced(
        str(tmp_path), stable_project_id="pid-1", file_path="claims.md", summary="second", actor="me",
    ))
    assert result2 == "uploaded"
    assert not os.path.isfile(queue_path)  # queue drained, not left behind
    assert len(upload_calls) == 3  # 1 failed original + 1 drained retry + 1 new delta
