"""Provider adapters: tools kell calls with its own key, so a person needs no sign-in (docs/goal_router.md P1-08/P1-09).

An adapter is a module with:

    SERVERS: tuple[str, ...]            the tool-card ids / server names it answers for
    RISK: str                           "R0".."R4", the tier it declares
    TOOLS: dict[str, dict]              tool name -> {"description", "params": {name: required?}}
    ALIASES: dict[str, str]             other names for the same tools (optional)
    BASE_URL: str                       the host it calls; the gate checks it against the policy's egress list
    READ_ONLY_TOOLS: tuple[str, ...]    tools a valid certificate may run without an approval (optional; R2+ only)
    ADAPTER_ONLY: bool                  the provider has no MCP server: the gateway lists and calls the adapter itself
                                        (optional; an R2+ one runs only while the gate enforces)
    def list_tools() -> list[dict]      its tools in MCP shape (with ADAPTER_ONLY)
    def available() -> bool             its key is configured
    async def call(tool, arguments) -> dict
        {"ok": bool, "status": "ok" | "failed:<why>", "text": str, "cost_usd": float | None,
         "result_hash": str | None, "untrusted": True, "injection_flags": [...]}

The gate (app/tool_routing/gate.py, P1-04) takes an adapter's RISK as the call's tier when it cannot decide. The
gateway (mcp_server/server.py `_gateway_call`) calls an ADAPTER_ONLY adapter after the gate; every other server goes
through its MCP card. An adapter never reaches for a person's credentials: a missing key is
"failed:not_configured".
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Optional

ADAPTERS = ("app.tool_providers.exa", "app.tool_providers.hinoki")


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


def adapter_only(server: str) -> Optional[ModuleType]:
    """The adapter that IS the server (ADAPTER_ONLY, e.g. Hinoki): the gateway calls it instead of an MCP card."""
    module = get(server)
    return module if module is not None and getattr(module, "ADAPTER_ONLY", False) else None


def refusal(module: ModuleType) -> Optional[str]:
    """Why the adapter cannot run on this deployment now, else None. An R2+ adapter needs the gate enforcing: with the
    gate off or in shadow, nothing would hold it to its certificate and approval."""
    from app.tool_routing import gate, policy

    name = module.SERVERS[0]
    if not module.available():
        return f"{name} is not configured on this deployment"
    if not policy.risk_at_most(module.RISK, "R1") and gate.gate_mode() != "enforce":
        return f"{name} is {module.RISK}: it runs only while kell's tool gate is enforcing"
    return None
