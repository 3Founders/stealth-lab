"""
Python-side AES-256-GCM for the local MCP process's OWN ongoing sync
uploads -- the half of docs/local_project_sync_security.md's key hierarchy
that runs after the browser has closed (§ Implementation Closure item 1's
"local process can self-rotate... keep working indefinitely after the
browser closes" requirement, and the ADR's own Phase 6: "local MCP to be
able to continue automatic synchronization after the browser is closed").

The BROWSER performs the FIRST encryption (client-side, Web Crypto,
lib/sync-crypto.ts in prod_frontend) and hands the raw P-DEK to this
machine's local bridge over loopback once (app.mcp_server.
local_sync_bridge's register-local-key step) specifically so this module
can encrypt every SUBSEQUENT delta itself, without needing the browser
open again.

SAME PRIMITIVE, SAME WIRE FORMAT as the browser side, on purpose -- a
delta encrypted here must be decryptable by the SAME client-side code that
decrypts everything else: AES-256-GCM via `cryptography`
(hazmat.primitives.ciphers.aead.AESGCM, the standard, audited Python
implementation -- not a second, competing construction), a fresh random
12-byte IV per call, wire format `base64(iv || ciphertext)` (see
lib/sync-crypto.ts's own docstring for why the IV is prepended rather than
sent as a separate field).

This module NEVER derives a key from anything but the cached raw P-DEK
bytes (app.stealth.local_key_store) -- never from a subject, a token, a
project id, or a path.
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_IV_BYTES = 12


def encrypt_bytes(p_dek_base64: str, plaintext: bytes) -> str:
    """Returns base64(iv || ciphertext) -- the exact wire format
    lib/sync-crypto.ts's decryptBytes expects."""
    key = base64.b64decode(p_dek_base64)
    aesgcm = AESGCM(key)
    iv = os.urandom(_IV_BYTES)
    ciphertext = aesgcm.encrypt(iv, plaintext, None)
    return base64.b64encode(iv + ciphertext).decode("ascii")


def encrypt_json(p_dek_base64: str, data: object) -> str:
    return encrypt_bytes(p_dek_base64, json.dumps(data).encode("utf-8"))


def decrypt_bytes(p_dek_base64: str, combined_base64: str) -> bytes:
    """The inverse -- included for completeness/testability (this module's
    own round-trip test), not because the local process normally decrypts
    anything (it only ever encrypts outgoing deltas)."""
    key = base64.b64decode(p_dek_base64)
    combined = base64.b64decode(combined_base64)
    iv, ciphertext = combined[:_IV_BYTES], combined[_IV_BYTES:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(iv, ciphertext, None)
