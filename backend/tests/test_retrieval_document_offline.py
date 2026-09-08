"""
Unit tests for app/services/retrieval_document.py -- the canonical
procedure retrieval representation.

These assert the CONTRACT the representation must keep (determinism,
normalization, section coverage, exclusion of volatile data), not the
current byte output. No database, no embedding provider.
"""
from __future__ import annotations

from app.services.retrieval_document import (
    RETRIEVAL_DOCUMENT_VERSION,
    build_procedure_retrieval_document,
    retrieval_document_sha256,
)


def _rich_procedure() -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "procedure_id": "22222222-2222-2222-2222-222222222222",
        "family_id": None,
        "name": "mcp-lazy-tool-schema-loading",
        "goal": "Use when an agent's MCP tool surface is large and per-turn "
        "context is dominated by tool schemas.",
        "capability_statement": "Defer loading a tool's full schema until the "
        "agent actually selects that tool.",
        "steps": [
            {"order": 0, "goal": "List tool names only at conversation start"},
            {"order": 1, "goal": "Load the full schema for a tool on first use"},
            {"order": 2, "goal": "Cache loaded schemas for the rest of the session"},
            {"order": 1, "goal": "Load the full schema for a tool on first use"},  # dup
        ],
        "preconditions": [
            {"subject": "mcp_server", "predicate": "exposes", "object": "many_tools"},
        ],
        "invariants": [{"kind": "numeric", "expr": "tool_count >= 10"}],
        "postconditions": [{"description": "per-turn token cost is lower"}],
        "failure_conditions": [
            {"when": "a tool is used every turn", "description": "no saving, adds a round trip"},
        ],
        "scope": {},
        "exclusions": [],
        "domain": "agent-runtime",
        "domain_payload": {
            "source": {"uri": "https://example/x", "commit": "deadbeef"},  # excluded
            "embedding": {"model_id": "voyage:x"},  # excluded
            "applies_when": "the tool schemas are large and expensive",
            "tool_requirements": ["Read", "Bash", "Read"],
            "dependencies": [
                {"reference": "../schema-cache/SKILL.md", "resolution": "resolved"},
            ],
            "compatibility": "Any MCP-capable client.",
        },
        "evidence_refs": [{"secret": "should-never-appear"}],
        "source_episode_ids": ["33333333-3333-3333-3333-333333333333"],
        "created_by": "skill_md_ingestion",
        "owner_id": "44444444-4444-4444-4444-444444444444",
        "t_created": "2026-09-01T00:00:00Z",
    }


def test_deterministic_and_key_order_invariant():
    proc = _rich_procedure()
    a = build_procedure_retrieval_document(proc)
    b = build_procedure_retrieval_document(dict(reversed(list(proc.items()))))
    c = build_procedure_retrieval_document(dict(proc))
    assert a == b == c
    assert retrieval_document_sha256(a) == retrieval_document_sha256(b)


def test_contains_substantive_procedural_signal():
    doc = build_procedure_retrieval_document(_rich_procedure())
    # name, purpose, when-to-use, steps, tools, deps, domain, constraints, failures
    assert "Name:" in doc and "Purpose:" in doc
    assert "When to use:" in doc
    assert "Steps:" in doc and "Load the full schema for a tool on first use" in doc
    assert "Tools: Bash, Read" in doc  # sorted + de-duplicated
    assert "Depends on: schema cache" in doc  # path + SKILL.md stripped, de-slugged
    assert "Domain: agent-runtime" in doc
    assert "Constraints:" in doc and "tool_count >= 10" in doc
    assert "Fails when:" in doc and "a tool is used every turn" in doc
    assert "requires mcp_server exposes many_tools" in doc  # precondition rendered


def test_excludes_volatile_and_bookkeeping_fields():
    doc = build_procedure_retrieval_document(_rich_procedure())
    for forbidden in (
        "11111111-1111-1111-1111-111111111111",  # row id
        "22222222-2222-2222-2222-222222222222",  # procedure_id
        "33333333-3333-3333-3333-333333333333",  # source episode id
        "44444444-4444-4444-4444-444444444444",  # owner id
        "deadbeef",                               # source commit
        "should-never-appear",                    # evidence ref
        "skill_md_ingestion",                     # created_by
        "2026-09-01",                             # timestamp
        "voyage:x",                               # embedding sub-dict
    ):
        assert forbidden not in doc, forbidden


def test_normalization_strips_markdown_control_and_emoji():
    proc = {
        "name": "x-y",
        "goal": "**Bold** goal\twith\na tab ✅ and emoji ⛔ and � junk",
        "steps": [],
    }
    doc = build_procedure_retrieval_document(proc)
    assert "**" not in doc and "\t" not in doc and "\n" in doc  # newline only between sections
    assert "✅" not in doc and "⛔" not in doc and "�" not in doc
    assert "Bold goal with a tab  and emoji  and  junk".replace("  ", " ") in doc.replace("  ", " ")


def test_sections_omitted_when_absent_not_blank():
    doc = build_procedure_retrieval_document({"name": "solo", "goal": "do a thing", "steps": []})
    assert "Tools:" not in doc and "Depends on:" not in doc and "Steps:" not in doc
    assert "Constraints:" not in doc and "Fails when:" not in doc
    assert doc.startswith("Name: solo") and "Purpose: do a thing" in doc


def test_step_and_purpose_caps_bound_size():
    proc = {
        "name": "big",
        "goal": "g " * 2000,
        "steps": [{"goal": f"step number {i} " + "x" * 200} for i in range(200)],
    }
    doc = build_procedure_retrieval_document(proc)
    # A pathological row cannot produce an unbounded document.
    assert len(doc) < 12000
    # Only the first 40 steps survive.
    assert "41. step number 40" not in doc


def test_slug_name_is_deslugged_but_human_name_preserved():
    assert "Name: mcp lazy tool schema loading" in build_procedure_retrieval_document(
        {"name": "mcp-lazy-tool-schema-loading", "goal": "g", "steps": []}
    )
    assert "Name: Isolate parallel agents" in build_procedure_retrieval_document(
        {"name": "Isolate parallel agents", "goal": "g", "steps": []}
    )


def test_display_name_preferred_over_machine_name_when_present():
    doc = build_procedure_retrieval_document(
        {"name": "mcp-lazy-tool-schema-loading",
         "display_name": "Load tool details only when needed",
         "goal": "g", "steps": []}
    )
    assert "Name: Load tool details only when needed" in doc


def test_dependencies_from_explicit_arg_take_precedence():
    doc = build_procedure_retrieval_document(
        {"name": "x", "goal": "g", "steps": [], "domain_payload": {"dependencies": ["ignored"]}},
        dependencies=[{"dependency_ref": "skill:git-worktree-isolation"}],
    )
    assert "Depends on: git worktree isolation" in doc
    assert "ignored" not in doc


def test_version_constant_is_stable_string():
    assert isinstance(RETRIEVAL_DOCUMENT_VERSION, str) and RETRIEVAL_DOCUMENT_VERSION
