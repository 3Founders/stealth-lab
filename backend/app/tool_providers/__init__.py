"""Provider adapters: tools kell calls with its own key, so a person needs no sign-in (docs/goal_router.md P1-08/P1-09).

An adapter is a module with:

    SERVERS: tuple[str, ...]            the tool-card ids / server names it answers for
    RISK: str                           "R0".."R4", the tier it declares
    TOOLS: dict[str, dict]              tool name -> {"description", "params": {name: required?}}
    ALIASES: dict[str, str]             other names for the same tools (optional)
    BASE_URL: str                       the host it calls; the gate checks it against the policy's egress list
    READ_ONLY_TOOLS: tuple[str, ...]    tools a valid certificate may run without an approval (optional; R2+ only)
    def available() -> bool             its key is configured
    async def call(tool, arguments) -> dict
        {"ok": bool, "status": "ok" | "failed:<why>", "text": str, "cost_usd": float | None,
         "result_hash": str | None, "untrusted": True, "injection_flags": [...]}

The gate's dispatch (app/tool_routing/gate.py, P1-04) asks `get(server)` first and only falls back to the MCP gateway
when no adapter claims the server. An adapter never reaches for a person's credentials: a missing key is
"failed:not_configured".
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Optional

ADAPTERS = ("app.tool_providers.exa",)   # P1-09 adds "app.tool_providers.hinoki"


def _modules() -> list[ModuleType]:
    out: list[ModuleType] = []
    for name in ADAPTERS:
        try:
            out.append(importlib.import_module(name))
        except ImportError:
            continue
    return out


def get(server: str) -> Optional[ModuleType]:
    """The adapter for a tool card id or server name, else None."""
    s = str(server or "").strip()
    for module in _modules():
        if s in getattr(module, "SERVERS", ()):
            return module
    return None


def all_servers() -> dict[str, ModuleType]:
    return {s: m for m in _modules() for s in getattr(m, "SERVERS", ())}
