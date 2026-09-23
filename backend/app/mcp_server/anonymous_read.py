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
header ourselves, before the request ever reaches the SDK's app. Everything
downstream -- `BearerAuthBackend`, `RequireAuthMiddleware`,
`OidcAwareTokenVerifier.verify_token`, `_enforce_tool_scope` -- runs
completely unmodified and stays correct across SDK upgrades. A caller who
sends a real, non-blank credential (shared secret, OIDC JWT, or anything
else, valid or not) is passed through byte-for-byte untouched -- this
middleware never overrides a caller-supplied credential.

REAL, LIVE-DISCOVERED EDGE CASE (2026-09-23, via the official MCP
Inspector): a browser-based MCP client with its own "Bearer Token" UI
field commonly sends `Authorization: Bearer` (or `Bearer ` with trailing
whitespace, or just an empty value) when that field is left blank --
the header is PRESENT, just carrying no real credential. A naive
"inject only when the header is fully absent" check (this module's own
first version) misses this entirely: the blank header counts as
"present", so nothing gets injected, and the request 401s downstream at
the real verifier for an empty token -- confirmed by direct curl A/B
test (header omitted entirely -> 200; empty `Authorization: Bearer `
-> 401) against the live server. So this treats a present-but-blank
Authorization value the SAME as a fully absent one.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

_ANONYMOUS_READ_TOKEN = "stealthlab-anonymous-read"
_AUTH_HEADER = b"authorization"
_INJECTED_VALUE = f"Bearer {_ANONYMOUS_READ_TOKEN}".encode("ascii")


def _is_blank_bearer(value: bytes) -> bool:
    """True for a genuinely empty credential: no value at all, `Bearer`
    with nothing after it, or all-whitespace. False for any real
    (even invalid/garbage) token -- this function's whole job is telling
    "nothing was supplied" apart from "something was supplied and it's
    wrong", never the two, and never the latter is upgraded to anonymous."""
    text = value.decode("latin-1", errors="replace").strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered == "bearer":
        return True
    if lowered.startswith("bearer") and not text[len("bearer"):].strip():
        return True
    return False


class AnonymousReadInjectorMiddleware:
    """Pure ASGI middleware. Wraps an existing ASGI `app` callable."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self._app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
        auth_values = [v for k, v in headers if k.lower() == _AUTH_HEADER]
        # present AND carries a real (non-blank) value -> never touch it
        if auth_values and not _is_blank_bearer(auth_values[0]):
            await self._app(scope, receive, send)
            return

        # absent, OR present-but-blank -> replace/add with the real
        # anonymous-read credential. Drop any blank Authorization
        # entries first so the header set never carries two.
        kept = [(k, v) for k, v in headers if k.lower() != _AUTH_HEADER]
        new_scope = dict(scope)
        new_scope["headers"] = [*kept, (_AUTH_HEADER, _INJECTED_VALUE)]
        await self._app(new_scope, receive, send)
