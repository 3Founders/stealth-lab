"""The router never offers an archived server or another coding agent's (app/tool_routing/catalog.py, 2026-11-14).

A card for "run Claude Code / Codex / Cursor for me" is not a tool kell should rank: kell runs the agent itself. A card
whose repository is archived is dead upstream. Both stay in the snapshot and the database; only the search leaves them
out. These are offline: no database, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tool_routing.catalog import hidden_server, registry_cards


def test_archived_server_is_hidden() -> None:
    assert hidden_server("io.github.someone/tool", "Tool", "does a thing", archived=True)


def test_agent_wrapper_is_hidden() -> None:
    assert hidden_server("io.github.constripacity/claude-code-bridge", "Claude Bridge",
                         "Durable local-first MCP relay for independent coding agents.")
    assert hidden_server("io.github.hampsterx/codex-mcp-bridge", "",
                         "Wraps Codex CLI as MCP tools: query, search, structured.")
    assert hidden_server("io.github.qmediat/cursor-mcp", "",
                         "Cursor's agent (cursor-agent CLI) through MCP: any model your plan offers.")
    assert hidden_server("io.github.jayden99236/consult-opencode", "",
                         "Delegate a self-contained task to opencode's free-tier models as a one-shot.")
    assert hidden_server("io.github.georgeracu/google-jules-mcp-server", "",
                         "Model Context Protocol server for Google's Jules AI coding agent")


def test_name_alone_is_not_enough() -> None:
    # "Eclipse Codex" is a banking-regulation library, not the Codex agent; a Proxmox tool that happens to be driven by
    # Cursor is a Proxmox tool.
    assert not hidden_server("io.github.CYAN-ECLIPSE-TECHNOLOGIES-LLC/codex", "Eclipse Codex MCP",
                             "Banking regulation as data: a citation-exact library, a deadline engine.")
    assert not hidden_server("io.github.hackmods/cursor-proxmox-mcp", "Cursor Proxmox MCP",
                             "Manage Proxmox VE via Cursor: QEMU, LXC, storage, HA, firewall, access.")
    assert not hidden_server("io.github.sadri-dridi/journal-ok", "journald cursor shape, value discarded", "")


def test_ordinary_tool_is_kept() -> None:
    assert not hidden_server("ai.exa/exa", "", "Connect AI agents to Exa for web search and research.")
    assert not hidden_server("io.github.ihor-sokoliuk/mcp-searxng", "SearXNG Search",
                             "Search the web through a SearXNG instance.")


def test_installed_and_kell_cards_are_never_hidden() -> None:
    assert not hidden_server("cursor", "Cursor", "an installed server named cursor", source="installed")
    assert not hidden_server("kell:call_model", "Call a model", "run a prompt on another model", source="kell")


def test_the_bundled_snapshot_has_no_archived_cards() -> None:
    cards = registry_cards()
    if not cards:                       # no snapshot in this checkout
        return
    assert not [c for c in cards if c.extra.get("archived")]
    assert not [c for c in cards if hidden_server(c.name, c.title, c.description,
                                                  archived=bool(c.extra.get("archived")), source=c.source)]
