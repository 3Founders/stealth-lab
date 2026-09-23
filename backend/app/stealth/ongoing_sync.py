"""
Ongoing automatic local -> encrypted-server sync (docs/local_project_sync_
security.md §G). Hooks into the EXISTING local write points (today:
`record_stealth_edit` in app.mcp_server.server; the same call can be added
to any other point that already calls app.stealth.journal.append_events --
generate_projection's own projection_regenerated event, record_run_update,
etc. -- without building a new watcher or daemon) rather than polling or
watching anything.

Best-effort and NEVER blocks or fails the caller's own local operation:
every public function here catches its own errors and returns a status
string. A project with no cached P-DEK/sync device credential (i.e. never
synced on this machine, or unsynced) is an instant, cheap no-op.

OFFLINE QUEUE: a failed upload is appended, ALREADY ENCRYPTED, to
`.stealth/.sync-queue.jsonl` (ciphertext only -- this file, if someone
found it, reveals nothing plaintext) and drained opportunistically on the
next call for the same workspace -- not a background timer. This is what
satisfies "local keळ continues working normally offline; pending changes
sync when connectivity returns" without any polling loop.

REVISION: the workspace's own local journal `seq` (app.stealth.journal.
latest_seq) -- already a real, monotonic, per-workspace counter; reused
here rather than inventing a second one.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from app.stealth.atomic import atomic_write
from app.stealth.legacy_context import STEALTH_DIRNAME

_QUEUE_FILENAME = ".sync-queue.jsonl"


def _queue_path(repo_path: str) -> str:
    return os.path.join(repo_path, STEALTH_DIRNAME, _QUEUE_FILENAME)


def _append_to_queue(repo_path: str, *, revision: int, ciphertext_base64: str) -> None:
    path = _queue_path(repo_path)
    line = json.dumps({"revision": revision, "ciphertext_base64": ciphertext_base64}) + "\n"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass  # best-effort local queue; losing a queued delta is not fatal -- the next real change re-triggers a fresh attempt


def _read_and_clear_queue(repo_path: str) -> list[dict]:
    path = _queue_path(repo_path)
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return []
    entries = []
    for ln in lines:
        try:
            entries.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    try:
        os.remove(path)
    except OSError:
        pass
    return entries


async def _upload(api_url: str, project_id: str, device_token: str, revision: int, ciphertext_base64: str) -> bool:
    import httpx

    url = f"{api_url.rstrip('/')}/v1/me/synced-projects/{project_id}/sync"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url, headers={"authorization": f"Bearer {device_token}"},
            json={"revision": revision, "ciphertext_base64": ciphertext_base64},
        )
    return resp.status_code == 200


async def sync_delta_if_synced(
    repo_path: str, *, stable_project_id: str, file_path: str, summary: str, actor: str,
) -> str:
    """Called from an existing local write point right after a real local
    change (e.g. record_stealth_edit). Returns one of: "not_synced_on_this_
    machine" (no cached key -- the common case, cheap and instant),
    "upload_disabled" (SYNC_UPLOAD_API_URL unset on this machine),
    "uploaded", "queued_offline". Never raises."""
    from app.config import settings
    from app.stealth.journal import latest_seq
    from app.stealth.local_key_store import read_device_token, read_p_dek
    from app.stealth.local_sync_encrypt import encrypt_json

    p_dek = read_p_dek(stable_project_id)
    device_token = read_device_token(stable_project_id)
    if not p_dek or not device_token:
        return "not_synced_on_this_machine"

    api_url = settings.sync_upload_api_url
    if not api_url:
        return "upload_disabled"

    # Drain anything queued from a previous, failed attempt first -- best
    # effort, oldest first, never lets a queue failure block the NEW delta.
    for entry in _read_and_clear_queue(repo_path):
        try:
            await _upload(api_url, stable_project_id, device_token, entry["revision"], entry["ciphertext_base64"])
        except Exception:  # noqa: BLE001
            _append_to_queue(repo_path, revision=entry["revision"], ciphertext_base64=entry["ciphertext_base64"])

    revision = latest_seq(repo_path)
    delta = {
        "file_path": file_path, "summary": summary, "actor": actor,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ciphertext_base64 = encrypt_json(p_dek, delta)

    try:
        ok = await _upload(api_url, stable_project_id, device_token, revision, ciphertext_base64)
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        return "uploaded"
    _append_to_queue(repo_path, revision=revision, ciphertext_base64=ciphertext_base64)
    return "queued_offline"
