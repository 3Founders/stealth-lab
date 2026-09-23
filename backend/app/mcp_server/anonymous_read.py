"""
Free-reads/gated-writes (2026-09-23): a thin outer ASGI middleware that
lets a caller reach the MCP server with NO `Authorization` header at all,
without touching (or forking) the SDK's own auth wiring at all.

WHY THIS SHAPE, not a fork of the SDK's `RequireAuthMiddleware`: confirmed
by direct source research (mcp==2.0.0) that the SDK's bearer-auth backend
(`mcp/server/auth/middleware/bearer_auth.py::BearerAuthBackend.authenticate`)
returns `None` -- WITHOUT ever calling `token_verifier.verify_token()` --
the instant no `Authorization` header is present at all. So a caller with
zero header never even reaches our own `OidcAwareTokenVerifier.verify_token`
branch that already recognizes `_ANONYMOUS_READ_TOKEN` (server.py) --
`RequireAuthMiddleware` 401s first, upstream of that check entirely.

Forking/replacing the SDK's own auth middleware to fix that would be
version-fragile (its enforcement wraps the WHOLE `/mcp` route as one unit,
confirmed no per-tool granularity exists to hook into instead). The robust
fix: inject the literal `_ANONYMOUS_READ_TOKEN` as a real `Authorization`
header ourselves, ONLY when the caller sent none, before the request ever
reaches the SDK's app. Everything downstream -- `BearerAuthBackend`,
`RequireAuthMiddleware`, `OidcAwareTokenVerifier.verify_token`,
`_enforce_tool_scope` -- runs completely unmodified and stays correct
across SDK upgrades. A caller who DOES send a real header (shared secret,
OIDC JWT, or anything else, valid or not) is passed through byte-for-byte
untouched -- this middleware never overrides a caller-supplied credential.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

_ANONYMOUS_READ_TOKEN = "stealthlab-anonymous-read"
_AUTH_HEADER = b"authorization"


class AnonymousReadInjectorMiddleware:
    """Pure ASGI middleware. Wraps an existing ASGI `app` callable."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self._app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
        has_auth = any(k.lower() == _AUTH_HEADER for k, _v in headers)
        if has_auth:
            await self._app(scope, receive, send)
            return

        new_scope = dict(scope)
        new_scope["headers"] = [
            *headers,
            (_AUTH_HEADER, f"Bearer {_ANONYMOUS_READ_TOKEN}".encode("ascii")),
        ]
        await self._app(new_scope, receive, send)
