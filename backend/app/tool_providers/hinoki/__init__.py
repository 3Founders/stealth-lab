"""Hinoki through kell's own key: a read-only defensive security review of public code (docs/goal_router.md P1-09).

R3: the gateway runs it only while kell's gate enforces (a certificate, and the R3 policy's approval). Findings are a
model's opinion about third-party code: labelled untrusted and scanned for prompt-injection shapes, never acted on.
"""
from app.tool_providers.hinoki.client import (ADAPTER_ONLY, ALIASES, BASE_URL, READ_ONLY_TOOLS, RISK, SERVERS, TOOLS,
                                              available, call)
from app.tool_providers.hinoki.mcp_server import list_tools

__all__ = ["ADAPTER_ONLY", "ALIASES", "BASE_URL", "READ_ONLY_TOOLS", "RISK", "SERVERS", "TOOLS", "available", "call",
           "list_tools"]
