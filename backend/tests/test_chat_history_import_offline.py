"""
Offline suite for `app.local_agent.chat_history_import`. Needs NO
DATABASE_URL and no live Postgres -- LocalProcedureStore is SQLite-backed
(one temp file per test, same convention as
test_local_learning_offline.py), and the parsers here read a plain local
JSON file. Nothing here is a mock: real NormalizedConversation objects,
real classify_message_evidence calls, a real sqlite-backed
LocalProcedureStore, real files written to tmp_path for the parser tests.

CENTRAL ASSERTION THIS FILE EXISTS TO PROVE: discussion-only and
suggestion-only conversations NEVER become a candidate procedure, no
matter how detailed or confident the assistant's described recommendation
is. Only a conversation carrying real attempted-or-better evidence
produces a candidate.
"""
from __future__ import annotations

import json

from app.local_agent.chat_history_import import (
    EVIDENCE_LEVELS,
    NormalizedConversation,
    NormalizedMessage,
    classify_message_evidence,
    extract_candidates_from_conversation,
    import_chat_history,
    parse_chatgpt_export,
    parse_claude_export,
)
from app.local_agent.local_store import LocalProcedureStore


def _store(tmp_path) -> LocalProcedureStore:
    return LocalProcedureStore(db_path=str(tmp_path / "local_procedures.db"))


def _conv(*messages) -> NormalizedConversation:
    return NormalizedConversation(
        source_type="claude", source_id="c", created_at=None, updated_at=None,
        messages=list(messages),
    )


# ---------------------------------------------------------------------------
# P0-3: an outcome must be attributable to a real command; a later success
# never reaches back past a recommendation, and an unrelated success makes
# no candidate. Same four cases the directive names, on the shipped path.
# ---------------------------------------------------------------------------


def test_p0_3_case1_recommendation_plus_unrelated_success_makes_no_candidate(tmp_path):
    conv = _conv(
        _msg("user", "how do I check the code?", index=0),
        _msg("assistant", "You could run the tests:\n```\npytest -q\n```", index=1),
        _msg("user", "unrelated: our nightly deploy pipeline tests passed today", index=2),
    )
    assert extract_candidates_from_conversation(conv) == []


def test_p0_3_case2_command_with_matching_outcome_yields_candidate(tmp_path):
    conv = _conv(
        _msg("user", "the client keeps timing out", index=0),
        _msg("assistant", "I added retries and ran it:\n```\npytest tests/test_client.py\n```", index=1),
        _msg("user", "that worked, the tests pass now", index=2),
    )
    cands = extract_candidates_from_conversation(conv)
    assert len(cands) == 1
    step_levels = [s["properties"]["evidence_level"] for s in cands[0]["steps"]]
    # the human's own confirmation, attributed to the real command, is what
    # grounds the candidate -- it is NOT clamped to "discussion".
    assert _rank(step_levels[-1]) >= _rank("completed")
    assert cands[0]["evidence_refs"][0]["evidence_level"] in ("attempted", "completed", "verified")


def test_p0_3_case3_discussion_only_yields_no_candidate(tmp_path):
    conv = _conv(
        _msg("user", "queue or cron for this?", index=0),
        _msg("assistant", "Depends on latency needs; both are fine.", index=1),
    )
    assert extract_candidates_from_conversation(conv) == []


def test_p0_3_case4_mixed_conversation_keeps_each_step_status(tmp_path):
    conv = _conv(
        _msg("assistant", "You could lint first:\n```\nruff check .\n```", index=0),   # recommend
        _msg("assistant", "I ran the formatter:\n```\nruff format .\n```", index=1),   # command, unhedged
        _msg("user", "that worked", index=2),                                          # outcome for #1
        _msg("assistant", "You should also run mypy:\n```\nmypy .\n```", index=3),     # recommend
    )
    cands = extract_candidates_from_conversation(conv)
    assert len(cands) == 1
    levels = [s["properties"]["evidence_level"] for s in cands[0]["steps"]]
    assert levels[0] == "suggested"                       # recommendation A, not upgraded
    assert _rank(levels[2]) >= _rank("completed")         # outcome attributed to the real run
    assert levels[3] == "suggested"                       # recommendation C, not upgraded


