"""
Remote sign-in for the MCP server: OAuth 2.1 with Supabase Auth as the
authorization server -- the same identity prod_frontend signs users in with.

The flow a remote MCP client (Claude, Cursor, VS Code, MCP Inspector) runs:

  1. It calls /mcp with no token and gets 401 with
     `WWW-Authenticate: Bearer ..., resource_metadata="<origin>/.well-known/oauth-protected-resource/mcp"`
     (the SDK's RequireAuthMiddleware builds that header from AuthSettings).
  2. It reads that document (RFC 9728, served below) and finds the
     authorization server: `https://<project>.supabase.co/auth/v1`.
  3. It reads Supabase's RFC 8414 metadata, registers itself (dynamic client
     registration), and opens the browser at Supabase's /authorize with PKCE
     (S256).
  4. Supabase sends the user to prod_frontend's /oauth/consent (the
     "authorization path" set in the Supabase dashboard). The user signs in
     there if needed and approves or denies; Supabase then redirects back to
     the client's callback with a code.
  5. The client exchanges the code for a Supabase access token (and refresh
     token) and sends it as a bearer on every call. OidcAwareTokenVerifier
     validates it with the SAME Supabase preset the REST API uses
     (app/services/authn.py: issuer {project}/auth/v1, the project's JWKS,
     ES256/RS256 only).

Why this module serves the RFC 9728 document itself instead of letting the
SDK's route do it: the SDK advertises `scopes_supported = required_scopes`
(`stealthlab:tools`), and Supabase supports only the standard scopes
(openid, email, profile, phone; custom scopes are refused). A client that
asked Supabase for `stealthlab:tools` would fail at /authorize. So the
advertised scopes are Supabase's, while `stealthlab:tools` stays REQUIRED
by the SDK: every token the verifier accepts is granted it server-side,
and per-tool scopes are still enforced by _enforce_tool_scope. Nothing
about enforcement changes here.

Honest limits:
  * RFC 8707 resource binding: Supabase does not document support for the
    `resource` parameter, and its access tokens carry `aud="authenticated"`.
    Audience can't separate services, so tokens are split by origin instead
    (authn.supabase_token_allowed): this server takes only OAuth-flow tokens
    (they carry the app's `client_id`), the REST API only website sessions.
  * Scope step-up (403 + new scope demand) is not offered: Supabase has no
    custom scopes to step up to. A signed-in user already holds the write
    scopes their account allows; a denied tool says why in its result.
"""
from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable, Mapping, Optional

# Supabase OAuth 2.1 server scopes (https://supabase.com/docs/guides/auth/oauth-server/oauth-flows).
# `phone` exists too; not asked for, since nothing here uses a phone number.
SUPABASE_SCOPES: tuple[str, ...] = ("openid", "email", "profile")

PRM_PATH = "/.well-known/oauth-protected-resource"


def supabase_authorization_server(cfg: Any) -> Optional[str]:
    """`{project}/auth/v1` when the Supabase Auth preset is fully configured
    (both SUPABASE_PROJECT_URL and SUPABASE_JWT_AUDIENCE, the same pair
    authn.OidcConfig.from_settings requires), else None."""
    url = getattr(cfg, "supabase_project_url", None)
    aud = getattr(cfg, "supabase_jwt_audience", None)
    if not (url and aud):
        return None
    return url.rstrip("/") + "/auth/v1"


def authorization_server_url(cfg: Any, server_origin: str) -> str:
    """The issuer AuthSettings names. Without Supabase this server names
    itself, as before (it runs no authorization endpoints, so only
    pre-issued tokens work there)."""
    return supabase_authorization_server(cfg) or server_origin


def resource_url(server_origin: str) -> str:
    return f"{server_origin.rstrip('/')}/mcp"


def resource_metadata_url(server_origin: str) -> str:
    """RFC 9728 path-inserted location for the /mcp resource; identical to
    the SDK's build_resource_metadata_url for the same resource."""
    return f"{server_origin.rstrip('/')}{PRM_PATH}/mcp"


