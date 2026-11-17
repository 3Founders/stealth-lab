"""Exa's tools in MCP shape, for discover_tools(server="exa") and the gateway: names, descriptions and JSON input
schemas built from client.TOOLS, so the listing and what `call` accepts cannot drift apart."""
from __future__ import annotations

from typing import Any

from app.tool_providers.exa.client import TOOLS

TYPES = {
    "query": "what to search the web for, in words",
    "num_results": "how many results (1-10, default 5)",
    "max_chars": "how many characters of each result's text (default 1500 for a search, 4000 for a fetch)",
    "url": "one page to read, a full https:// link",
    "urls": "pages to read, full https:// links (at most 5)",
}
INTEGERS = {"num_results", "max_chars"}


def list_tools() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, spec in TOOLS.items():
        props: dict[str, Any] = {}
        for p in spec["params"]:
            if p == "urls":
                props[p] = {"type": "array", "items": {"type": "string"}, "description": TYPES.get(p, p)}
            elif p in INTEGERS:
                props[p] = {"type": "integer", "description": TYPES.get(p, p)}
            else:
                props[p] = {"type": "string", "description": TYPES.get(p, p)}
        out.append({"name": name, "description": spec["description"],
                    "inputSchema": {"type": "object",
                                    "required": [p for p, req in spec["params"].items() if req],
                                    "properties": props}})
    return out