def _rank(level: str) -> int:
    return EVIDENCE_LEVELS.index(level)


def _msg(role: str, text: str, *, index: int, has_tool_use=False, has_tool_result=False) -> NormalizedMessage:
    return NormalizedMessage(
        role=role, text=text, timestamp=f"2026-01-01T00:00:{index:02d}Z",
        has_tool_use=has_tool_use, has_tool_result=has_tool_result, index=index,
    )


# ---------------------------------------------------------------------------
# Fixture 1: pure discussion -- no suggestion, no attempt, no evidence at all
# ---------------------------------------------------------------------------

PURE_DISCUSSION = NormalizedConversation(
    source_type="claude",
    source_id="conv-discussion-only",
    created_at="2026-01-01T00:00:00Z",
    updated_at="2026-01-01T00:05:00Z",
    messages=[
        _msg("user", "What's the difference between pandas concat and append?", index=0),
        _msg("assistant", "pandas.DataFrame.append() was removed in pandas 2.0. "
                           "It historically returned a new DataFrame by copying data, "
                           "which was slower than building a list and concatenating once.", index=1),
        _msg("user", "Interesting, thanks for explaining.", index=2),
    ],
)

# ---------------------------------------------------------------------------
# Fixture 2: discussion plus one weak "I would" suggestion -- still no
# real evidence of execution, must still produce zero candidates.
# ---------------------------------------------------------------------------

DISCUSSION_WITH_WEAK_SUGGESTION = NormalizedConversation(
    source_type="claude",
    source_id="conv-weak-suggestion",
    created_at="2026-01-01T01:00:00Z",
    updated_at="2026-01-01T01:05:00Z",
    messages=[
        _msg("user", "My ETL job is failing after upgrading pandas.", index=0),
        _msg("assistant", "That's likely the removal of DataFrame.append() in pandas 2.0. "
                           "I would run a search for df.append( across the codebase and "
                           "you could replace each call site with pd.concat([df, other]) instead.", index=1),
        _msg("user", "Ok, I'll take a look later, thanks.", index=2),
    ],
)

# ---------------------------------------------------------------------------
# Fixture 3: a real attempted-then-verified exchange -- structural
# tool_result evidence plus an explicit human confirmation of a check.
# ---------------------------------------------------------------------------

ATTEMPTED_THEN_VERIFIED = NormalizedConversation(
    source_type="claude",
    source_id="conv-real-fix",
    created_at="2026-01-01T02:00:00Z",
    updated_at="2026-01-01T02:10:00Z",
    messages=[
        _msg("user", "Fix AttributeError from pandas DataFrame.append() removal in app/etl/transform.py", index=0),
        _msg("assistant", "I'll replace the df.append(x) call with pd.concat([df, x]).", index=1,
             has_tool_use=True),
        _msg("tool", "Applied edit to app/etl/transform.py: replaced df.append(x) with pd.concat([df, x])",
             index=2, has_tool_result=True),
        _msg("user", "I ran the test suite and all tests passed now.", index=3),
    ],
)


# ---------------------------------------------------------------------------
# classify_message_evidence
# ---------------------------------------------------------------------------

def test_assistant_recommendation_language_never_classifies_above_suggested():
    msg = _msg("assistant", "I would run pd.concat instead of df.append here.", index=0)
    level = classify_message_evidence(msg)
    assert level in ("discussion", "suggested")
    assert level != "attempted"
    assert level != "completed"
    assert level != "verified"


def test_user_hedged_suggestion_is_suggested_not_executed():
    msg = _msg("user", "You could try running the migration script.", index=0)
    assert classify_message_evidence(msg) == "suggested"


def test_tool_result_message_is_real_attempted_evidence():
    msg = _msg("tool", "exit code 0, patch applied", index=0, has_tool_result=True)
    assert classify_message_evidence(msg) == "attempted"


