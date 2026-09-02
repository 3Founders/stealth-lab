"""
Offline proving tests for app/local_agent/historical_bootstrap.py --
Ideal V1 directive §4-§13 (historical local memory bootstrap).

Real objects only: a real LocalProcedureStore over a real sqlite file in
tmp_path, real fixture files on disk shaped like the ACTUAL export
formats (Claude `conversations.json`, ChatGPT `conversations.json`,
Claude Code session transcripts). No DB, no network, no mocks of the
code under test. Embeddings are never required here: the lexical dedup
path is the honest fallback, exercised on purpose.
"""
from __future__ import annotations

import json

import pytest

from app.local_agent.historical_bootstrap import (
    EVIDENCE_EXECUTED,
    EVIDENCE_RECOMMENDED,
    PROVENANCE,
    HistoricalEpisode,
    bootstrap_claude_code_traces,
    bootstrap_repository,
    converge_episode,
    parse_chatgpt_export,
    parse_claude_export,
    run_bootstrap,
)
from app.local_agent.local_store import LocalProcedureStore


@pytest.fixture()
def store(tmp_path):
    return LocalProcedureStore(str(tmp_path), db_path=str(tmp_path / "lib.db"))


def _make_repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "skills" / "deploy").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "skills" / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy-service\ndescription: Deploy the service to staging\n"
        "applies_when: repository uses docker compose\n---\n"
        "## Steps\n1. build the image\n2. push to registry\n3. restart the stack\n",
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest -q\n      - run: ruff check .\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "# Agent instructions\n- always run the test suite before committing\n",
        encoding="utf-8",
    )
    (root / "scripts" / "deploy.sh").write_text("echo deploy\n", encoding="utf-8")
    (root / "README.md").write_text(
        "This project is a web service. It is written in Python and you could "
        "run pytest.\n",  # prose: must NOT become a procedure
        encoding="utf-8",
    )
    return str(root)


def _claude_export_file(tmp_path):
    convs = [{
        "uuid": "c-exec-1",
        "name": "fix failing auth test",
        "created_at": "2026-08-01T10:00:00Z",
        "chat_messages": [
            {"sender": "human", "text": "auth tests are failing"},
            {"sender": "assistant", "text": "Let me run the suite.\n```\npytest tests/test_auth.py -q\n```"},
            {"sender": "human", "text": "It ran successfully and the tests passed after your fix."},
        ],
    }, {
        "uuid": "c-disc-1",
        "name": "architecture brainstorm",
        "chat_messages": [
            {"sender": "human", "text": "Should we use a queue?"},
            {"sender": "assistant", "text": "You could use Redis, but let's discuss tradeoffs first."},
        ],
    }, {
        "uuid": "c-reco-1",
        "name": "deployment advice",
        "chat_messages": [
            {"sender": "assistant", "text": "I would run `make release` -- here is how:\n```\nmake release\n```"},
        ],
    }]
    path = tmp_path / "claude_conversations.json"
    path.write_text(json.dumps(convs), encoding="utf-8")
    return str(path)


def _chatgpt_export_file(tmp_path):
    def node(role, text, t, mid):
        return {"message": {"author": {"role": role}, "id": mid,
                            "create_time": t, "content": {"parts": [text]}}}
    convs = [{
        "uuid": "g-exec-1",
        "title": "add retry logic",
        "create_time": 1754000000,
        "mapping": {
            "a": node("user", "how do I add retries to our client?", 1754000000, "m1"),
            "b": node("assistant", "Use tenacity:\n```\npip install tenacity\n```", 1754000001, "m2"),
            "c": node("user", "executed it and the tests passed", 1754000002, "m3"),
        },
    }]
    path = tmp_path / "chatgpt_conversations.json"
    path.write_text(json.dumps(convs), encoding="utf-8")
    return str(path)


def _trace_dir(tmp_path):
    d = tmp_path / "traces"
    d.mkdir()
    (d / "sess-1.jsonl").write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"content": "run the migration"}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "python scripts/migrate.py --once"}}]}},
            ),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}},
            ),
        ]),
        encoding="utf-8",
    )
    (d / "chat-only.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "what does this repo do?"}}),
        encoding="utf-8",
    )
    return str(d)


# ---------------------------------------------------------------------------
# Source parsers
# ---------------------------------------------------------------------------


def test_repo_bootstrap_extracts_only_genuinely_procedural_material(tmp_path):
    episodes = bootstrap_repository(_make_repo(tmp_path))
    goals = [e.goal for e in episodes]
    assert any("staging" in g.lower() for g in goals)                    # SKILL.md
    assert any("CI workflow: CI" in g for g in goals)                     # workflow run steps
    assert any("repo agent instructions" in g for g in goals)             # AGENTS.md
    assert any("run deploy.sh" in g for g in goals)                       # script exists
    assert all(e.evidence_status == EVIDENCE_RECOMMENDED for e in episodes)  # §8
    assert not any("web service" in g for g in goals)                     # prose never a procedure


def test_claude_export_distinguishes_discussion_from_execution(tmp_path):
    episodes = parse_claude_export(_claude_export_file(tmp_path))
    by_id = {e.source_id: e for e in episodes}
    executed = by_id["c-exec-1"]
    assert executed.evidence_status == EVIDENCE_EXECUTED
    assert executed.steps[0]["properties"]["command"] == "pytest tests/test_auth.py -q"
    reco = by_id["c-reco-1"]
    assert reco.evidence_status == EVIDENCE_RECOMMENDED   # §7: "I would run" ≠ executed
    assert "c-disc-1" not in by_id                         # pure discussion: no candidate


