"""
Offline (no real DB, no network) tests for free-reads/gated-writes:
`AnonymousReadInjectorMiddleware` (app/mcp_server/anonymous_read.py) and
`OidcAwareTokenVerifier.verify_token`'s new `_ANONYMOUS_READ_TOKEN`
branch (app/mcp_server/server.py). Pure ASGI-scope / token-verification
logic -- no server actually listening, no DB.
"""
from __future__ import annotations

import asyncio

import app.mcp_server.server as srv
from app.mcp_server.anonymous_read import _ANONYMOUS_READ_TOKEN, AnonymousReadInjectorMiddleware
from app.services import auth_context as _acx


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# verify_token -- the anonymous-read branch
# ---------------------------------------------------------------------


def test_verify_token_recognizes_the_anonymous_read_token():
    verifier = srv._TOKEN_VERIFIER
    token = _run(verifier.verify_token(_ANONYMOUS_READ_TOKEN))
    assert token is not None
    assert token.subject is None
    assert set(token.scopes) == {"stealthlab:tools", _acx.RETRIEVAL_READ}


def test_verify_token_anonymous_branch_grants_no_write_scope():
    verifier = srv._TOKEN_VERIFIER
    token = _run(verifier.verify_token(_ANONYMOUS_READ_TOKEN))
    assert _acx.KNOWLEDGE_WRITE not in token.scopes
    assert _acx.EXECUTION_RUN not in token.scopes
    assert _acx.KNOWLEDGE_PUBLISH not in token.scopes
    assert _acx.INGESTION_SUBMIT not in token.scopes


def test_verify_token_rejects_a_garbage_token_as_before():
    verifier = srv._TOKEN_VERIFIER
    token = _run(verifier.verify_token("totally-not-a-real-token"))
    assert token is None


# ---------------------------------------------------------------------
# _enforce_tool_scope -- proves the anonymous token is denied write tools
# ---------------------------------------------------------------------


def test_enforce_tool_scope_denies_a_write_tool_for_anonymous_scopes(monkeypatch):
    from mcp.server.auth.provider import AccessToken

    anon = AccessToken(token=_ANONYMOUS_READ_TOKEN, client_id="stealthlab-anonymous",
                        scopes=["stealthlab:tools", _acx.RETRIEVAL_READ])
    monkeypatch.setattr(srv, "get_access_token", lambda: anon)
    try:
        srv._enforce_tool_scope("submit_procedure")
        assert False, "expected PermissionError"
    except PermissionError:
        pass


def test_enforce_tool_scope_allows_a_read_tool_for_anonymous_scopes(monkeypatch):
    from mcp.server.auth.provider import AccessToken

    anon = AccessToken(token=_ANONYMOUS_READ_TOKEN, client_id="stealthlab-anonymous",
                        scopes=["stealthlab:tools", _acx.RETRIEVAL_READ])
    monkeypatch.setattr(srv, "get_access_token", lambda: anon)
    srv._enforce_tool_scope("search_goals")  # must not raise


# ---------------------------------------------------------------------
# AnonymousReadInjectorMiddleware -- the ASGI layer
# ---------------------------------------------------------------------


def _http_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(_message):
    pass


def test_injects_a_real_authorization_header_when_none_present():
    seen_scopes = []

    async def fake_app(scope, receive, send):
        seen_scopes.append(scope)

    mw = AnonymousReadInjectorMiddleware(fake_app)
    _run(mw(_http_scope([]), _noop_receive, _noop_send))

    assert len(seen_scopes) == 1
    headers = dict(seen_scopes[0]["headers"])
    assert headers[b"authorization"] == f"Bearer {_ANONYMOUS_READ_TOKEN}".encode("ascii")


def test_passes_through_an_existing_authorization_header_untouched():
    seen_scopes = []

    async def fake_app(scope, receive, send):
        seen_scopes.append(scope)

    mw = AnonymousReadInjectorMiddleware(fake_app)
    real_header = [(b"authorization", b"Bearer some-real-token")]
    _run(mw(_http_scope(real_header), _noop_receive, _noop_send))

    assert seen_scopes[0]["headers"] == real_header


def test_passes_through_a_malformed_authorization_header_untouched():
    """Never overrides a caller-supplied credential, valid or not."""
    seen_scopes = []

    async def fake_app(scope, receive, send):
        seen_scopes.append(scope)

    mw = AnonymousReadInjectorMiddleware(fake_app)
    garbage_header = [(b"authorization", b"garbage-not-even-bearer-shaped")]
    _run(mw(_http_scope(garbage_header), _noop_receive, _noop_send))

    assert seen_scopes[0]["headers"] == garbage_header


def test_injects_when_authorization_header_present_but_empty():
    """The real Inspector bug (2026-09-23): a browser client's blank
    'Bearer Token' field sends the header PRESENT but empty -- must be
    treated the same as fully absent, not passed through to 401."""
    seen_scopes = []

    async def fake_app(scope, receive, send):
        seen_scopes.append(scope)

    mw = AnonymousReadInjectorMiddleware(fake_app)
    blank_header = [(b"authorization", b"")]
    _run(mw(_http_scope(blank_header), _noop_receive, _noop_send))

    headers = dict(seen_scopes[0]["headers"])
    assert headers[b"authorization"] == f"Bearer {_ANONYMOUS_READ_TOKEN}".encode("ascii")


def test_injects_when_authorization_header_is_bare_bearer_with_nothing_after():
    seen_scopes = []

    async def fake_app(scope, receive, send):
        seen_scopes.append(scope)

    mw = AnonymousReadInjectorMiddleware(fake_app)
    for value in (b"Bearer", b"Bearer ", b"bearer   "):
        seen_scopes.clear()
        mw2 = AnonymousReadInjectorMiddleware(fake_app)
        _run(mw2(_http_scope([(b"authorization", value)]), _noop_receive, _noop_send))
        headers = dict(seen_scopes[0]["headers"])
        assert headers[b"authorization"] == f"Bearer {_ANONYMOUS_READ_TOKEN}".encode("ascii"), value


def test_authorization_header_check_is_case_insensitive():
    seen_scopes = []

    async def fake_app(scope, receive, send):
        seen_scopes.append(scope)

    mw = AnonymousReadInjectorMiddleware(fake_app)
    mixed_case_header = [(b"Authorization", b"Bearer some-real-token")]
    _run(mw(_http_scope(mixed_case_header), _noop_receive, _noop_send))

    assert seen_scopes[0]["headers"] == mixed_case_header


def test_non_http_scope_passes_through_unmodified():
    seen_scopes = []

    async def fake_app(scope, receive, send):
        seen_scopes.append(scope)

    mw = AnonymousReadInjectorMiddleware(fake_app)
    lifespan_scope = {"type": "lifespan"}
    _run(mw(lifespan_scope, _noop_receive, _noop_send))

    assert seen_scopes[0] == lifespan_scope
