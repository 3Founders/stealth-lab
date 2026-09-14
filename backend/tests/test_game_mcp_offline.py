"""
Offline (no running pokemon-agent, no real network) tests for the
Pokemon Game MCP server (backend/app/game_mcp/server.py) -- a separate
MCPServer instance from `stealthlab`'s.

Mirrors this repo's existing MCP offline-test convention (see
test_mcp_minimal_surface_offline.py / test_mcp_six_tool_surface_offline.py):
pin the exact tool-name surface, then exercise each tool as a thin
wrapper. httpx.AsyncClient is replaced with a fake so no real HTTP call
ever leaves the process -- these tests would pass or fail identically
whether or not a real pokemon-agent server happens to be running.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import app.game_mcp.server as gm
from mcp.server.mcpserver import Image


class _FakeAsyncClient:
    """Records every request and returns/raises a preset result --
    replaces httpx.AsyncClient so no real socket is ever opened.

    Supports two modes: a single `response` reused for every call (the
    original, still used by every pre-existing test), or an ordered
    `responses` list consumed one-per-call -- needed for game_load's
    session-routing path, which issues more than one request (GET
    /games/current, GET /games, then the actual load) before returning."""

    def __init__(self, calls, *, response=None, responses=None, exc=None, **_kwargs):
        self._calls = calls
        self._response = response
        # NOT copied: `_request()` opens a brand-new AsyncClient (and thus a
        # brand-new _FakeAsyncClient instance) per call, so `responses` must
        # be the SAME list object shared across every instance for pop(0) to
        # actually drain it across calls instead of always returning the
        # first entry.
        self._responses = responses
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    async def request(self, method, url, json=None):
        self._calls.append({"method": method, "url": url, "json": json})
        if self._exc is not None:
            raise self._exc
        if self._responses is not None:
            return self._responses.pop(0)
        return self._response


class _FakeContext:
    """None of these tools read anything off ctx (unlike the stealthlab
    server's tools, which pull the DB pool from
    ctx.request_context.lifespan_context) -- a bare stand-in is enough."""


def _install_fake_client(monkeypatch, *, response=None, responses=None, exc=None):
    calls: list[dict] = []
    monkeypatch.setattr(
        gm.httpx, "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(
            calls, response=response, responses=responses, exc=exc, **kwargs
        ),
    )
    return calls


def _json_response(status_code: int, payload) -> httpx.Response:
    return httpx.Response(
        status_code, json=payload,
        request=httpx.Request("GET", "http://localhost:8765/x"),
    )


def _text_response(status_code: int, text: str) -> httpx.Response:
    return httpx.Response(
        status_code, text=text,
        request=httpx.Request("GET", "http://localhost:8765/x"),
    )


def _png_response(status_code: int, content: bytes) -> httpx.Response:
    return httpx.Response(
        status_code, content=content,
        headers={"content-type": "image/png"},
        request=httpx.Request("GET", "http://localhost:8765/screenshot"),
    )


# --------------------------------------------------------------- tool surface


def test_registered_tools_are_exactly_the_six_game_tools():
    """Pins this server's tool surface, same discipline as
    packaging/tests/test_server_offline.py::test_all_registered_tools --
    but as its OWN, separate assertion: this server is a different
    MCPServer object, so a new tool here can never make that unrelated
    stealthlab-server test fail, and vice versa."""
    names = sorted(tool.name for tool in gm.server._tool_manager.list_tools())
    assert names == [
        "game_action",
        "game_load",
        "game_reset",
        "game_save",
        "game_screenshot",
        "game_state",
    ]


def test_server_is_a_separate_mcpserver_object_from_stealthlab():
    import app.mcp_server.server as stealthlab_srv

    assert gm.server is not stealthlab_srv.server
    assert gm.server.name != stealthlab_srv.server.name


# --------------------------------------------------------------- game_state


@pytest.mark.asyncio
async def test_game_state_returns_the_real_response_body(monkeypatch):
    state = {"player": {"name": "RED"}, "map": {"map_name": "Pallet Town"}}
    calls = _install_fake_client(monkeypatch, response=_json_response(200, state))

    result = await gm.game_state(ctx=_FakeContext())

    assert calls == [{"method": "GET", "url": "http://localhost:8765/state", "json": None}]
    assert result == _json_response(200, state).text


# --------------------------------------------------------------- game_screenshot


@pytest.mark.asyncio
async def test_game_screenshot_returns_real_image_content_not_base64_text(monkeypatch):
    png_bytes = b"\x89PNG\r\n\x1a\nFAKE-PNG-BYTES"
    _install_fake_client(monkeypatch, response=_png_response(200, png_bytes))

    result = await gm.game_screenshot(ctx=_FakeContext())

    assert isinstance(result, Image), (
        "game_screenshot must return the SDK's Image helper (converted to a "
        "real ImageContent block by the tool-call layer), not a plain string"
    )
    assert result.data == png_bytes
    assert result._mime_type == "image/png"
    # The raw PNG bytes must not have been base64-encoded into a text result
    # by this tool itself -- that encoding is the SDK's job at the wire layer.
    assert not isinstance(result, str)


@pytest.mark.asyncio
async def test_game_screenshot_calls_the_real_screenshot_endpoint(monkeypatch):
    calls = _install_fake_client(monkeypatch, response=_png_response(200, b"\x89PNG"))
    await gm.game_screenshot(ctx=_FakeContext())
    assert calls[0]["url"] == "http://localhost:8765/screenshot"
    assert calls[0]["method"] == "GET"


# --------------------------------------------------------------- game_action


@pytest.mark.asyncio
async def test_game_action_forwards_documented_actions_and_returns_response(monkeypatch):
    calls = _install_fake_client(
        monkeypatch, response=_json_response(200, {"ok": True, "frames": 12}),
    )

    result = await gm.game_action(actions=["press_a", "press_start"], ctx=_FakeContext())

    assert calls == [{
        "method": "POST", "url": "http://localhost:8765/action",
        "json": {"actions": ["press_a", "press_start"]},
    }]
    assert result == _json_response(200, {"ok": True, "frames": 12}).text


@pytest.mark.asyncio
async def test_game_action_does_not_rewrite_an_unsupported_action(monkeypatch):
    """The MCP must not invent its own action grammar or silently correct/
    drop a string it doesn't recognise -- pokemon-agent owns that
    decision. Whatever the caller sent goes out unchanged, and
    pokemon-agent's own rejection (a non-2xx here) surfaces as a real
    error rather than being swallowed or downgraded to a fake success."""
    calls = _install_fake_client(
        monkeypatch,
        response=_json_response(422, {"detail": "unknown action: teleport_to_moon"}),
    )

    with pytest.raises(RuntimeError, match="teleport_to_moon"):
        await gm.game_action(actions=["teleport_to_moon"], ctx=_FakeContext())

    # sent through byte-for-byte, no rewriting/normalizing/dropping, even
    # though pokemon-agent went on to reject it
    assert calls[0]["json"] == {"actions": ["teleport_to_moon"]}


@pytest.mark.asyncio
async def test_game_action_rejects_on_non_2xx_instead_of_hiding_the_failure(monkeypatch):
    """A 4xx/5xx from pokemon-agent must raise, not come back looking
    like a normal successful tool result."""
    _install_fake_client(monkeypatch, response=_text_response(500, "internal error"))

    with pytest.raises(RuntimeError, match="500"):
        await gm.game_action(actions=["press_a"], ctx=_FakeContext())


# --------------------------------------------------------------- game_save / game_load


@pytest.mark.asyncio
async def test_game_save_posts_the_documented_save_request_shape(monkeypatch):
    calls = _install_fake_client(monkeypatch, response=_json_response(200, {"saved": "slot1"}))
    result = await gm.game_save(name="slot1", ctx=_FakeContext())
    assert calls == [{
        "method": "POST", "url": "http://localhost:8765/save",
        "json": {"name": "slot1"},
    }]
    assert result == _json_response(200, {"saved": "slot1"}).text


@pytest.mark.asyncio
async def test_game_load_posts_the_documented_save_request_shape(monkeypatch):
    """No active session (GET /games/current -> {"active": null}):
    behavior is unchanged from before the session-routing fix -- plain
    flat POST /load."""
    calls = _install_fake_client(monkeypatch, responses=[
        _json_response(200, {"active": None}),
        _json_response(200, {"loaded": "slot1"}),
    ])
    result = await gm.game_load(name="slot1", ctx=_FakeContext())
    assert calls == [
        {"method": "GET", "url": "http://localhost:8765/games/current", "json": None},
        {"method": "POST", "url": "http://localhost:8765/load", "json": {"name": "slot1"}},
    ]
    assert result == _json_response(200, {"loaded": "slot1"}).text


@pytest.mark.asyncio
async def test_game_load_uses_session_load_endpoint_when_name_matches_active_session(monkeypatch):
    """This is the actual fix under test: with a session active and the
    requested name matching that session's own latest save, game_load
    must call pokemon-agent's real session-scoped load endpoint --
    POST /games/{sid}/load -- not the flat /load that 404s on
    session-scoped saves (the bug this fix closes)."""
    calls = _install_fake_client(monkeypatch, responses=[
        _json_response(200, {"active": {"id": "sess-1", "name": "run"}}),
        _json_response(200, {"games": [
            {"id": "sess-1", "latest_save": "pokemon_experiment_start_v2"},
        ], "active": "sess-1"}),
        _json_response(200, {"success": True, "restored_save": "pokemon_experiment_start_v2"}),
    ])

    result = await gm.game_load(name="pokemon_experiment_start_v2", ctx=_FakeContext())

    assert calls == [
        {"method": "GET", "url": "http://localhost:8765/games/current", "json": None},
        {"method": "GET", "url": "http://localhost:8765/games", "json": None},
        {"method": "POST", "url": "http://localhost:8765/games/sess-1/load", "json": None},
    ]
    assert result == _json_response(
        200, {"success": True, "restored_save": "pokemon_experiment_start_v2"}
    ).text


@pytest.mark.asyncio
async def test_game_load_refuses_when_name_does_not_match_session_latest_save(monkeypatch):
    """pokemon-agent's session-load endpoint has no per-name selection --
    it always loads whatever the session's latest save happens to be.
    If the caller asked for a DIFFERENT name, silently calling that
    endpoint would restore the wrong game state while claiming the
    requested name was loaded. This must refuse instead, and it must
    never fall through to the flat /load path either (that could load
    an unrelated, differently-scoped save under the same name)."""
    calls = _install_fake_client(monkeypatch, responses=[
        _json_response(200, {"active": {"id": "sess-1", "name": "run"}}),
        _json_response(200, {"games": [
            {"id": "sess-1", "latest_save": "some_other_save"},
        ], "active": "sess-1"}),
    ])

    with pytest.raises(RuntimeError, match="some_other_save"):
        await gm.game_load(name="pokemon_experiment_start_v2", ctx=_FakeContext())

    # Only the two read-only lookups happened -- no load call was made at all,
    # neither the session endpoint nor the flat one.
    assert [c["method"] for c in calls] == ["GET", "GET"]


@pytest.mark.asyncio
async def test_game_load_refuses_when_active_session_has_no_saves_yet(monkeypatch):
    calls = _install_fake_client(monkeypatch, responses=[
        _json_response(200, {"active": {"id": "sess-1", "name": "run"}}),
        _json_response(200, {"games": [{"id": "sess-1", "latest_save": None}], "active": "sess-1"}),
    ])

    with pytest.raises(RuntimeError, match="none yet"):
        await gm.game_load(name="pokemon_experiment_start_v2", ctx=_FakeContext())

    assert [c["method"] for c in calls] == ["GET", "GET"]


@pytest.mark.asyncio
async def test_game_load_session_endpoint_404_surfaces_as_a_clear_error(monkeypatch):
    """Missing saves must surface as errors even on the session-routed
    path -- a 404 from POST /games/{sid}/load itself (e.g. the session's
    save file was deleted from disk after GET /games reported it) must
    raise, not come back as a fake success."""
    calls = _install_fake_client(monkeypatch, responses=[
        _json_response(200, {"active": {"id": "sess-1", "name": "run"}}),
        _json_response(200, {"games": [
            {"id": "sess-1", "latest_save": "pokemon_experiment_start_v2"},
        ], "active": "sess-1"}),
        _text_response(404, "save not found"),
    ])

    with pytest.raises(RuntimeError, match="404"):
        await gm.game_load(name="pokemon_experiment_start_v2", ctx=_FakeContext())

    assert calls[-1]["url"] == "http://localhost:8765/games/sess-1/load"


# --------------------------------------------------------------- game_reset


@pytest.mark.asyncio
async def test_game_reset_uses_the_real_games_new_endpoint_not_an_invented_reset(monkeypatch):
    """The underlying server has no /reset endpoint (confirmed against
    its real /openapi.json) -- the actual reset/new-game mechanism is
    POST /games/new. This pins that this tool calls THAT endpoint,
    guarding against ever silently swapping in a made-up /reset call."""
    calls = _install_fake_client(
        monkeypatch, response=_json_response(200, {"session_id": "abc123"}),
    )

    result = await gm.game_reset(ctx=_FakeContext(), name="run-1")

    assert calls == [{
        "method": "POST", "url": "http://localhost:8765/games/new",
        "json": {"name": "run-1"},
    }]
    assert result == _json_response(200, {"session_id": "abc123"}).text


@pytest.mark.asyncio
async def test_game_reset_defaults_name_to_none(monkeypatch):
    calls = _install_fake_client(monkeypatch, response=_json_response(200, {}))
    await gm.game_reset(ctx=_FakeContext())
    assert calls[0]["json"] == {"name": None}


# --------------------------------------------------------------- failure handling


@pytest.mark.asyncio
async def test_connection_failure_raises_a_clear_error_not_a_fake_success(monkeypatch):
    _install_fake_client(
        monkeypatch,
        exc=httpx.ConnectError("Connection refused", request=httpx.Request("GET", "http://x")),
    )
    with pytest.raises(RuntimeError, match="Could not reach"):
        await gm.game_state(ctx=_FakeContext())


@pytest.mark.asyncio
async def test_timeout_raises_a_clear_error(monkeypatch):
    _install_fake_client(
        monkeypatch,
        exc=httpx.TimeoutException("timed out", request=httpx.Request("GET", "http://x")),
    )
    with pytest.raises(RuntimeError, match="timed out"):
        await gm.game_state(ctx=_FakeContext())


@pytest.mark.asyncio
async def test_http_error_status_surfaces_status_code_and_body(monkeypatch):
    _install_fake_client(monkeypatch, response=_text_response(404, "save not found"))
    with pytest.raises(RuntimeError, match="404"):
        await gm.game_load(name="missing-slot", ctx=_FakeContext())


# ------------------------------------------------- experiment-recorder hook


@pytest.mark.asyncio
async def test_request_helper_calls_the_recorder_hook_with_method_path_and_body(monkeypatch):
    """The recorder integration point: _request() must call _log_mcp_call
    with the exact (method, path, json_body) of every real call, for
    every one of the six tools to be automatically counted -- without
    any tool's own body changing."""
    calls = []
    monkeypatch.setattr(gm, "_log_mcp_call", lambda method, path, json_body=None: calls.append(
        (method, path, json_body)
    ))
    _install_fake_client(monkeypatch, response=_json_response(200, {"actions_executed": 1}))

    await gm.game_action(actions=["press_a"], ctx=_FakeContext())

    assert calls == [("POST", "/action", {"actions": ["press_a"]})]