def test_user_confirmation_of_tests_passing_is_verified():
    msg = _msg("user", "I re-ran the suite and all tests passed.", index=0)
    assert classify_message_evidence(msg) == "verified"


def test_user_that_worked_without_naming_a_check_is_completed_not_verified():
    msg = _msg("user", "That worked, thanks!", index=0)
    assert classify_message_evidence(msg) == "completed"


def test_plain_question_defaults_to_discussion():
    msg = _msg("user", "What does this error mean?", index=0)
    assert classify_message_evidence(msg) == "discussion"


def test_all_evidence_levels_are_the_documented_five():
    assert EVIDENCE_LEVELS == ("discussion", "suggested", "attempted", "completed", "verified")


# ---------------------------------------------------------------------------
# extract_candidates_from_conversation -- the three required fixtures
# ---------------------------------------------------------------------------

def test_pure_discussion_produces_zero_candidates():
    candidates = extract_candidates_from_conversation(PURE_DISCUSSION)
    assert candidates == []


def test_discussion_with_weak_i_would_suggestion_produces_zero_candidates():
    candidates = extract_candidates_from_conversation(DISCUSSION_WITH_WEAK_SUGGESTION)
    assert candidates == []


def test_attempted_then_verified_exchange_produces_one_candidate_with_evidence_trail():
    candidates = extract_candidates_from_conversation(ATTEMPTED_THEN_VERIFIED)
    assert len(candidates) == 1

    candidate = candidates[0]
    assert candidate["goal"].startswith("Fix AttributeError")
    assert candidate["provenance"] == "system_pending_review"
    assert candidate["scope_type"] == "session"
    assert candidate["scope_entity_id"] == "conv-real-fix"
    assert candidate["scope"]["privacy"] == "local-only"
    assert candidate["scope"]["source_type"] == "claude_chat"

    # Evidence trail: one step per real message, in order, each carrying
    # its OWN classified evidence level -- never collapsed to a single
    # conversation-level assertion.
    steps = candidate["steps"]
    assert len(steps) == len(ATTEMPTED_THEN_VERIFIED.messages)
    levels_seen = [s["properties"]["evidence_level"] for s in steps]
    assert "attempted" in levels_seen or "completed" in levels_seen or "verified" in levels_seen
    assert levels_seen[-1] == "verified"  # the final user confirmation

    evidence_ref = candidate["evidence_refs"][0]
    assert evidence_ref["kind"] == "claude_chat_history"
    assert evidence_ref["source_conversation_id"] == "conv-real-fix"
    assert evidence_ref["evidence_level"] == "verified"


def test_empty_conversation_produces_zero_candidates():
    empty = NormalizedConversation(
        source_type="claude", source_id="conv-empty",
        created_at=None, updated_at=None, messages=[],
    )
    assert extract_candidates_from_conversation(empty) == []


# ---------------------------------------------------------------------------
# import_chat_history -- end-to-end against a real sqlite-backed store,
# driven by the three fixtures above via a hand-rolled Claude export file.
# ---------------------------------------------------------------------------

def _claude_export_from_conversations(conversations: list[NormalizedConversation]) -> list[dict]:
    """Round-trips our own NormalizedConversation fixtures back into the
    real claude.ai export shape parse_claude_export expects, so
    import_chat_history is exercised through its real file-parsing path,
    not by monkeypatching the parser."""
    export = []
    for conv in conversations:
        chat_messages = []
        for msg in conv.messages:
            content = []
            if msg.has_tool_use:
                content.append({"type": "tool_use", "id": f"tu-{msg.index}", "name": "bash", "input": {}})
            if msg.has_tool_result:
                content.append({"type": "tool_result", "content": msg.text})
            else:
                content.append({"type": "text", "text": msg.text})
            chat_messages.append({
                "sender": "human" if msg.role == "user" else msg.role,
                "text": "" if msg.has_tool_result else msg.text,
                "content": content,
                "created_at": msg.timestamp,
            })
        export.append({
            "uuid": conv.source_id,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "chat_messages": chat_messages,
        })
    return export


