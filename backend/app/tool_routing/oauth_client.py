"""OAuth 2.1 client for remote MCP servers that ask for a sign-in (docs/tool_gateway_design.md, phase 2).

The MCP authorization spec (2025-06-18) in one line: a call with no token gets 401 and a `WWW-Authenticate` header
pointing at protected-resource metadata (RFC 9728); that names the authorization server(s); their metadata (RFC 8414 or
OIDC discovery) names the authorize/token/registration endpoints; the client registers (dynamic client registration,
RFC 7591, when offered), sends the person to the authorization endpoint with PKCE S256 and the `resource` parameter
(RFC 8707), and exchanges the code for tokens. This module does exactly that and nothing more:

  discover()  401 challenge -> protected resource metadata -> authorization server metadata
  begin()     registration (cached per server) + PKCE + a pending row -> the URL the person opens
  complete()  the callback's code -> tokens, sealed with app/services/secret_box.py under (owner, server)
  token()     the access token for a call, refreshed when it is about to expire; None when there is none
  forget()    drop the tokens (the existing use_tool "revoke" path)

Honest limits: one authorization-server choice (the first https one the resource names); no Client ID Metadata Documents
yet (DCR only, and a server that offers neither is refused); no revocation endpoint call on forget (the local copy is
deleted, the server's grant may remain until it expires); and the feature is off unless TOOL_GATEWAY_OAUTH=on. Tokens are
never logged. This is phase 2 of the design and needs a security review before it is switched on for everyone.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

import httpx

LINK_TTL_S = 600.0
REFRESH_SKEW_S = 60.0
TIMEOUT_S = 15.0
MAX_BYTES = 200_000
CLIENT_NAME = "kell"


class OAuthRefused(Exception):
    """The sign-in could not be started or finished; the message says why, in words a person can act on."""


def enabled(env: Optional[dict[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("TOOL_GATEWAY_OAUTH", "")).strip().lower() in ("on", "1", "true", "yes")


def redirect_uri(public_origin: str) -> str:
    origin = str(public_origin or "").rstrip("/")
    if not origin.startswith("https://"):
        raise OAuthRefused("kell has no https public address, so a server cannot redirect back after sign-in")
    return f"{origin}/tools/connect/callback"


# --- small helpers ------------------------------------------------------------------------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def parse_challenge(header: Optional[str]) -> dict[str, str]:
    """`Bearer realm="mcp", resource_metadata="https://...", scope="a b"` -> {realm, resource_metadata, scope}."""
    out: dict[str, str] = {}
    text = str(header or "").strip()
    if text.lower().startswith("bearer "):
        text = text[7:]
    for part in text.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip().lower()] = v.strip().strip('"')
    return out


def _https(url: str, what: str) -> str:
    """https, a real host, and not a literal private/loopback address (the resource URL itself is checked by the gateway)."""
    try:
        u = urlsplit(str(url))
    except ValueError as exc:
        raise OAuthRefused(f"{what} is not a URL") from exc
    if u.scheme != "https" or not u.hostname:
        raise OAuthRefused(f"{what} is not https")
    try:
        ip = ipaddress.ip_address(u.hostname)
        if not ip.is_global:
            raise OAuthRefused(f"{what} is not a public address")
    except ValueError:
        pass
    return str(url)


class _Client:
    """An injected httpx client (tests) or a fresh one with no redirects and a time limit."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.given = client
        self.own: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self.given is not None:
            return self.given
        self.own = httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
        return self.own

    async def __aexit__(self, *exc: Any) -> None:
        if self.own is not None:
            await self.own.aclose()


async def _json(client: httpx.AsyncClient, method: str, url: str, **kw: Any) -> dict:
    _https(url, "the sign-in address")
    try:
        r = await client.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise OAuthRefused(f"the sign-in service did not answer ({type(exc).__name__})") from exc
    raw = r.content[:MAX_BYTES]
    if r.status_code >= 300:
        raise OAuthRefused(f"the sign-in service refused ({r.status_code})")
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise OAuthRefused("the sign-in service answered with something that is not JSON") from exc
    return body if isinstance(body, dict) else {}


def _metadata_urls(resource_url: str) -> list[str]:
    """RFC 9728: the path-specific well-known URL first, then the origin's."""
    u = urlsplit(resource_url)
    origin = f"{u.scheme}://{u.netloc}"
    path = u.path.rstrip("/")
    out = []
    if path:
        out.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    out.append(f"{origin}/.well-known/oauth-protected-resource")
    return list(dict.fromkeys(out))


