"""Hinoki's tools in MCP shape, for discover_tools(server="hinoki") and the gateway: names, descriptions and JSON input
schemas built from client.TOOLS, so the listing and what `call` accepts cannot drift apart."""
from __future__ import annotations

from typing import Any

from app.tool_providers.hinoki.client import TOOLS, call

TYPES = {"repo_url": "a public https://github.com/<owner>/<repo> link", "ref": "a branch, tag or commit (default: "
         "the default branch)", "diff": "a unified diff of public source", "finding_id": "an id from scan_repository "
         "or analyze_diff"}


def list_tools() -> list[dict[str, Any]]:
    return [{"name": name, "description": spec["description"],
             "inputSchema": {"type": "object",
                             "required": [p for p, req in spec["params"].items() if req],
                             "properties": {p: {"type": "string", "description": TYPES.get(p, p)}
                                            for p in spec["params"]}}}
            for name, spec in TOOLS.items()]


async def call_tool(name: str, arguments: dict) -> dict[str, Any]:
    return await call(name, arguments)
