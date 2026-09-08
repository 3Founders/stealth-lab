"""
Unit tests for app/services/procedure_display.py -- deterministic
human-facing display metadata. No model, no database.
"""
from __future__ import annotations

from app.services.procedure_display import (
    DISPLAY_METADATA_VERSION,
    build_display_description,
    build_display_metadata,
    build_display_name,
    display_metadata_quality,
)


def test_slug_name_becomes_readable_title_with_acronym_casing():
    assert build_display_name({"name": "mcp-lazy-tool-schema-loading"}) == "MCP Lazy Tool Schema Loading"
    assert build_display_name({"name": "parallel-agent-git-worktree-isolation"}) == (
        "Parallel Agent Git Worktree Isolation"
    )
    assert build_display_name({"name": "aiq-research"}) == "AI-Q Research"
    assert build_display_name({"name": "copilot-cli-quickstart"}) == "Copilot CLI Quickstart"


def test_human_name_preserved():
    assert build_display_name({"name": "Isolate parallel coding agents"}) == "Isolate parallel coding agents"


def test_description_strips_ingestion_preamble_and_leads_with_capability():
    d = build_display_description(
        {"goal": "Use this skill when someone wants public Shopify App Store reviews triaged and clustered."}
    )
    assert d.startswith("Someone wants public Shopify App Store reviews") or d.startswith(
        "Public Shopify App Store reviews"
    )
    assert "Use this skill when" not in d


def test_description_prefers_capability_statement_when_present():
    d = build_display_description(
        {"capability_statement": "Defers loading a tool's full schema until it is first used.",
         "goal": "Use when tool schemas are large."}
    )
    assert d == "Defers loading a tool's full schema until it is first used."


def test_description_truncates_on_word_boundary_with_ellipsis():
    d = build_display_description({"goal": "word " * 200})
    assert len(d) <= 241 and d.endswith("…")


def test_quality_flags_raw_slug_and_short_and_fragment():
    assert display_metadata_quality("", "a real description here") is not None
    assert display_metadata_quality("Real Name", "") is not None
    assert display_metadata_quality("Real Name", "too short") is not None
    assert display_metadata_quality("Real Name", "Never accept support tickets or PII") is not None
    assert display_metadata_quality(
        "Isolate Parallel Agents",
        "Run several coding agents on one repo without their git operations colliding.",
    ) is None


def test_build_display_metadata_returns_triple_with_quality():
    name, desc, err = build_display_metadata(
        {"name": "structural-summary-before-full-read",
         "goal": "Get a deterministic structural outline of a file before reading it in full, "
         "to reduce context cost on orientation."}
    )
    assert name == "Structural Summary Before Full Read"
    assert desc.startswith("Get a deterministic structural outline")
    assert err is None


def test_build_display_metadata_flags_fixture_garbage():
    _, _, err = build_display_metadata({"name": "pm-gold-abc-strong", "goal": "pm-gold-abc-strong goal"})
    assert err is not None


def test_deterministic():
    proc = {"name": "some-skill-name", "goal": "Does a specific useful thing for a specific reason."}
    assert build_display_metadata(proc) == build_display_metadata(dict(proc))
    assert isinstance(DISPLAY_METADATA_VERSION, str) and DISPLAY_METADATA_VERSION