def test_import_chat_history_end_to_end_matches_required_fixture_outcomes(tmp_path):
    store = _store(tmp_path)
    export_path = tmp_path / "conversations.json"
    export_path.write_text(
        json.dumps(_claude_export_from_conversations([
            PURE_DISCUSSION,
            DISCUSSION_WITH_WEAK_SUGGESTION,
            ATTEMPTED_THEN_VERIFIED,
        ])),
        encoding="utf-8",
    )

    summary = import_chat_history(str(export_path), "claude", store)

    assert summary == {
        "conversations_parsed": 3,
        "candidates_created": 1,
        "discussion_only_skipped": 2,
    }

    rows = store.list_local_procedures()
    assert len(rows) == 1
    assert rows[0]["provenance"] == "system_pending_review"
    assert rows[0]["verification_state"] == "candidate"
    assert rows[0]["scope_entity_id"] == "conv-real-fix"


def test_import_chat_history_rejects_unknown_source_type(tmp_path):
    store = _store(tmp_path)
    export_path = tmp_path / "conversations.json"
    export_path.write_text("[]", encoding="utf-8")
    try:
        import_chat_history(str(export_path), "gemini", store)
        assert False, "expected ValueError for unknown source_type"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# parse_claude_export / parse_chatgpt_export -- real file parsing against
# the documented field shapes.
# ---------------------------------------------------------------------------

def test_parse_claude_export_reads_real_documented_fields(tmp_path):
    export_path = tmp_path / "conversations.json"
    export_path.write_text(json.dumps([
        {
            "uuid": "abc-123",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:10:00Z",
            "chat_messages": [
                {"sender": "human", "text": "Hello", "content": [{"type": "text", "text": "Hello"}],
                 "created_at": "2026-01-01T00:00:00Z"},
                {"sender": "assistant", "text": "", "content": [
                    {"type": "tool_use", "id": "tu1", "name": "bash", "input": {}},
                ], "created_at": "2026-01-01T00:01:00Z"},
                {"sender": "assistant", "text": "", "content": [
                    {"type": "tool_result", "content": [{"type": "text", "text": "ok"}]},
                ], "created_at": "2026-01-01T00:02:00Z"},
            ],
        },
    ]), encoding="utf-8")

    conversations = parse_claude_export(str(export_path))
    assert len(conversations) == 1
    conv = conversations[0]
    assert conv.source_type == "claude"
    assert conv.source_id == "abc-123"
    assert len(conv.messages) == 3
    assert conv.messages[0].role == "user"
    assert conv.messages[0].text == "Hello"
    assert conv.messages[1].has_tool_use is True
    assert conv.messages[2].has_tool_result is True
    assert conv.messages[2].text == "ok"


def test_parse_chatgpt_export_reads_real_documented_fields(tmp_path):
    export_path = tmp_path / "conversations.json"
    export_path.write_text(json.dumps([
        {
            "conversation_id": "conv-xyz",
            "create_time": 1700000000.0,
            "update_time": 1700000100.0,
            "mapping": {
                "node-1": {
                    "id": "node-1",
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["Fix this error"]},
                        "create_time": 1700000000.0,
                    },
                },
                "node-2": {
                    "id": "node-2",
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["I would check the logs first."]},
                        "create_time": 1700000010.0,
                    },
                },
                "node-3": {
                    "id": "node-3",
                    "message": None,
                },
            },
        },
    ]), encoding="utf-8")

    conversations = parse_chatgpt_export(str(export_path))
    assert len(conversations) == 1
    conv = conversations[0]
    assert conv.source_type == "chatgpt"
    assert conv.source_id == "conv-xyz"
    assert len(conv.messages) == 2  # node-3 (no message) is skipped
    assert conv.messages[0].role == "user"
    assert conv.messages[0].text == "Fix this error"
    assert conv.messages[1].role == "assistant"
    assert conv.messages[1].has_tool_use is False


# ---------------------------------------------------------------------------
# §28: ChatGPT exports are a node TREE. An abandoned edited/regenerated
# sibling branch must NOT contaminate the evidence extracted from the
# branch that was actually continued (the `current_node` path).
# ---------------------------------------------------------------------------


