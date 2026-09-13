"""Standalone experiment infrastructure -- deliberately outside app.mcp_server
and app.game_mcp. Nothing here is imported by, or changes, either MCP
server's tool surface; app/game_mcp/server.py only takes an optional,
fail-safe observational hook from pokemon_red_recorder (see that module's
docstring)."""
