"""
Local project sync bridge -- the narrowly-scoped, capability-gated HTTP
surface an authenticated keळ browser tab uses to discover and initially
sync local `.stealth` projects. Design reference:
docs/local_project_sync_security.md §C (protocol) and its "Implementation
Closure" §1 (credentials) / §2 (discovery). Read both before changing this
file -- this is the single most security-critical new surface this feature
adds (the MCP server had NO browser-facing endpoint at all before this).

PROTOCOL (five steps; routes registered in app.mcp_server.server):
  1. GET  /.well-known/stealthlab-local      -- harmless discovery probe
  2. POST /local-sync/start-handshake        -- browser proves it holds a
                                                real Supabase session
  3. POST /local-sync/list-projects          -- reads the local registry
                                                ONLY (app.stealth.
                                                local_registry) -- never a
                                                filesystem scan
  4. POST /local-sync/prepare-payload        -- returns PLAINTEXT snapshots
                                                for explicitly selected
                                                projects, over loopback only
  5. POST /local-sync/register-local-key     -- browser hands the raw
                                                P-DEK(s) + sync device
                                                token(s) back for local
                                                OS-keychain caching

EVERY state-mutating request is checked, IN THIS ORDER, before any body is
parsed: Origin (exact allowlist match) -> Host (exact 127.0.0.1:<port> or
localhost:<port> match, the DNS-rebinding defense) -> capability token
(exists, unexpired, unused, correct step, correct owner). THIS is the real
security boundary.

CORS (Access-Control-Allow-* response headers, plus OPTIONS preflight
handling below) is present too, but ONLY so the browser's own fetch()
calls are permitted to complete at all -- it is NOT trusted as the
enforcement layer and is always computed from the SAME allowlist the
Origin check above uses, never a wildcard. Per the ADR's explicit
warning, CORS alone would not stop a non-browser client, nor a same-origin
"simple" request that skips preflight, from reaching this server --
that's what the Origin/Host/capability checks are for, unconditionally,
regardless of what CORS headers say.

Capability tokens are held ONLY in an in-memory dict in this process --
never persisted, never logged, single-use, ~60 second TTL, step-scoped (a
token minted for "list" cannot be used for "prepare" or "register").
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_CAPABILITY_TTL_SECONDS = 60.0


@dataclass
class _Capability:
    owner_subject: str
    step: str
    expires_at: float
    used: bool = False


# Process-local only. Never written to disk, never logged. A restart of the
# MCP server naturally invalidates every outstanding handshake, which is
# fine -- handshakes are ~60 seconds end to end and are not meant to
# survive a restart.
_CAPABILITIES: dict[str, _Capability] = {}


def _prune_expired() -> None:
    now = time.time()
    for tok in [t for t, c in _CAPABILITIES.items() if c.expires_at < now]:
        _CAPABILITIES.pop(tok, None)


def _mint_capability(owner_subject: str, step: str) -> str:
    _prune_expired()
    token = secrets.token_urlsafe(32)
    _CAPABILITIES[token] = _Capability(owner_subject=owner_subject, step=step, expires_at=time.time() + _CAPABILITY_TTL_SECONDS)
    return token


def _consume_capability(token: Optional[str], expected_step: str) -> Optional[str]:
    """Returns the owner_subject on success, None on any failure. Marks the
    token used (single-use) as its very first side effect on a match, so a
    concurrent replay of the exact same request cannot both succeed."""
    _prune_expired()
    if not token:
        return None
    cap = _CAPABILITIES.get(token)
    if cap is None or cap.used or cap.step != expected_step or cap.expires_at < time.time():
        return None
    cap.used = True
    _CAPABILITIES.pop(token, None)
    return cap.owner_subject


def _allowed_origins(settings: Any) -> frozenset[str]:
    raw = getattr(settings, "sync_bridge_allowed_origins", "") or ""
    return frozenset(o.strip() for o in raw.split(",") if o.strip())


def _cors_headers(origin: str) -> dict[str, str]:
    """Only ever called with an ALREADY-validated origin (see
    _check_origin_and_host) -- never reflects an arbitrary Origin header
    unchecked, and never a wildcard."""
    return {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": "GET, POST, OPTIONS",
        "access-control-allow-headers": "content-type, x-sync-capability",
        "access-control-max-age": "600",
        "vary": "origin",
    }


def _json(data: dict, *, status: int = 200, origin: Optional[str] = None) -> JSONResponse:
    headers = _cors_headers(origin) if origin else None
    return JSONResponse(data, status_code=status, headers=headers)


def _pool():
    from app.mcp_server.server import _LIFESPAN_STATE

    pool = _LIFESPAN_STATE.get("pool")
    if pool is None:  # pragma: no cover - only before startup / after shutdown
        raise RuntimeError("server not started -- DB pool unavailable")
    return pool


def _check_origin_and_host(request: Request, *, settings: Any, port: int) -> tuple[Optional[str], Optional[JSONResponse]]:
    """The one check every state-mutating route runs FIRST, before parsing
    any body. Returns (origin, None) on success or (None, rejection) on
    failure. Order matters: this must run before body parsing so a
    disallowed caller never gets the server to do any work on their
    payload at all. The returned `origin` is then used ONLY to echo back
    in this one response's CORS headers -- never trusted for anything
    else."""
    origin = request.headers.get("origin", "")
    allowed = _allowed_origins(settings)
    if not allowed or origin not in allowed:
        return None, _json({"error": "origin_not_allowed"}, status=403)

    host = request.headers.get("host", "")
    if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        # The DNS-rebinding defense: a rebound hostname's Origin can look
        # legitimate while its Host header still names the attacker's own
        # domain -- reject on Host regardless of what Origin claimed.
        return None, _json({"error": "host_not_allowed"}, status=403)
    return origin, None


async def handle_preflight(request: Request, *, settings: Any, port: int) -> Response:
    """CORS preflight (OPTIONS) for every /local-sync/* route. NOT the
    security boundary (see module docstring) -- it only lets the browser
    proceed to send the real request, which re-validates Origin + Host +
    capability itself regardless of what this returns."""
    origin, rejection = _check_origin_and_host(request, settings=settings, port=port)
    if rejection is not None:
        return Response(status_code=403)
    return Response(status_code=204, headers=_cors_headers(origin))


async def handle_discover(request: Request, *, port: int) -> Response:  # noqa: ARG001
    """GET /.well-known/stealthlab-local -- reveals only that a local keळ
    process is running. No project data, no filesystem paths, nothing
    account-specific. Same sensitivity level as the pre-existing GET /
    health route. No CORS headers needed: this response contains nothing
    sensitive, and the discovery probe is intentionally readable by any
    origin so the account page can detect a local bridge before knowing
    whether it's even configured to trust this particular deployment's
    origin yet."""
    return JSONResponse({"service": "stealthlab-local-bridge", "version": 1})


async def handle_start_handshake(request: Request, *, settings: Any, port: int) -> Response:
    origin, rejection = _check_origin_and_host(request, settings=settings, port=port)
    if rejection is not None:
        return rejection

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _json({"error": "malformed_body"}, status=400, origin=origin)
    token = body.get("supabase_access_token") if isinstance(body, dict) else None
    if not token or not isinstance(token, str):
        return _json({"error": "missing_token"}, status=400, origin=origin)

    from app.mcp_server.server import _TOKEN_VERIFIER  # the module-level singleton, already resolved from settings
    from app.services.authn import TokenRejected, validate_token_async

    if _TOKEN_VERIFIER._oidc_config is None:  # noqa: SLF001 -- same-module-family access, see docstring
        return _json({"error": "oidc_not_configured"}, status=503, origin=origin)
    try:
        actor = await validate_token_async(
            token, config=_TOKEN_VERIFIER._oidc_config, jwks_provider=_TOKEN_VERIFIER._jwks_provider,  # noqa: SLF001
        )
    except TokenRejected:
        return _json({"error": "invalid_session"}, status=401, origin=origin)

    capability = _mint_capability(actor.subject, "list")
    return _json({"capability": capability}, origin=origin)


async def handle_list_projects(request: Request, *, settings: Any, port: int) -> Response:
    origin, rejection = _check_origin_and_host(request, settings=settings, port=port)
    if rejection is not None:
        return rejection

    owner_subject = _consume_capability(request.headers.get("x-sync-capability"), "list")
    if owner_subject is None:
        return _json({"error": "invalid_or_expired_capability"}, status=401, origin=origin)

    from app.stealth.local_registry import list_local_projects

    projects = list_local_projects()
    next_capability = _mint_capability(owner_subject, "prepare")
    return _json({
        "projects": [
            {
                "project_id": p["stable_project_id"],
                "display_hint": p.get("display_hint"),
                "last_local_activity_at": p.get("last_local_activity_at"),
            }
            for p in projects
        ],
        "capability": next_capability,
    }, origin=origin)


async def handle_prepare_payload(request: Request, *, settings: Any, port: int) -> Response:
    origin, rejection = _check_origin_and_host(request, settings=settings, port=port)
    if rejection is not None:
        return rejection

    owner_subject = _consume_capability(request.headers.get("x-sync-capability"), "prepare")
    if owner_subject is None:
        return _json({"error": "invalid_or_expired_capability"}, status=401, origin=origin)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _json({"error": "malformed_body"}, status=400, origin=origin)
    project_ids = body.get("project_ids") if isinstance(body, dict) else None
    if not isinstance(project_ids, list) or not project_ids or not all(isinstance(p, str) for p in project_ids):
        return _json({"error": "missing_project_ids"}, status=400, origin=origin)

    from app.services.procedure_extraction import _project_id_from_repo_root
    from app.stealth.edit_ledger import list_stealth_edits
    from app.stealth.local_registry import list_local_projects
    from app.stealth.project_sync import read_allowed_snapshot_files

    pool = _pool()
    by_id = {p["stable_project_id"]: p for p in list_local_projects()}
    payloads: dict[str, Any] = {}
    for pid in project_ids:
        entry = by_id.get(pid)
        if entry is None:
            continue  # never a generic file endpoint: only registry-known, explicitly-requested projects
        repo_path = entry["repo_path"]
        if not os.path.isdir(repo_path):
            continue
        legacy_id = _project_id_from_repo_root(repo_path)
        edits = await list_stealth_edits(pool, project_id=legacy_id, limit=500)
        payloads[pid] = {
            "files": read_allowed_snapshot_files(repo_path),
            "activity": [
                {
                    "timestamp": e["created_at"].isoformat() if e["created_at"] else None,
                    "file_path": e["file_path"], "summary": e["summary"], "actor": e["actor"],
                }
                for e in edits
            ],
        }

    next_capability = _mint_capability(owner_subject, "register")
    return _json({"payloads": payloads, "capability": next_capability}, origin=origin)


async def handle_register_local_key(request: Request, *, settings: Any, port: int) -> Response:
    origin, rejection = _check_origin_and_host(request, settings=settings, port=port)
    if rejection is not None:
        return rejection

    owner_subject = _consume_capability(request.headers.get("x-sync-capability"), "register")
    if owner_subject is None:
        return _json({"error": "invalid_or_expired_capability"}, status=401, origin=origin)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _json({"error": "malformed_body"}, status=400, origin=origin)
    entries = body.get("projects") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        return _json({"error": "missing_projects"}, status=400, origin=origin)

    from app.stealth.local_key_store import LocalKeyStoreUnavailable, store_device_token, store_p_dek

    results: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        project_id = entry.get("project_id")
        p_dek = entry.get("p_dek_base64")
        device_token = entry.get("sync_device_token")
        if not (isinstance(project_id, str) and isinstance(p_dek, str) and isinstance(device_token, str)):
            continue
        try:
            store_p_dek(project_id, p_dek)
            store_device_token(project_id, device_token)
            results[project_id] = "cached"
        except LocalKeyStoreUnavailable:
            # Fail closed, never fall back to plaintext storage -- see
            # app.stealth.local_key_store's own docstring. Ongoing
            # automatic sync will simply not be available on this
            # machine; a fresh browser-mediated handshake still works.
            results[project_id] = "keychain_unavailable"

    return _json({"results": results}, origin=origin)
