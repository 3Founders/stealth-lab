"""
Offline proving test for `_resolve_caller_identity` (backend/app/mcp_server/
server.py) -- the smallest real wiring of caller identity into the MCP
server's write-path attribution (created_by / approved_by / author),
replacing the static, tool-name-derived strings those call sites used
unconditionally before.

Same env-snapshot gotcha as test_mcp_check_procedure_offline.py: importing
app.mcp_server.server runs a module-level load_dotenv() that can leak
DATABASE_URL into process-wide os.environ for every test collected after
this file -- restored immediately after import, at collection time.

Load-bearing claims proven here:
  - no real identity resolved anywhere -> the honest fallback, never a
    fabricated identity;
  - the MCP SDK's own resolved AccessToken.subject (get_access_token(),
    populated by AuthContextMiddleware) wins over the fallback when
    present -- the real, already-installed identity source this server's
    own auth stack provides;
  - authn.py's OIDC actor contextvar (current_actor_id()) also wins over
    the fallback when present, and is checked as a second real source;
  - the SDK access-token identity takes precedence over authn's actor
    when both are present (it authenticated *this* MCP request).
"""
import os

_ENV_BEFORE_MCP_SERVER_IMPORT = dict(os.environ)

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var, get_access_token
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from app.mcp_server.server import _resolve_caller_identity
from app.services.authn import Actor, current_actor_id, reset_current_actor, set_current_actor

for _key in set(os.environ) - set(_ENV_BEFORE_MCP_SERVER_IMPORT):
    del os.environ[_key]
for _key, _val in _ENV_BEFORE_MCP_SERVER_IMPORT.items():
    if os.environ.get(_key) != _val:
        os.environ[_key] = _val


def _set_access_token(subject):
    token = AccessToken(
        token="irrelevant-in-this-test",
        client_id="stealthlab-local",
        scopes=["stealthlab:tools"],
        subject=subject,
    )
    return auth_context_var.set(AuthenticatedUser(token))


@pytest.fixture(autouse=True)
def _clean_identity_contextvars():
    """Neither contextvar this function reads should leak between tests."""
    yield
    auth_context_var.set(None)
    reset_current_actor(set_current_actor(None))


def test_no_real_identity_returns_honest_fallback():
    assert _resolve_caller_identity(fallback="find_best_way_adhoc") == "find_best_way_adhoc"


def test_sdk_access_token_subject_overrides_fallback():
    tok = _set_access_token("mcp-caller-42")
    try:
        assert _resolve_caller_identity(fallback="mcp_submit_procedure") == "mcp-caller-42"
    finally:
        auth_context_var.reset(tok)


def test_access_token_without_subject_falls_through():
    """A resolved AccessToken with no .subject (StaticTokenVerifier's
    real current behaviour) must not be mistaken for a real identity."""
    tok = _set_access_token(None)
    try:
        assert _resolve_caller_identity(fallback="find_best_way_adhoc") == "find_best_way_adhoc"
    finally:
        auth_context_var.reset(tok)


def test_authn_actor_overrides_fallback_when_no_sdk_token():
    tok = set_current_actor(Actor(subject="oidc-subject-7"))
    try:
        assert _resolve_caller_identity(fallback="mcp_submit_procedure") == "oidc-subject-7"
    finally:
        reset_current_actor(tok)


def test_sdk_access_token_wins_over_authn_actor_when_both_present():
    actor_tok = set_current_actor(Actor(subject="oidc-subject-7"))
    access_tok = _set_access_token("mcp-caller-42")
    try:
        assert _resolve_caller_identity(fallback="x") == "mcp-caller-42"
    finally:
        auth_context_var.reset(access_tok)
        reset_current_actor(actor_tok)


def test_decide_procedure_self_asserted_approver_is_the_fallback_shape():
    """decide_procedure passes the client-supplied approver_id as
    `fallback` -- proving the no-real-identity case reduces to today's
    self-asserted behaviour, unchanged, rather than silently blanking it."""
    assert _resolve_caller_identity(fallback="human-reviewer-claimed-by-client") == (
        "human-reviewer-claimed-by-client"
    )


def test_stdio_posture_structurally_cannot_populate_either_identity_source():
    """LOCAL DEV / STDIO POSTURE (packaging/README.md's Smoke test B,
    `stealthlab-mcp-server --stdio`): confirms, explicitly rather than by
    assumption, that this identity work does not break the documented
    unauthenticated local-dev path.

    Both of `_resolve_caller_identity`'s real sources are populated by
    code that stdio transport never runs, structurally, not just by
    absence of a token at request time:

      - `get_access_token()` (mcp.server.auth.middleware.auth_context) is
        only ever set by `AuthContextMiddleware`, which this file's
        module-level `app = server.streamable_http_app()` wires into the
        ASGI stack. The `--stdio` entrypoint (this file's
        `if __name__ == "__main__": server.run()`) calls MCPServer.run()
        directly and never builds or touches that ASGI app at all -- so
        `AuthContextMiddleware` never runs, and this contextvar can only
        ever read as unset (confirmed here the same way
        test_no_real_identity_returns_honest_fallback confirms the
        unset-contextvar value itself: get_access_token() with nothing
        set returns None).
      - `current_actor_id()` (app.services.authn) is populated only by
        `install_actor_middleware`, which this file never calls at all
        (it is wired onto app.main:app, a wholly separate ASGI app/port)
        -- so it is unset in this process regardless of transport,
        stdio included.

    Both being structurally unset means stdio mode always takes
    `_resolve_caller_identity`'s honest-fallback branch -- proven by the
    same assertion `test_no_real_identity_returns_honest_fallback` makes,
    restated here under the stdio-specific reasoning above so the posture
    claim isn't left implicit."""
    assert get_access_token() is None
    assert current_actor_id() is None
    assert _resolve_caller_identity(fallback="stdio-local-dev-caller") == (
        "stdio-local-dev-caller"
    )