async def discover(resource_url: str, challenge: Optional[dict[str, str]] = None, *, client: Optional[httpx.AsyncClient] = None) -> dict:
    """-> {authorization_endpoint, token_endpoint, registration_endpoint, scope, resource, issuer}. OAuthRefused otherwise."""
    ch = challenge or {}
    async with _Client(client) as c:
        prm: dict = {}
        meta_url = ch.get("resource_metadata") or ""
        candidates = [meta_url] if meta_url else []
        candidates += _metadata_urls(resource_url)
        last: Optional[Exception] = None
        for url in candidates:
            try:
                prm = await _json(c, "GET", url)
                if prm.get("authorization_servers"):
                    break
            except OAuthRefused as exc:
                last = exc
        servers = [s for s in (prm.get("authorization_servers") or []) if isinstance(s, str) and s.startswith("https://")]
        if not servers:
            raise OAuthRefused("this server does not publish a sign-in service kell can use") from last
        issuer = servers[0].rstrip("/")
        asm: dict = {}
        for url in (f"{issuer}/.well-known/oauth-authorization-server", f"{issuer}/.well-known/openid-configuration"):
            try:
                asm = await _json(c, "GET", url)
                if asm.get("authorization_endpoint") and asm.get("token_endpoint"):
                    break
            except OAuthRefused:
                continue
        auth = str(asm.get("authorization_endpoint") or "")
        token = str(asm.get("token_endpoint") or "")
        if not auth or not token:
            raise OAuthRefused("the sign-in service did not say where to authorize or exchange a code")
        methods = asm.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OAuthRefused("the sign-in service does not support the required PKCE method")
        scopes = ch.get("scope") or " ".join(prm.get("scopes_supported") or []) or " ".join(asm.get("scopes_supported") or [])
        return {
            "authorization_endpoint": _https(auth, "the authorize address"),
            "token_endpoint": _https(token, "the token address"),
            "registration_endpoint": _https(str(asm.get("registration_endpoint")), "the registration address") if asm.get("registration_endpoint") else None,
            "scope": " ".join(str(scopes).split())[:500],
            "resource": str(prm.get("resource") or resource_url),
            "issuer": issuer,
        }


