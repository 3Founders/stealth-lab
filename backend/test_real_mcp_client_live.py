"""
Real, live proof that a REAL MCP client -- not a direct Python function
call, not a FakeContext -- can connect to the running server over the
actual Streamable HTTP wire protocol, authenticate with the real bearer
token, and successfully call the real tools.

Requires the server already running (e.g.
`uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8765`).

Hand-run, not part of pytest.
"""
import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TOKEN = os.environ["STEALTHLAB_MCP_TOKEN"]
URL = "http://127.0.0.1:8765/mcp"


async def main():
    # REAL FINDING this test surfaced: the default client read timeout is
    # too short for a real embedding-API round trip (~3.6s measured) --
    # not a server defect, a client-config lesson worth carrying into any
    # real MCP client config for this server's LLM-backed tools.
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60)

    async with streamable_http_client(URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=60) as session:
            print("=== real initialize() over the wire ===")
            init_result = await session.initialize()
            print(f"server: {init_result.server_info.name} v{init_result.server_info.version}")

            print("\n=== real list_tools() over the wire ===")
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"{len(names)} real tools: {names}")
            assert "search_procedures" in names
            assert "find_best_way" in names
            assert "decide_procedure" in names

            print("\n=== real call_tool('search_procedures', ...) over the wire ===")
            result = await session.call_tool(
                "search_procedures",
                {"task": "explore an unfamiliar repository", "require_verified": False, "limit": 3},
            )
            text = result.content[0].text
            print(text)
            matches = json.loads(text)
            assert isinstance(matches, list) and len(matches) > 0, "FAIL: no real matches returned"

            print("\n=== real call_tool('get_procedure', ...) over the wire ===")
            proc_result = await session.call_tool(
                "get_procedure", {"procedure_id": matches[0]["procedure_id"]},
            )
            proc_text = proc_result.content[0].text
            print(proc_text[:300] + "...")
            proc = json.loads(proc_text)
            assert proc["procedure_id"] == matches[0]["procedure_id"]

    print("\nPASS: a real MCP client, over the real Streamable HTTP wire protocol, "
          "with real bearer-token auth, connected to the real running server and "
          "successfully called real tools -- not a direct function call, not a "
          "FakeContext, the actual outer shell.")


if __name__ == "__main__":
    asyncio.run(main())
