"""
Pokemon Game MCP server -- a SEPARATE MCP server/module from the
`stealthlab` server in app/mcp_server/server.py. Deliberately its own
MCPServer instance so it never touches that server's exact-39-tool
surface pinned by packaging/tests/test_server_offline.py::
test_all_registered_tools -- adding a tool here cannot make that
unrelated assertion fail, and this module never imports app.mcp_server.

Scope, honestly stated:
  - This is a thin HTTP pass-through client for the already-running
    `pokemon-agent` server (a separate process this repo does not start
    or own -- confirmed live at http://localhost:8765 during
    implementation: GET / -> {"name": "pokemon-agent", ...}, with a real
    OpenAPI schema at /openapi.json used to verify every endpoint below).
  - No game logic lives here: no action-string parsing/validation, no
    state interpretation, no save/reset policy. Every tool forwards
    exactly what the caller passed and returns exactly what pokemon-agent
    returned (or raises on a connection failure / non-2xx response --
    never a successful-looking response for a real failure).
  - Does not write to .stealth/ and does not touch the StealthLab
    knowledge graph/Postgres in any way -- this module imports nothing
    from app.stealth or app.db.

Endpoints used (verified against the running server's real
/openapi.json, not assumed):
  GET  /state          -> full game-state JSON
  GET  /screenshot      -> current frame as a PNG image (binary body,
                            content-type: image/png -- confirmed via a
                            real HEAD-equivalent request)
  POST /action  {"actions": [...]} -> ActionRequest; pokemon-agent owns
                            the action-string grammar (press_a/press_b/
                            press_start/press_select/press_up/press_down/
                            press_left/press_right, plus its documented
                            walk_*/hold_*/wait_*/a_until_dialog_end
                            forms). This tool does not parse, validate,
                            or rewrite the list -- an unsupported string
                            is forwarded as-is and whatever pokemon-agent
                            does with it (including its own validation
                            error) is what the caller sees.
  POST /save    {"name": ...} -> SaveRequest
  POST /load    {"name": ...} -> SaveRequest (same schema, /load path) --
                            used only when NO game session is active; see
                            game_load's own docstring for the session case.
  GET  /games/current   -> {"active": null | {"id": ..., ...}} -- used by
                            game_load to detect an active session.
  GET  /games            -> {"games": [{"id":..., "latest_save":...}, ...],
                            "active": ...} -- used by game_load to read the
                            active session's own latest-save name (the only
                            documented way to learn it over HTTP; there is
                            no per-session "list saves" endpoint).
  POST /games/{sid}/load -> pokemon-agent's real session-scoped load
                            (restores that session's most recent save and
                            reactivates its manifest). Confirmed live: this
                            is the ONLY endpoint that can load a save written
                            into a session's own saves/ folder -- plain
                            POST /load is hardcoded to pokemon-agent's flat
                            saves/ directory and 404s on a session-scoped
                            save even when it exists on disk (confirmed by
                            reproducing the failure against the real running
                            server, then reading server.py to confirm why).
  POST /games/new {"name": ...} -> NewGameRequest; this is the reset/
                            new-game tool. There is no /reset endpoint --
                            checked directly against /openapi.json rather
                            than assumed. POST /games/new's own
                            description: "Start a NEW game: fresh
                            emulator boot + a fresh session manifest.
                            Resets the emulator to the ROM's title/boot
                            (no save loaded) and creates a new
                            GameSession." HONEST LIMIT: this creates a
                            new session alongside any existing ones
                            (per GET /games) rather than deleting/
                            overwriting save state -- pokemon-agent has
                            no destructive "wipe everything" endpoint,
                            and this module does not approximate one.

Transport: stdio only, no auth -- this is a local developer tool with no
network-exposed HTTP transport, so the "auth applies to HTTP transports
only" carve-out the stealthlab server documents doesn't even come up
here. If an HTTP transport is ever added for this server, it must gate
it the same way app/mcp_server/server.py gates its own (a required
bearer token, checked with secrets.compare_digest, refusing to import
without one set) -- never an unauthenticated network listener.

Built against the same real, installed mcp==2.0.0 SDK the stealthlab
server targets (see that module's own docstring for how this was
confirmed). Image content uses mcp.server.mcpserver.Image, the SDK's own
helper for returning binary image data from a tool -- confirmed by
reading its source (mcp/server/mcpserver/mcpserver.py): it base64-encodes
into a real mcp.types.ImageContent block only because that is the wire
format the MCP spec itself requires for image content blocks, not
because this code chose to stringify a screenshot into ordinary text.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from mcp.server import MCPServer
from mcp.server.mcpserver import Context, Image

from app.config import settings

# Optional, fail-safe experiment-recorder hook (backend/app/experiments/
# pokemon_red_recorder.py). Guarded at import time -- if that module is
# ever missing or broken, game_mcp must still boot and work normally, so
# this falls back to a no-op rather than letting the import failure take
# the whole server down. See that module's own docstring for the full
# design rationale (no seventh tool, no action-string rewriting, a true
# no-op whenever no experiment run is active).
try:
    from app.experiments.pokemon_red_recorder import log_mcp_call as _log_mcp_call
except Exception:  # noqa: BLE001
    def _log_mcp_call(method: str, path: str, json_body: dict | None = None) -> None:
        return None

server = MCPServer(
    name="pokemon-game",
    version="0.1.0",
    instructions=(
        "Thin pass-through tools for the pokemon-agent Pokemon Red "
        "emulator HTTP server (a separate, already-running process). "
        "No game logic lives here -- game_action forwards action "
        "strings verbatim to pokemon-agent, which owns their grammar."
    ),
)

_REQUEST_TIMEOUT_SECONDS = 30.0


def _base_url() -> str:
    return settings.pokemon_agent_base_url.rstrip("/")


async def _request(method: str, path: str, *, json_body: dict | None = None) -> httpx.Response:
    """Shared thin HTTP client, matching this repo's existing convention
    (app/debate/panel.py: a bare httpx.AsyncClient(timeout=...) per call,
    no shared connection-pool wrapper class -- see the MCP-architecture
    inspection this module followed). Raises a clear RuntimeError, never
    returns a successful-looking result, on a connection failure or a
    non-2xx response -- the SDK's own ToolManager (tools/base.py) turns
    any exception raised here into a real isError CallToolResult, so
    this failure reaches the MCP caller honestly rather than being
    swallowed."""
    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.request(method, url, json=json_body)
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Could not reach the pokemon-agent emulator server at {url} "
            f"({exc}). Is pokemon-agent running?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"Request to the pokemon-agent emulator server at {url} timed "
            f"out after {_REQUEST_TIMEOUT_SECONDS}s ({exc})."
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"HTTP request to the pokemon-agent emulator server at {url} "
            f"failed: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"pokemon-agent emulator server returned HTTP "
            f"{response.status_code} for {method} {path}: {response.text}"
        )

    # Observational only -- see the guarded import above. Second try/except
    # here (belt AND suspenders): _log_mcp_call already swallows its own
    # exceptions, but a real tool call must never fail because of a
    # recorder bug, at either layer, so this call site cannot raise either.
    try:
        _log_mcp_call(method, path, json_body)
    except Exception:  # noqa: BLE001
        pass

    return response


@server.tool()
async def game_state(ctx: Context) -> str:
    """
    Return the current Pokemon Red game state.

    Thin wrapper over pokemon-agent's GET /state -- returns that
    response's JSON body verbatim (player/party/bag/battle/dialog/map/
    flags/collision, as pokemon-agent's own /state describes it), text-
    encoded for the MCP text-content channel. This tool does not
    interpret, filter, or summarize the state; whatever pokemon-agent
    reports is exactly what is returned.
    """
    response = await _request("GET", "/state")
    return response.text


@server.tool()
async def game_screenshot(ctx: Context) -> Image:
    """
    Return the current emulator frame as an image.

    Thin wrapper over pokemon-agent's GET /screenshot, which returns the
    frame as a raw PNG. Returned as real MCP image content (via the SDK's
    Image helper, which the tool-call layer converts to an
    mcp.types.ImageContent block) -- not base64 text pasted into an
    ordinary string result.
    """
    response = await _request("GET", "/screenshot")
    return Image(data=response.content, format="png")


@server.tool()
async def game_action(actions: list[str], ctx: Context) -> str:
    """
    Execute a sequence of game actions on the emulator.

    Thin wrapper over pokemon-agent's POST /action, body
    {"actions": actions}. `actions` is forwarded exactly as given -- this
    tool does not parse, validate, normalize, or rewrite any action
    string. pokemon-agent owns the actual action grammar (documented
    forms include press_a, press_b, press_start, press_select, press_up,
    press_down, press_left, press_right, plus its own walk_*, hold_*,
    wait_*, and a_until_dialog_end forms); an action string outside that
    grammar is sent to pokemon-agent unchanged, and whatever it does with
    it -- including its own validation error -- is returned to the
    caller verbatim, never silently corrected or dropped by this tool.
    """
    response = await _request("POST", "/action", json_body={"actions": actions})
    return response.text


@server.tool()
async def game_save(name: str, ctx: Context) -> str:
    """
    Save the current emulator state under the given name.

    Thin wrapper over pokemon-agent's POST /save, body {"name": name}.
    pokemon-agent routes this into the active game session's folder when
    one is active, otherwise its legacy flat saves/ directory -- that
    routing decision is pokemon-agent's, not reimplemented here.
    """
    response = await _request("POST", "/save", json_body={"name": name})
    return response.text


async def _active_game_session() -> Optional[dict]:
    """Return the active session's summary dict from GET /games/current,
    or None if no session is active. Always a fresh live lookup (never
    cached) since the active session can change between calls, e.g.
    after a game_reset."""
    response = await _request("GET", "/games/current")
    return response.json().get("active")


async def _session_latest_save_name(session_id: str) -> Optional[str]:
    """Look up `session_id`'s most-recent save name via GET /games --
    pokemon-agent's session-list endpoint reports `latest_save` per
    session. This is the only documented way to learn a session's save
    name over HTTP; pokemon-agent exposes no per-session "list saves"
    endpoint, so this does not guess at one or at a filesystem path."""
    response = await _request("GET", "/games")
    for summary in response.json().get("games", []):
        if summary.get("id") == session_id:
            return summary.get("latest_save")
    return None


@server.tool()
async def game_load(name: str, ctx: Context) -> str:
    """
    Load a previously saved emulator state by name.

    SESSION/FLAT SAVE-PATH FIX: pokemon-agent's POST /save writes into
    the ACTIVE game session's own saves/ folder whenever a session is
    active (started via game_reset / POST /games/new), but its flat
    POST /load endpoint only ever reads pokemon-agent's legacy flat
    saves/ directory -- confirmed by reproducing this live against the
    real running server (a save made during an active session came back
    HTTP 404 from plain POST /load even though the file existed on
    disk) and by reading pokemon-agent's own server.py, which confirms
    /load never branches on session state the way /save does. This tool
    closes that gap entirely on the CALLER side -- no pokemon-agent
    source is modified, and no endpoint is invented:

      - If a session is active (GET /games/current), this reads that
        session's own latest save name (GET /games ->
        games[].latest_save) and, ONLY if it matches `name` exactly,
        loads it through pokemon-agent's real session-load endpoint,
        POST /games/{sid}/load -- the same endpoint game_reset's own
        session machinery relies on.
      - If `name` does NOT match the active session's latest save
        (including when the session has no saves yet), this refuses
        and raises a clear error rather than silently loading
        pokemon-agent's "latest save" under a different claimed name --
        POST /games/{sid}/load has no per-name selection of its own, so
        calling it on a name mismatch would restore the WRONG game
        state while claiming `name` was loaded.
      - If no session is active, behavior is unchanged from before this
        fix: plain POST /load, body {"name": name} (the flat save/
        path) -- so the pre-session save/load mechanism keeps working
        exactly as it always has.

    HONEST LIMIT: pokemon-agent's session-load endpoint can only load a
    session's single most-recent save, never an arbitrary older named
    save within that session's history. This tool does not work around
    that (doing so would mean guessing at pokemon-agent's on-disk
    layout instead of using its documented API) -- it only verifies the
    requested name actually IS that latest save before using the
    endpoint, and refuses otherwise.
    """
    active = await _active_game_session()
    if active is not None:
        session_id = active["id"]
        latest_save = await _session_latest_save_name(session_id)
        if latest_save != name:
            latest_desc = repr(latest_save) if latest_save else "none yet"
            raise RuntimeError(
                f"Cannot load save '{name}': game session {session_id} is "
                f"active, and pokemon-agent's session-load endpoint (POST "
                f"/games/{{sid}}/load) can only load that session's most "
                f"recent save, which is {latest_desc}. Refusing to load a "
                f"different save under this session rather than silently "
                f"restoring the wrong game state."
            )
        response = await _request("POST", f"/games/{session_id}/load")
        return response.text

    response = await _request("POST", "/load", json_body={"name": name})
    return response.text


@server.tool()
async def game_reset(ctx: Context, name: Optional[str] = None) -> str:
    """
    Start a fresh game (the emulator's real reset/new-game mechanism).

    Thin wrapper over pokemon-agent's POST /games/new, body
    {"name": name}. This is pokemon-agent's own documented reset
    mechanism, confirmed against its live /openapi.json -- there is no
    separate /reset endpoint, and this tool does not invent one.
    pokemon-agent's own description of what this does: "Start a NEW
    game: fresh emulator boot + a fresh session manifest. Resets the
    emulator to the ROM's title/boot (no save loaded) and creates a new
    GameSession."

    HONEST LIMIT: this creates a new game session alongside any existing
    ones (visible via pokemon-agent's own GET /games) rather than
    deleting or overwriting prior saves -- pokemon-agent exposes no
    destructive "erase everything" operation, and this tool does not
    approximate one. `name` is an optional label for the new session;
    omit it to let pokemon-agent assign its own default.
    """
    response = await _request("POST", "/games/new", json_body={"name": name})
    return response.text


if __name__ == "__main__":
    server.run()
