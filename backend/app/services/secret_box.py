"""Encrypting the provider keys an organisation's admins enter in the dashboard.

AES-256-GCM (`cryptography`, the same primitive as app/stealth/local_sync_encrypt.py), a fresh random 12-byte IV per
call, stored as `v1.<base64(iv || ciphertext)>`. The key is ORG_KEY_ENCRYPTION_KEY (base64 of 32 bytes) and is never
derived from anything else. The row's identity (organisation id and connection id) is bound in as associated data, so
a ciphertext copied onto another row, or another organisation's row, fails to decrypt instead of serving that key.

Honest limits: one key, no rotation (`v1` is the only version; a rotation needs a second key setting and a re-encrypt
job); a process that can read the environment variable and the table can read every key, so this protects a database
dump or a backup, not a compromised server. A customer KMS (register_secret_resolver) is the next step for that.
"""
from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

VERSION = "v1"
_IV_BYTES = 12


class SecretBoxError(Exception):
    """Messages never contain the secret or the key."""


def _key() -> bytes:
    raw = settings.org_key_encryption_key
    if not raw:
        raise SecretBoxError("ORG_KEY_ENCRYPTION_KEY is not set, so organisation provider keys cannot be stored or read")
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecretBoxError("ORG_KEY_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != 32:
        raise SecretBoxError("ORG_KEY_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def _aad(org_id: str, connection_id: str) -> bytes:
    return f"org-provider-key|{org_id}|{connection_id}".encode("utf-8")


def seal(plaintext: str, *, org_id: str, connection_id: str) -> str:
    iv = os.urandom(_IV_BYTES)
    ct = AESGCM(_key()).encrypt(iv, plaintext.encode("utf-8"), _aad(str(org_id), connection_id))
    return f"{VERSION}.{base64.b64encode(iv + ct).decode('ascii')}"


def open_sealed(token: str, *, org_id: str, connection_id: str) -> str:
    version, _, body = (token or "").partition(".")
    if version != VERSION or not body:
        raise SecretBoxError("unknown ciphertext version")
    try:
        raw = base64.b64decode(body, validate=True)
        iv, ct = raw[:_IV_BYTES], raw[_IV_BYTES:]
        return AESGCM(_key()).decrypt(iv, ct, _aad(str(org_id), connection_id)).decode("utf-8")
    except (InvalidTag, binascii.Error, ValueError) as exc:
        raise SecretBoxError("the stored key cannot be decrypted (wrong encryption key, or the row was moved)") from exc


def hint(secret: str) -> str:
    """The last four characters, for the dashboard to show which key is stored. Never enough to use the key."""
    s = secret.strip()
    return "..." + s[-4:] if len(s) >= 12 else "..."
