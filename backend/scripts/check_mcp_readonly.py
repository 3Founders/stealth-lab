"""Read-only smoke check of the configured local MCP server."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import httpx2
from dotenv import load_dotenv
from mcp import Client, ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    url = "http://127.0.0.1:8765/mcp"
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + os.environ["STEALTHLAB_MCP_TOKEN"]},
        timeout=60,
    ) as client:
        response = await client.post(url, headers={"Authorization": ""})
        assert response.status_code == 401, response.status_code
        print("PASS unauthenticated request rejected", flush=True)
        async with streamable_http_client(url, http_client=client) as (read, write):
            async with ClientSession(read, write) as session:
                info = await session.initialize()
                print("PASS initialize", info.server_info, flush=True)
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                assert {"search_procedures", "get_procedure", "find_best_way"} <= names
                print("PASS tools/list", len(names), sorted(names), flush=True)
                for name, args in [
                    ("get_procedure", {"procedure_id": "not-a-uuid"}),
                    ("search_procedures", {"task": "debug Python errors", "require_verified": False}),
                ]:
                    result = await session.call_tool(name, args)
                    data = result.model_dump(mode="json", by_alias=True)
                    assert not data.get("isError"), data
                    payload = data.get("structuredContent", {}).get("result")
                    if name == "search_procedures":
                        rows = json.loads(payload)
                        assert rows, "No searchable procedures for smoke query"
                        print("PASS search", len(rows), "matches", flush=True)
                        known = rows[0]
                        fetched = await session.call_tool("get_procedure", {"procedure_id": known["procedure_id"]})
                        fetched_data = fetched.model_dump(mode="json", by_alias=True)
                        assert not fetched_data.get("isError"), fetched_data
                        procedure = json.loads(fetched_data["structuredContent"]["result"])
                        assert procedure["procedure_id"] == known["procedure_id"]
                        print("PASS get_procedure", known["name"], flush=True)
                        verified = await session.call_tool("search_procedures", {"task": "debug Python errors"})
                        verified_data = verified.model_dump(mode="json", by_alias=True)
                        assert not verified_data.get("isError"), verified_data
                        verified_rows = json.loads(verified_data["structuredContent"]["result"])
                        print("PASS default verified-only search", len(verified_rows), "matches", flush=True)
                    else:
                        assert payload.startswith("REFUSED:"), payload
                        print("PASS malformed ID rejected", flush=True)

    # Exercise the SDK v2 / MCP 2026-07-28 stateless path as well as the
    # legacy initialize path above, since current clients may use either.
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + os.environ["STEALTHLAB_MCP_TOKEN"]},
        timeout=60,
    ) as modern_http:
        transport = streamable_http_client(url, http_client=modern_http)
        async with Client(transport, mode="auto") as modern:
            print("PASS modern protocol", modern.protocol_version, flush=True)
            listing = await modern.list_tools()
            assert {"search_procedures", "get_procedure", "find_best_way"} <= {
                tool.name for tool in listing.tools
            }
            result = await modern.call_tool(
                "search_procedures",
                {"task": "debug Python errors", "require_verified": False},
            )
            assert not result.is_error, result
            print("PASS modern tools/call search_procedures", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
