"""Exa through kell's own key: web search and page fetch with no sign-in for the person (docs/goal_router.md P1-08).

R0: read-only public web data. The results are third-party data: every answer is labelled untrusted and scanned for
prompt-injection shapes (app/services/untrusted.py), which flags, never cleans.
"""
from app.tool_providers.exa.client import (ADAPTER_ONLY, ALIASES, BASE_URL, GOALS, RISK, SERVERS, TOOLS, available, call)
from app.tool_providers.exa.mcp_server import list_tools

__all__ = ["ADAPTER_ONLY", "ALIASES", "BASE_URL", "GOALS", "RISK", "SERVERS", "TOOLS", "available", "call", "list_tools"]
