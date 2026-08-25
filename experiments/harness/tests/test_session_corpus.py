"""Session-corpus ingestion: discovery, genuine-prompt predicate, LOCATOR-ONLY
privacy guarantee, tolerance to torn lines, and runner-task conversion."""
import json

import session_corpus
from session_corpus import (ALLOWED_ROW_KEYS, assert_locator_only,
                            corpus_tasks, discover_sessions,
                            is_human_prompt, kind_of, manifest_rows)

SENTINEL = "ZZQ-SECRET-PROMPT-TEXT-DO-NOT-LEAK-ZZQ"


def make_project(root, slug, files):
    base = root / slug
    (base / "subagents").mkdir(parents=True)
    for name, lines in files.items():
        path = base / name if not name.startswith("subagents/") \
            else base / "subagents" / name.split("/", 1)[1]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def line_user_text(text, **meta):
    rec = {"type": "user",
           "message": {"content": [{"type": "text", "text": text}]}}
    rec.update(meta)
    return json.dumps(rec)


def assistant_tool_line(tool="Bash"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": {"command": "ls"}}]}})


class TestDiscoveryAndPredicate:
    def test_discovers_main_and_subagent_transcripts(self, tmp_path):
        make_project(tmp_path, "C--Users-x-proj", {
            "s1.jsonl": [line_user_text("hello")],
            "subagents/agent-abc.jsonl": [line_user_text("sub prompt")],
        })
        found = discover_sessions(tmp_path)
        assert [p.name for p in found] == ["s1.jsonl", "agent-abc.jsonl"]
        assert [kind_of(p) for p in found] == ["main", "subagent"]

    def test_missing_root_is_empty(self, tmp_path):
        assert discover_sessions(tmp_path / "nope") == []

    def test_genuine_prompt_predicate(self):
        assert is_human_prompt(json.loads(line_user_text("fix the bug")))
        # auto-continuations are not prompts
        assert not is_human_prompt(json.loads(
            line_user_text("<task-notification>done</task-notification>")))
        assert not is_human_prompt(json.loads(
            line_user_text("[Request interrupted by user]")))
        # meta / compact / sidechain lines are not prompts
        assert not is_human_prompt(
            json.loads(line_user_text("x", isMeta=True)))
        assert not is_human_prompt(
            json.loads(line_user_text("x", isCompactSummary=True)))
        assert not is_human_prompt(
            json.loads(line_user_text("x", isSidechain=True)))
        # tool_result-first user lines are tool echoes, not prompts
        assert not is_human_prompt({"type": "user", "message": {
            "content": [{"type": "tool_result", "content": "ok"}]}})
        # plain-string content still counts
        assert is_human_prompt({"type": "user",
                                "message": {"content": "plain text"}})
        # assistant lines never count
        assert not is_human_prompt(json.loads(assistant_tool_line()))


class TestManifestPrivacy:
    def test_rows_are_locator_only_and_leak_no_text(self, tmp_path):
        make_project(tmp_path, "C--Users-x-proj", {
            "s1.jsonl": [
                json.dumps({"type": "ai-title", "aiTitle": "t"}),
                line_user_text(f"please {SENTINEL} and organize"),
                "",
                "{torn json",
                assistant_tool_line(),
                line_user_text("second prompt", timestamp="2026-08-25T00:00:00Z"),
                line_user_text("<task-notification>wake</task-notification>"),
            ],
        })
        sessions = discover_sessions(tmp_path)
        rows = [r for p in sessions for r in manifest_rows(p)]
        assert len(rows) == 2  # torn/assistant/notification/title lines skipped
        r = rows[0]
        assert set(r) <= ALLOWED_ROW_KEYS
        blob = json.dumps(rows)
        assert SENTINEL not in blob
        assert "organize" not in blob
        assert r["prompt_chars"] == len(f"please {SENTINEL} and organize")
        assert r["line_no"] == 2 and r["kind"] == "main"
        assert rows[1]["line_no"] == 6
        assert rows[1]["ts"] == "2026-08-25T00:00:00Z"

    def test_assert_locator_only_rejects_unexpected_keys(self):
        try:
            assert_locator_only({"task_id": "t", "text": "leak"})
        except AssertionError as exc:
            assert "text" in str(exc)
        else:
            raise AssertionError("leaky row must fail loudly")

    def test_corpus_tasks_shape(self, tmp_path):
        p = tmp_path / "m.jsonl"
        p.write_text(json.dumps({
            "task_id": "cc-ab-L1", "kind": "main", "session_id": "ab",
            "source_path": "X:\\s.jsonl", "line_no": 1, "prompt_chars": 3,
            "ts": None, "unseen": True, "dry_run": True,
            "archetype": "real_session_prompt"}), encoding="utf-8")
        tasks = corpus_tasks(p)
        assert tasks[0]["domain"] == "real_session"
        assert tasks[0]["unseen"] and tasks[0]["dry_run"]
        assert tasks[0]["rag"] == "none"
        assert "solo_outcome" not in tasks[0]

    def test_write_manifest_roundtrip_counts(self, tmp_path):
        make_project(tmp_path, "slug", {
            "s.jsonl": [line_user_text("a"), line_user_text("b")]})
        out = tmp_path / "corpus" / "cc_manifest.jsonl"
        n = session_corpus.write_manifest(discover_sessions(tmp_path), out)
        assert n == 2
        assert len(out.read_text(encoding="utf-8").splitlines()) == 2


class TestRealCorpusThroughArms:
    def test_dry_run_task_needs_no_solo_outcome(self):
        from scripted_arms import SoloFrontierAgent
        ep = SoloFrontierAgent().run({
            "task_id": "cc-x-L1", "domain": "real_session",
            "unseen": True, "rag": "none"})
        assert ep["resolved"] is False and ep["valid"] is True
