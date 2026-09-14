"""
DB-free coverage for GET / on the MCP server's ASGI app
(app/mcp_server/server.py). Before this route existed, GET / had no
handler at all -- a bare 404 for every stray liveness probe or
accidental browser hit against this port (a real, observed example this
session: a stray Chrome DevTools /json/version discovery request landing
here). Same offline-import convention as test_mcp_minimal_surface_offline.py:
importing app.mcp_server.server needs no DATABASE_URL.
"""
from __future__ import annotations

import asyncio

import httpx

import app.mcp_server.server as srv


def test_root_route_returns_ok_without_auth():
    async def _run():
        transport = httpx.ASGITransport(app=srv.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            resp = await client.get("/")
            assert resp.status_code == 200
            assert "text/plain" not in resp.headers["content-type"]
            body = resp.json()
            assert body["service"] == "stealthlab-mcp"
            assert body["status"] == "ok"
            assert body["mcp_endpoint"] == "/mcp"

    asyncio.run(_run())


def test_root_route_needs_no_authorization_header():
    """Unauthenticated by design, same posture as /claim-graph and
    /procedure-graph -- a stray probe with no credentials must never see
    a 401 here, only ever the plain health payload."""
    async def _run():
        transport = httpx.ASGITransport(app=srv.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            resp = await client.get("/")
            assert resp.status_code == 200

    asyncio.run(_run())