def _chatgpt_tree_export(tmp_path, nodes, current_node, name="conversations.json"):
    """nodes: list of (node_id, parent_id|None, role|None, text|None).
    Emits per-node `parent` pointers and `children` arrays, matching the
    real ChatGPT `conversations.json` mapping shape."""
    mapping: dict = {}
    kids: dict = {}
    for i, (nid, parent, role, text) in enumerate(nodes):
        node = {"id": nid, "parent": parent, "children": []}
        node["message"] = None if role is None else {
            "author": {"role": role}, "id": nid,
            "create_time": 1700000000.0 + i,
            "content": {"parts": [text]},
        }
        mapping[nid] = node
        if parent is not None:
            kids.setdefault(parent, []).append(nid)
    for pid, children in kids.items():
        if pid in mapping:
            mapping[pid]["children"] = children
    conv: dict = {"conversation_id": "conv-tree", "create_time": 1700000000.0,
                  "update_time": 1700000200.0, "mapping": mapping}
    if current_node is not None:
        conv["current_node"] = current_node
    path = tmp_path / name
    path.write_text(json.dumps([conv]), encoding="utf-8")
    return str(path)


def test_s28_abandoned_fabricated_tool_sibling_makes_no_candidate(tmp_path):
    convs = parse_chatgpt_export(_chatgpt_tree_export(tmp_path, [
        ("u1", None, "user", "add caching to the client"),
        # the answer that was actually continued -- a plain recommendation:
        ("a_ok", "u1", "assistant", "Use functools.lru_cache on the hot path."),
        # ABANDONED regeneration with a FABRICATED tool result:
        ("a_bad", "u1", "assistant", "Running the suite now."),
        ("t_bad", "a_bad", "tool", "all tests passed, exit code 0"),
    ], current_node="a_ok"))
    assert len(convs) == 1
    conv = convs[0]
    assert [m.role for m in conv.messages] == ["user", "assistant"]   # active path only
    assert not any(m.has_tool_result for m in conv.messages)          # fabricated node dropped
    assert extract_candidates_from_conversation(conv) == []           # no verified evidence


def test_s28_abandoned_hedged_sibling_does_not_suppress_active_confirmation(tmp_path):
    convs = parse_chatgpt_export(_chatgpt_tree_export(tmp_path, [
        ("u1", None, "user", "fix the flaky auth test"),
        ("a1", "u1", "assistant", "I'll patch the retry config."),
        ("t1", "a1", "tool", "patch applied to tests/test_auth.py"),
        # ABANDONED hedged regeneration carrying its own command block:
        ("a_hedge", "u1", "assistant",
         "You could also just skip it:\n```\npytest -k 'not flaky'\n```"),
        ("u2", "t1", "user", "I ran the suite and all tests passed"),
    ], current_node="u2"))
    assert len(convs) == 1
    cands = extract_candidates_from_conversation(convs[0])
    assert len(cands) == 1
    # the genuine confirmation on the active branch is still attributed to
    # the real tool run -- not clamped by the abandoned hedge.
    assert cands[0]["evidence_refs"][0]["evidence_level"] == "verified"
    assert cands[0]["steps"][-1]["properties"]["evidence_level"] == "verified"