def protected_resource_metadata(authorization_server: str, server_origin: str) -> dict[str, Any]:
    return {
        "resource": resource_url(server_origin),
        "authorization_servers": [authorization_server],
        "scopes_supported": list(SUPABASE_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "keळ",
    }


def www_authenticate(server_origin: str, *, error: Optional[str] = None) -> str:
    """The challenge a non-SDK route (the custom routes) sends with its 401,
    so a client can discover sign-in from any endpoint, not only /mcp."""
    parts = [f'resource_metadata="{resource_metadata_url(server_origin)}"']
    if error:
        parts.insert(0, f'error="{error}"')
    return "Bearer " + ", ".join(parts)


def anonymous_reads_enabled(env: Mapping[str, str], *, public_origin: Optional[str],
                            supabase_configured: bool) -> bool:
    """Whether a request with no token is served as the anonymous reader.

    With anonymous reads on, a token-less client never sees a 401, so it
    never starts sign-in (step 1 of the flow above). Default: OFF whenever
    Supabase sign-in is configured -- hosted or on this machine -- so a
    client (Claude Code, opencode, Cursor) with no token is challenged and
    the person signs in in the browser; the client then keeps and refreshes
    the Supabase token itself, and no static token is copied into its
    config. ON when Supabase is not configured, so the stealthlab-connect
    package and a bare local checkout keep working with no token.
    `public_origin` does not change the default; it stays in the signature
    for callers. STEALTHLAB_MCP_ANONYMOUS_READS=1/0 overrides either way."""
    raw = env.get("STEALTHLAB_MCP_ANONYMOUS_READS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return not supabase_configured


class ProtectedResourceMetadataMiddleware:
    """Pure ASGI: answers GET/HEAD/OPTIONS for the RFC 9728 document (both
    the root and the /mcp path-inserted location) and passes everything
    else through untouched. Public by design: the document names where to
    sign in, nothing secret. CORS-open so browser-based clients (MCP
    Inspector) can read it."""

    def __init__(self, app: Callable[..., Awaitable[Any]], metadata: dict[str, Any]) -> None:
        self._app = app
        self._body = json.dumps(metadata).encode("utf-8")
        self._paths = {PRM_PATH, f"{PRM_PATH}/mcp"}

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or scope.get("path", "").rstrip("/") not in self._paths:
            await self._app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        cors = [(b"access-control-allow-origin", b"*"), (b"access-control-allow-methods", b"GET, OPTIONS"),
                (b"access-control-allow-headers", b"mcp-protocol-version")]
        if method == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": cors})
            await send({"type": "http.response.body", "body": b""})
            return
        if method not in ("GET", "HEAD"):
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"allow", b"GET, HEAD, OPTIONS"), *cors]})
            await send({"type": "http.response.body", "body": b""})
            return
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(self._body)).encode()),
            (b"cache-control", b"public, max-age=3600"), *cors]})
        await send({"type": "http.response.body", "body": b"" if method == "HEAD" else self._body})


def wrap_app(app: Callable[..., Awaitable[Any]], cfg: Any, *, server_origin: str,
             public_origin: Optional[str], env: Optional[Mapping[str, str]] = None) -> Callable[..., Awaitable[Any]]:
    """The served ASGI app: the SDK app, behind the anonymous-read injector
    when anonymous reads are on, behind the RFC 9728 document when Supabase
    sign-in is configured (outermost, so it is reachable with no token)."""
    from app.mcp_server.anonymous_read import AnonymousReadInjectorMiddleware

    env = os.environ if env is None else env
    auth_server = supabase_authorization_server(cfg)
    if anonymous_reads_enabled(env, public_origin=public_origin, supabase_configured=auth_server is not None):
        app = AnonymousReadInjectorMiddleware(app)
    if auth_server is not None:
        app = ProtectedResourceMetadataMiddleware(app, protected_resource_metadata(auth_server, server_origin))
    return app