@pytest.mark.asyncio
async def test_a_raising_recorder_hook_does_not_break_the_real_tool_call(monkeypatch):
    """Requirement: recorder failure must never change game behavior. If
    the hook itself raises, game_state must still return pokemon-agent's
    real response, not an error."""
    def _boom(method, path, json_body=None):
        raise RuntimeError("recorder is broken")

    monkeypatch.setattr(gm, "_log_mcp_call", _boom)
    state = {"player": {"name": "RED"}}
    _install_fake_client(monkeypatch, response=_json_response(200, state))

    result = await gm.game_state(ctx=_FakeContext())

    assert result == _json_response(200, state).text


@pytest.mark.asyncio
async def test_recorder_hook_is_not_called_on_a_failed_pokemon_agent_request(monkeypatch):
    """A 4xx/5xx from pokemon-agent must still raise (existing behavior)
    and must not be logged as a successful call."""
    calls = []
    monkeypatch.setattr(gm, "_log_mcp_call", lambda method, path, json_body=None: calls.append(1))
    _install_fake_client(monkeypatch, response=_text_response(500, "boom"))

    with pytest.raises(RuntimeError):
        await gm.game_action(actions=["press_a"], ctx=_FakeContext())

    assert calls == []


@pytest.mark.asyncio
async def test_a_real_exception_becomes_a_real_tool_error_over_the_mcp_surface(monkeypatch):
    """End-to-end through the SAME ToolManager.call_tool() path a real
    JSON-RPC request goes through (not just a direct Python call) --
    proves the failure reaches an MCP caller as isError=True, never as a
    quietly-successful CallToolResult."""
    from mcp.server.mcpserver.exceptions import ToolError

    _install_fake_client(
        monkeypatch,
        exc=httpx.ConnectError("refused", request=httpx.Request("GET", "http://x")),
    )
    with pytest.raises(ToolError):
        await gm.server._tool_manager.call_tool(
            "game_state", {}, context=_FakeContext(),
        )