def test_s28_linear_conversation_output_identical_with_or_without_tree_metadata(tmp_path):
    linear_nodes = [
        ("n0", None, "user", "the client keeps timing out"),
        ("n1", "n0", "assistant", "I added retries and ran it."),
        ("n2", "n1", "tool", "pytest tests/test_client.py -> 4 passed"),
        ("n3", "n2", "user", "that worked, the tests pass now"),
    ]
    tree = parse_chatgpt_export(_chatgpt_tree_export(
        tmp_path, linear_nodes, current_node=None, name="tree.json"))
    # same conversation, mapping with NO parent/children/current_node
    flat_mapping = {
        nid: {"id": nid, "message": {
            "author": {"role": role}, "id": nid,
            "create_time": 1700000000.0 + i, "content": {"parts": [text]}}}
        for i, (nid, _p, role, text) in enumerate(linear_nodes)
    }
    flat_path = tmp_path / "flat.json"
    flat_path.write_text(json.dumps([{"conversation_id": "conv-tree",
                                      "mapping": flat_mapping}]), encoding="utf-8")
    flat = parse_chatgpt_export(str(flat_path))
    assert len(tree) == len(flat) == 1
    assert [(m.role, m.text, m.has_tool_result) for m in tree[0].messages] == \
           [(m.role, m.text, m.has_tool_result) for m in flat[0].messages]
    tc = extract_candidates_from_conversation(tree[0])
    fc = extract_candidates_from_conversation(flat[0])
    assert len(tc) == len(fc) == 1
    assert [s["properties"]["evidence_level"] for s in tc[0]["steps"]] == \
           [s["properties"]["evidence_level"] for s in fc[0]["steps"]]


def test_s28_mixed_active_branch_preserves_per_step_evidence_levels(tmp_path):
    convs = parse_chatgpt_export(_chatgpt_tree_export(tmp_path, [
        ("u1", None, "user", "clean up the module"),
        ("a1", "u1", "assistant", "You could lint first:\n```\nruff check .\n```"),
        # ABANDONED sibling fabricating a clean run:
        ("a_bad", "u1", "assistant", "you should run mypy strict everywhere and it all passed"),
        ("t1", "a1", "tool", "ruff format applied, 3 files reformatted"),
        ("a3", "t1", "assistant", "You should also run mypy:\n```\nmypy .\n```"),
    ], current_node="a3"))
    assert len(convs) == 1
    cands = extract_candidates_from_conversation(convs[0])
    assert len(cands) == 1
    levels = [s["properties"]["evidence_level"] for s in cands[0]["steps"]]
    assert levels == ["discussion", "suggested", "attempted", "suggested"]  # per-step preserved
    assert len(cands[0]["steps"]) == 4                                      # abandoned node absent


def test_s28_ambiguous_ancestry_no_current_node_is_conservative(tmp_path):
    path = _chatgpt_tree_export(tmp_path, [
        ("u1", None, "user", "does the build pass?"),
        ("a_bad", "u1", "assistant", "Checking now."),
        ("t_bad", "a_bad", "tool", "build succeeded, all tests passed"),
        ("a_ok", "u1", "assistant", "Try make build."),
    ], current_node=None)
    convs = parse_chatgpt_export(path)
    assert len(convs) == 1
    assert convs[0].messages == []                              # ambiguous -> no messages
    assert extract_candidates_from_conversation(convs[0]) == []  # no fabricated confirmation

    store = _store(tmp_path)
    summary = import_chat_history(path, "chatgpt", store)
    assert summary["candidates_created"] == 0
    assert summary["discussion_only_skipped"] == 1
    assert store.list_local_procedures() == []


def test_chatgpt_export_full_pipeline_discussion_only_skipped(tmp_path):
    """A ChatGPT export whose only conversation is pure suggestion
    language ("I would check the logs first") must also produce zero
    candidates -- the conservative bar applies identically across both
    source types."""
    store = _store(tmp_path)
    export_path = tmp_path / "conversations.json"
    export_path.write_text(json.dumps([
        {
            "conversation_id": "conv-xyz",
            "create_time": 1700000000.0,
            "update_time": 1700000100.0,
            "mapping": {
                "node-1": {
                    "id": "node-1",
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["Fix this error"]},
                        "create_time": 1700000000.0,
                    },
                },
                "node-2": {
                    "id": "node-2",
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["I would check the logs first."]},
                        "create_time": 1700000010.0,
                    },
                },
            },
        },
    ]), encoding="utf-8")

    summary = import_chat_history(str(export_path), "chatgpt", store)
    assert summary["conversations_parsed"] == 1
    assert summary["candidates_created"] == 0
    assert summary["discussion_only_skipped"] == 1
    assert store.list_local_procedures() == []