def test_chatgpt_export_parses_mapping_tree_with_same_conservatism(tmp_path):
    episodes = parse_chatgpt_export(_chatgpt_export_file(tmp_path))
    assert len(episodes) == 1
    assert episodes[0].evidence_status == EVIDENCE_EXECUTED
    assert episodes[0].steps[0]["properties"]["command"] == "pip install tenacity"
    assert episodes[0].source_type == "chatgpt_chat"


def test_trace_bootstrap_only_records_real_tool_invocations(tmp_path):
    episodes = bootstrap_claude_code_traces(_trace_dir(tmp_path))
    assert len(episodes) == 1                              # chat-only session: nothing
    ep = episodes[0]
    assert ep.evidence_status == EVIDENCE_EXECUTED
    assert [s["properties"]["command"] for s in ep.steps] == [
        "python scripts/migrate.py --once", "pytest -q",
    ]


# ---------------------------------------------------------------------------
# Convergence + dedup
# ---------------------------------------------------------------------------


def test_converge_births_candidate_never_verified(store):
    ep = HistoricalEpisode(
        source_type="claude_code_trace", source_id="s.jsonl",
        source_location="s.jsonl#1", goal="run the test suite",
        steps=[{"order": 0, "goal": "pytest -q",
                "properties": {"command": "pytest -q", "evidence_status": EVIDENCE_EXECUTED}}],
        evidence_status=EVIDENCE_EXECUTED,
    )
    result = converge_episode(store, ep)
    assert result["status"] == "captured"
    row = store.get_local_procedure(result["id"])
    assert row["verification_state"] == "candidate"   # §10: history ≠ verified capability
    assert row["staleness"] == "fresh"
    assert row["provenance"] == PROVENANCE
    ref = row["evidence_refs"][0]
    assert ref["source_type"] == "claude_code_trace"
    assert ref["privacy"] == "local"                  # §9: private by construction


def test_same_workflow_from_two_sources_converges_into_one_canonical_row(store):
    """§11: repo + trace describing the same workflow -> ONE procedure,
    TWO preserved evidence sources."""
    ep1 = HistoricalEpisode(
        source_type="repository", source_id="ci.yml", source_location="ci.yml#1",
        goal="run the test suite before every push",
        steps=[{"order": 0, "goal": "pytest -q", "properties": {"command": "pytest -q"}}],
        evidence_status=EVIDENCE_RECOMMENDED,
    )
    ep2 = HistoricalEpisode(
        source_type="claude_code_trace", source_id="s1.jsonl",
        source_location="s1.jsonl#2", goal="run the test suite before every push",
        steps=[{"order": 0, "goal": "pytest -q", "properties": {"command": "pytest -q"}}],
        evidence_status=EVIDENCE_EXECUTED,
    )
    first = converge_episode(store, ep1)
    second = converge_episode(store, ep2)
    assert first["status"] == "captured"
    assert second["status"] == "merged"
    assert second["id"] == first["id"]                # one canonical row
    assert len(store.list_local_procedures()) == 1
    row = store.get_local_procedure(first["id"])
    assert {r["source_type"] for r in row["evidence_refs"]} == {
        "repository", "claude_code_trace"}            # provenance preserved
    assert row["source_episode_ids"] == ["ci.yml", "s1.jsonl"]


def test_discussion_only_episode_is_skipped_not_fabricated(store):
    ep = HistoricalEpisode(
        source_type="claude_chat", source_id="c1", source_location="x#1",
        goal="architecture brainstorm", steps=[],
        evidence_status="discussion",
    )
    assert converge_episode(store, ep)["status"] == "skipped"
    assert store.list_local_procedures() == []


def test_full_bootstrap_run_summary_is_honest(tmp_path, store):
    summary = run_bootstrap(
        store,
        repo_root=_make_repo(tmp_path),
        claude_export=_claude_export_file(tmp_path),
        chatgpt_export=_chatgpt_export_file(tmp_path),
        claude_traces_dir=_trace_dir(tmp_path),
        embed=None,   # lexical dedup path, on purpose
    )
    assert summary["episodes"] == summary["captured"] + summary["merged"] + summary["skipped"]
    assert summary["captured"] >= 5
    # The pure-discussion conversation never became an episode at all --
    # discussion is filtered before the store, not stored-then-flagged.
    assert not any(
        "brainstorm" in (r["name"] or "").lower()
        for r in store.list_local_procedures()
    )
    assert store.list_local_procedures()              # library now non-empty


# ---------------------------------------------------------------------------
# §13 cold-start proof: retrieval finds bootstrapped candidates BEFORE any
# new StealthLab work, on a wording-shifted query.
# ---------------------------------------------------------------------------


def test_cold_start_semantic_path_finds_bootstrapped_candidate(tmp_path, store):
    run_bootstrap(store, repo_root=_make_repo(tmp_path), embed=None)
    # Different words than the stored goal ("Deploy the service to staging").
    matches = store.search_local_procedures("push application to staging environment")
    assert matches, "a bootstrapped candidate must be retrievable before any new work"
    assert any(
        "staging" in (m["goal"] or "").lower() or "deploy" in (m["name"] or "").lower()
        for m in matches
    )


