"""
Local project sync -- the bridge between an anonymous local `.stealth`
workspace and an authenticated keळ account, and the account-side half of
the client-side end-to-end encryption design in
docs/local_project_sync_security.md. Callers: `app.mcp_server.
local_sync_bridge` (identity + registry helpers), `app.api.me` (the
sync-device and ciphertext-upload REST routes), and the `unsync_local_
project` MCP tool in `app.mcp_server.server`.

NAMING: this feature is "sync" ("local project sync" / "synced project"),
never "claim" -- "claim" already has an established, different meaning
elsewhere in keळ (claims.md, verification claims, the claim graph).
`owner_subject` means the account a workspace is synced to, server-
enforced, never a second/shared identity model.

STABLE PROJECT IDENTITY: `stealth_edit_ledger` / `init_workspace`'s
existing `project_id` is `sha256(realpath(repo_root))[:24]` -- derived
from the filesystem path, and deliberately NOT reused here, because a
sync must survive the workspace folder being renamed or moved (the path
hash would silently change identity on either). `ensure_stable_project_id`
instead mints (once) and persists a real UUID4 inside the EXISTING
`.stealth/meta.json` file, under a new `stable_project_id` key -- no new
hidden directory, no second identity file, no redesign of the existing
metadata schema. It is read back (and preserved) on every later call for
the same workspace. Deliberately does NOT solve a cloned/copied
directory (two copies of the same `meta.json` share one
`stable_project_id`) -- out of scope per this feature's own spec.

OWNERSHIP: `synced_projects.owner_subject` is the verified OIDC/Supabase
`sub` claim -- the SAME value space `stealth_edit_ledger.actor` and
REST's `AuthenticatedPrincipal.subject` already use (see
app.services.authn.Actor, app.services.auth_context.AuthContext.subject,
and app.mcp_server.server.OidcAwareTokenVerifier._human_token, which all
carry the same raw `sub` string end to end) -- not `users.id`, and not a
second identity model. See migration 107/108's own docstrings for why a
raw subject was chosen over a `users(id)` FK.

CIPHERTEXT, NOT PLAINTEXT (implements docs/local_project_sync_security.md
§D/§L): this module NEVER reads `.stealth/*.md` content for the purpose of
storing it server-side -- `read_allowed_snapshot_files` exists only for
`app.mcp_server.local_sync_bridge` to hand plaintext to the BROWSER over
the authenticated loopback connection, never to this module's own
server-side write path. `record_sync_upload` stores exactly the opaque
ciphertext bytes the browser already encrypted, through the EXISTING
`app.services.object_storage` seam -- no new storage subsystem, and the
server never decrypts, inspects, or validates what it stores. Idempotency
is by `revision` (a monotonic counter), not content hash -- see
`record_sync_upload`'s own docstring for why a content-hash key would be
both meaningless (ciphertext is non-deterministic by design) and wrong.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

import asyncpg

from app.stealth.atomic import atomic_write
from app.stealth.legacy_context import STEALTH_DIRNAME

# The single allow-list of `.stealth/*.md` files a bootstrap snapshot may
# ever contain -- see module docstring. Anything else in the workspace
# (source code, .env, the rest of the repository) is never read by this
# module.
ALLOWED_SNAPSHOT_FILES: tuple[str, ...] = (
    "goals.md", "claims.md", "procedures.md", "run.md", "exploration.md", "ledger.md",
)


class AlreadySyncedToAnotherAccount(Exception):
    """The project's stable id is already synced to a different verified subject."""


# --------------------------------------------------------------- identity