async def _client_for(pool: Any, server: str, meta: dict, redirect: str, *, client: Optional[httpx.AsyncClient] = None) -> str:
    """The client_id for this server: cached, else dynamic registration (RFC 7591)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT client_id, token_endpoint FROM tool_oauth_clients WHERE server = $1", server)
    if row and str(row["token_endpoint"]) == meta["token_endpoint"]:
        return str(row["client_id"])
    reg = meta.get("registration_endpoint")
    if not reg:
        raise OAuthRefused("this server's sign-in does not allow kell to register; ask its publisher to allow dynamic clients")
    body = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        **({"scope": meta["scope"]} if meta.get("scope") else {}),
    }
    async with _Client(client) as c:
        answer = await _json(c, "POST", reg, json=body, headers={"accept": "application/json"})
    client_id = str(answer.get("client_id") or "")
    if not client_id:
        raise OAuthRefused("the sign-in service registered kell but returned no client id")
    async with pool.acquire() as conn:
        await conn.execute("""INSERT INTO tool_oauth_clients (server, client_id, authorization_endpoint, token_endpoint, registration_endpoint, scope)
                              VALUES ($1, $2, $3, $4, $5, $6)
                              ON CONFLICT (server) DO UPDATE SET client_id = $2, authorization_endpoint = $3, token_endpoint = $4,
                                                                 registration_endpoint = $5, scope = $6""",
                           server, client_id, meta["authorization_endpoint"], meta["token_endpoint"], reg, meta.get("scope"))
    return client_id


async def begin(pool: Any, owner: str, server: str, resource_url: str, public_origin: str, challenge: Optional[dict[str, str]] = None,
                *, client: Optional[httpx.AsyncClient] = None, check_host: bool = True) -> str:
    """Start a sign-in for one person and server -> the URL to open. Raises OAuthRefused."""
    if check_host:
        from app.tool_routing.gateway import guard_url

        await guard_url(resource_url)
    redirect = redirect_uri(public_origin)
    meta = await discover(resource_url, challenge, client=client)
    client_id = await _client_for(pool, server, meta, redirect, client=client)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge_value = _b64url(hashlib.sha256(verifier.encode()).digest())
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tool_oauth_pending WHERE expires_at < now() OR used_at IS NOT NULL")
        await conn.execute("""INSERT INTO tool_oauth_pending
                                (state_hash, owner_id, server, client_id, verifier, redirect_uri, token_endpoint, resource, expires_at)
                              VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now() + make_interval(secs => $9))""",
                           _hash(state), owner, server, client_id, verifier, redirect, meta["token_endpoint"], meta.get("resource"), LINK_TTL_S)
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "state": state, "code_challenge": challenge_value, "code_challenge_method": "S256",
        **({"scope": meta["scope"]} if meta.get("scope") else {}),
        **({"resource": meta["resource"]} if meta.get("resource") else {}),
    }
    return f"{meta['authorization_endpoint']}?{urlencode(params)}"


async def complete(pool: Any, state: str, code: str, *, client: Optional[httpx.AsyncClient] = None) -> tuple[str, str]:
    """The callback's code -> (owner, server), with the tokens sealed. Raises OAuthRefused."""
    if not state or not code:
        raise OAuthRefused("the sign-in answer is missing its state or code")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""UPDATE tool_oauth_pending SET used_at = now()
                                     WHERE state_hash = $1 AND used_at IS NULL AND expires_at > now()
                                     RETURNING owner_id, server, client_id, verifier, redirect_uri, token_endpoint, resource""",
                                  _hash(state))
    if not row:
        raise OAuthRefused("this sign-in link is no longer valid; ask kell for a new one")
    form = {
        "grant_type": "authorization_code", "code": code, "redirect_uri": str(row["redirect_uri"]),
        "client_id": str(row["client_id"]), "code_verifier": str(row["verifier"]),
        **({"resource": str(row["resource"])} if row["resource"] else {}),
    }
    async with _Client(client) as c:
        answer = await _json(c, "POST", str(row["token_endpoint"]), data=form,
                             headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"})
    if not str(answer.get("access_token") or ""):
        raise OAuthRefused("the sign-in service did not return an access token")
    await _store(pool, str(row["owner_id"]), str(row["server"]), answer, token_endpoint=str(row["token_endpoint"]),
                 client_id=str(row["client_id"]))
    return str(row["owner_id"]), str(row["server"])


async def _store(pool: Any, owner: str, server: str, answer: dict, *, token_endpoint: str, client_id: str) -> None:
    from app.services import secret_box

    access = secret_box.seal(str(answer["access_token"]), org_id=owner, connection_id=server)
    refresh_raw = answer.get("refresh_token")
    refresh = secret_box.seal(str(refresh_raw), org_id=owner, connection_id=server) if refresh_raw else None
    expires_at = None
    try:
        if answer.get("expires_in") is not None:
            expires_at = time.time() + max(0.0, float(answer["expires_in"]))
    except (TypeError, ValueError):
        expires_at = None
    async with pool.acquire() as conn:
        await conn.execute("""INSERT INTO tool_oauth_tokens
                                (owner_id, server, access_token, refresh_token, token_endpoint, client_id, scope, expires_at, updated_at)
                              VALUES ($1, $2, $3, $4, $5, $6, $7,
                                      CASE WHEN $8::double precision IS NULL THEN NULL ELSE to_timestamp($8) END, now())
                              ON CONFLICT (owner_id, server) DO UPDATE
                              SET access_token = $3, refresh_token = COALESCE($4, tool_oauth_tokens.refresh_token),
                                  token_endpoint = $5, client_id = $6, scope = $7,
                                  expires_at = CASE WHEN $8::double precision IS NULL THEN NULL ELSE to_timestamp($8) END,
                                  updated_at = now()""",
                           owner, server, access, refresh, token_endpoint, client_id, str(answer.get("scope") or "") or None, expires_at)


async def token(pool: Any, owner: str, server: str, *, client: Optional[httpx.AsyncClient] = None) -> Optional[str]:
    """The access token for this person and server, refreshed when needed; None when there is no usable one."""
    from app.services import secret_box

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""SELECT access_token, refresh_token, token_endpoint, client_id, expires_at
                                     FROM tool_oauth_tokens WHERE owner_id = $1 AND server = $2""", owner, server)
    if not row:
        return None
    try:
        expires = row["expires_at"].timestamp() if hasattr(row["expires_at"], "timestamp") else float(row["expires_at"])
    except (TypeError, ValueError, AttributeError):
        expires = None
    if expires is None or expires > time.time() + REFRESH_SKEW_S:
        try:
            return secret_box.open_sealed(str(row["access_token"]), org_id=owner, connection_id=server)
        except Exception:  # noqa: BLE001 -- a key rotation or a moved row: treat it as no token
            return None
    refresh_raw = row["refresh_token"]
    if not refresh_raw:
        await forget(pool, owner, server)
        return None
    try:
        refresh = secret_box.open_sealed(str(refresh_raw), org_id=owner, connection_id=server)
    except Exception:  # noqa: BLE001
        await forget(pool, owner, server)
        return None
    form = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": str(row["client_id"])}
    try:
        async with _Client(client) as c:
            answer = await _json(c, "POST", str(row["token_endpoint"]), data=form,
                                 headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"})
        if not answer.get("access_token"):
            raise OAuthRefused("no access token in the refresh answer")
        await _store(pool, owner, server, answer, token_endpoint=str(row["token_endpoint"]), client_id=str(row["client_id"]))
        return str(answer["access_token"])
    except OAuthRefused:
        await forget(pool, owner, server)
        return None


async def forget(pool: Any, owner: str, server: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tool_oauth_tokens WHERE owner_id = $1 AND server = $2", owner, server)


async def has_token(pool: Any, owner: str, server: str) -> bool:
    async with pool.acquire() as conn:
        return bool(await conn.fetchval("SELECT 1 FROM tool_oauth_tokens WHERE owner_id = $1 AND server = $2", owner, server))
