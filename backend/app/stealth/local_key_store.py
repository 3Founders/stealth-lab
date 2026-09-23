"""
Local OS-keychain-backed storage for the two secrets the local sync bridge
ever caches on disk-adjacent, OS-managed storage: a project's raw P-DEK
(so ongoing sync can encrypt without a browser open) and its sync device
credential (so ongoing sync can authenticate without a browser open). See
docs/local_project_sync_security.md §F and Implementation Closure §1.

NEVER a plaintext file, never `.stealth/meta.json`, never an environment
variable, never a log line. Uses the `keyring` package, which delegates to
Windows Credential Manager / macOS Keychain / Linux Secret Service.

FAIL CLOSED ON LINUX WITHOUT A KEYCHAIN BACKEND (Implementation Closure
§4): a headless/minimal Linux machine may have no Secret Service provider
running at all. `keyring` raises in that case. This module does NOT fall
back to writing the secret to a plaintext file merely because the correct
storage isn't available -- callers must treat that failure as "ongoing
automatic sync is unavailable on this machine" (an availability
degradation, stated honestly to the user), never as a reason to store the
secret less safely.
"""
from __future__ import annotations

from typing import Optional

_P_DEK_SERVICE = "stealthlab-sync-p-dek"
_DEVICE_TOKEN_SERVICE = "stealthlab-sync-device-token"


class LocalKeyStoreUnavailable(Exception):
    """No usable OS credential store on this machine. Callers must degrade
    to "ongoing automatic sync disabled here", never to plaintext storage."""


def _backend():
    import keyring
    from keyring.errors import NoKeyringError

    try:
        kr = keyring.get_keyring()
    except NoKeyringError as exc:
        raise LocalKeyStoreUnavailable(str(exc)) from exc
    return kr


def store_p_dek(project_id: str, p_dek_base64: str) -> None:
    import keyring
    from keyring.errors import KeyringError

    try:
        _backend()
        keyring.set_password(_P_DEK_SERVICE, project_id, p_dek_base64)
    except KeyringError as exc:
        raise LocalKeyStoreUnavailable(str(exc)) from exc


def read_p_dek(project_id: str) -> Optional[str]:
    import keyring
    from keyring.errors import KeyringError

    try:
        _backend()
        return keyring.get_password(_P_DEK_SERVICE, project_id)
    except (KeyringError, LocalKeyStoreUnavailable):
        return None


def delete_p_dek(project_id: str) -> None:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError

    try:
        _backend()
        keyring.delete_password(_P_DEK_SERVICE, project_id)
    except PasswordDeleteError:
        pass  # already absent -- deleting a non-existent entry is not an error here
    except (KeyringError, LocalKeyStoreUnavailable):
        pass  # best-effort: unsync must not fail merely because local cleanup couldn't run


def store_device_token(project_id: str, token: str) -> None:
    import keyring
    from keyring.errors import KeyringError

    try:
        _backend()
        keyring.set_password(_DEVICE_TOKEN_SERVICE, project_id, token)
    except KeyringError as exc:
        raise LocalKeyStoreUnavailable(str(exc)) from exc


def read_device_token(project_id: str) -> Optional[str]:
    import keyring
    from keyring.errors import KeyringError

    try:
        _backend()
        return keyring.get_password(_DEVICE_TOKEN_SERVICE, project_id)
    except (KeyringError, LocalKeyStoreUnavailable):
        return None


def delete_device_token(project_id: str) -> None:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError

    try:
        _backend()
        keyring.delete_password(_DEVICE_TOKEN_SERVICE, project_id)
    except PasswordDeleteError:
        pass
    except (KeyringError, LocalKeyStoreUnavailable):
        pass


def is_available() -> bool:
    try:
        _backend()
        return True
    except LocalKeyStoreUnavailable:
        return False