def ensure_stable_project_id(repo_path: str) -> str:
    """Read-or-mint this workspace's stable project identity, persisted at
    `.stealth/meta.json`'s `stable_project_id` field. Merges into whatever
    `meta.json` already exists (run-scoped, from `generate_projection`, or
    the no-run marker from `workspace_init._write_bootstrap_marker`, or
    nothing yet) -- never overwrites its other keys. Idempotent: the
    second and every later call for the same workspace returns the exact
    same id."""
    stealth_dir = os.path.join(repo_path, STEALTH_DIRNAME)
    meta_path = os.path.join(stealth_dir, "meta.json")
    existing: dict[str, Any] = {}
    try:
        with open(meta_path, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            existing = loaded
    except (OSError, json.JSONDecodeError):
        pass

    current = existing.get("stable_project_id")
    if isinstance(current, str) and current:
        _upsert_registry_best_effort(repo_path, stable_project_id=current)
        return current

    new_id = str(uuid.uuid4())
    merged = dict(existing)
    merged["stable_project_id"] = new_id
    atomic_write(meta_path, json.dumps(merged, indent=2, default=str))
    _upsert_registry_best_effort(repo_path, stable_project_id=new_id)
    return new_id


def _upsert_registry_best_effort(repo_path: str, *, stable_project_id: str) -> None:
    """Keeps the local multi-project discovery registry (app.stealth.
    local_registry) current on every call -- see that module's own
    docstring for why it exists. Best-effort: a failure here (no writable
    app-data directory on this machine, for example) must never make
    `ensure_stable_project_id` itself fail -- the stable id is already
    durably written to .stealth/meta.json by this point regardless."""
    try:
        from app.stealth.local_registry import upsert_local_project

        upsert_local_project(repo_path, stable_project_id=stable_project_id)
    except OSError:
        pass


# -------------------------------------------------------------------- sync


async def preview_sync(pool: asyncpg.Pool, *, project_id: str, owner_subject: str) -> dict:
    """Read-only: never writes. Tells the caller (and, through it, the
    user, before any confirmation) whether this project is unsynced,
    already synced to them, or already synced to someone else."""
    row = await pool.fetchrow(
        "SELECT owner_subject FROM synced_projects WHERE project_id = $1::uuid", project_id,
    )
    if row is None:
        return {"project_id": project_id, "already_synced_by_you": False, "synced_by_someone_else": False}
    same = row["owner_subject"] == owner_subject
    return {"project_id": project_id, "already_synced_by_you": same, "synced_by_someone_else": not same}


async def sync_project(pool: asyncpg.Pool, *, project_id: str, owner_subject: str) -> dict:
    """Insert-or-verify the sync relationship, server-side, from a
    caller-verified `owner_subject` only -- never a client-supplied one
    (enforced one level up, in the MCP tool, which derives it from the
    verified token, not from any tool argument). A project already synced
    to a DIFFERENT subject raises `AlreadySyncedToAnotherAccount` --
    never silently transferred or overwritten. Re-syncing your own
    already-synced project is a harmless no-op that returns the existing
    row."""
    row = await pool.fetchrow(
        """
        INSERT INTO synced_projects (project_id, owner_subject)
        VALUES ($1::uuid, $2)
        ON CONFLICT (project_id) DO NOTHING
        RETURNING *
        """,
        project_id, owner_subject,
    )
    if row is None:
        row = await pool.fetchrow("SELECT * FROM synced_projects WHERE project_id = $1::uuid", project_id)
        if row["owner_subject"] != owner_subject:
            raise AlreadySyncedToAnotherAccount(project_id)
    return dict(row)


# --------------------------------------------------------------- bootstrap


def read_allowed_snapshot_files(repo_path: str) -> dict[str, str]:
    """Reads exactly the files named in ALLOWED_SNAPSHOT_FILES, and only
    those that actually exist -- never a directory listing, never a glob,
    never anything else under repo_path."""
    stealth_dir = os.path.join(repo_path, STEALTH_DIRNAME)
    files: dict[str, str] = {}
    for name in ALLOWED_SNAPSHOT_FILES:
        path = os.path.join(stealth_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                files[name] = f.read()
        except OSError:
            continue
    return files


class StaleRevision(Exception):
    """The uploaded revision is not newer than what's already stored --
    treated as a successful no-op by the caller (idempotent upload), never
    an error surfaced to the user."""


async def record_sync_upload(
    pool: asyncpg.Pool, *, project_id: str, revision: int, ciphertext: bytes,
    wrapped_p_dek: Optional[str] = None, recovery_salt: Optional[str] = None,
    kdf_params: Optional[dict] = None,
) -> dict:
    """Store one CIPHERTEXT payload (a full snapshot on first sync, or an
    incremental delta afterward -- this function doesn't know or care
    which; that distinction lives entirely in what the browser chose to
    encrypt, never in plaintext the server could inspect) through the
    EXISTING object-storage seam, and advance `synced_projects.revision`.

    Idempotent by `revision`, NOT by content hash: ciphertext is
    non-deterministic by construction (a fresh random IV every time), so
    two uploads of identical plaintext produce different ciphertext bytes
    on purpose (identical ciphertext for identical plaintext would leak
    "these two states are the same" to the server) -- content-hash dedup
    would therefore never fire anyway, and would be the wrong idempotency
    key even if it did. `revision` (the caller's local journal seq) is the
    real idempotency key: a re-upload of a revision already recorded is a
    silent no-op (raises StaleRevision, which callers treat as success).

    `wrapped_p_dek`/`recovery_salt`/`kdf_params` are written only when
    provided (the first sync uses them to establish the account's
    recovery-wrapped key material for this project; later incremental
    uploads omit them and leave the stored values untouched).

    The server never inspects, decrypts, or validates `ciphertext`'s
    contents -- it is opaque bytes as far as this function and everything
    it calls are concerned."""
    from app.services.object_storage import get_store, store_blob

    current = await pool.fetchrow("SELECT revision FROM synced_projects WHERE project_id = $1::uuid", project_id)
    if current is None:
        raise ValueError(f"project {project_id} is not synced")
    if revision <= current["revision"]:
        raise StaleRevision(f"revision {revision} <= stored revision {current['revision']}")

    store = get_store()
    if store is None:
        raise RuntimeError("no object storage configured (OBJECT_STORAGE_URL unset)")
    blob = await store_blob(pool, store, ciphertext, content_type="application/octet-stream")

    row = await pool.fetchrow(
        """
        UPDATE synced_projects
        SET snapshot_sha256 = $2, snapshot_locator = $3, snapshot_size_bytes = $4,
            bootstrapped_at = COALESCE(bootstrapped_at, now()), revision = $5,
            wrapped_p_dek = COALESCE($6, wrapped_p_dek),
            recovery_salt = COALESCE($7, recovery_salt),
            kdf_params = COALESCE($8::jsonb, kdf_params)
        WHERE project_id = $1::uuid
        RETURNING *
        """,
        project_id, blob["sha256"], blob["locator"], blob["size"], revision,
        wrapped_p_dek, recovery_salt, json.dumps(kdf_params) if kdf_params is not None else None,
    )
    return dict(row)


# ------------------------------------------------------------ account read


async def list_synced_projects(pool: asyncpg.Pool, *, owner_subject: str) -> list[dict]:
    """This account's synced projects, newest sync first. Scoped
    strictly to `owner_subject` -- never another account's projects."""
    rows = await pool.fetch(
        "SELECT * FROM synced_projects WHERE owner_subject = $1 ORDER BY synced_at DESC",
        owner_subject,
    )
    return [dict(r) for r in rows]


async def get_synced_project(pool: asyncpg.Pool, *, project_id: str, owner_subject: str) -> Optional[dict]:
    """One synced project, for its owner only. Callers pass a project_id
    that has already been validated as a well-formed UUID (an
    ill-formed one would otherwise surface as a raw asyncpg cast
    error rather than an honest 404 -- see app/api/me.py)."""
    row = await pool.fetchrow(
        "SELECT * FROM synced_projects WHERE project_id = $1::uuid AND owner_subject = $2",
        project_id, owner_subject,
    )
    return dict(row) if row else None


async def read_ciphertext(row: dict) -> Optional[bytes]:
    """Fetch the RAW CIPHERTEXT bytes for a synced_projects row's current
    snapshot/revision. None when the project hasn't been bootstrapped yet,
    or object storage is unavailable on this reader. This function never
    attempts to parse or interpret the bytes -- the server cannot decrypt
    them and must not pretend otherwise; decryption happens client-side,
    in the browser, using the unwrapped P-DEK."""
    locator = row.get("snapshot_locator")
    if not locator:
        return None
    from app.services.object_storage import get_store

    store = get_store()
    if store is None:
        return None
    return await store.get(locator)


async def unsync_project(pool: asyncpg.Pool, *, project_id: str, owner_subject: str) -> bool:
    """Delete the sync relationship and every piece of server-side state
    it produced: the `synced_projects` row (which also removes the
    wrapped P-DEK and the reference to the ciphertext blob) and the
    ciphertext blob itself from object storage. Revoking sync device
    credentials is the CALLER's job (app.services.sync_device_identity.
    revoke_all_sync_device_credentials_for_project) -- kept separate
    because that module has no reason to depend on this one or vice
    versa. Never touches local `.stealth/` files -- this function has no
    filesystem access at all, by construction (it only takes a pool).

    Returns True if a row was actually deleted (i.e. this project was
    synced and owned by this subject), False otherwise -- callers treat
    both "never synced" and "synced by someone else" identically (never
    confirms which)."""
    row = await pool.fetchrow(
        "DELETE FROM synced_projects WHERE project_id = $1::uuid AND owner_subject = $2 RETURNING snapshot_sha256",
        project_id, owner_subject,
    )
    if row is None:
        return False
    if row["snapshot_sha256"]:
        from app.services.object_storage import get_store

        store = get_store()
        if store is not None:
            await pool.execute("DELETE FROM raw_objects WHERE sha256 = $1", row["snapshot_sha256"])
    return True
