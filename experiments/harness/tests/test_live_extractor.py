"""Offline proofs for live_extractor (OpenRouter-backed observation
extractor). Zero network: the client is a fake; timing comes from the
injectable sleep in OpenRouterClient when used."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import error_floor  # noqa: E402
import live_extractor as le  # noqa: E402
import openrouter_arms as ora  # noqa: E402


class FakeClient:
    """Replies with queued contents, one per call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, *, task_id="", arm=""):
        self.calls.append({"task_id": task_id, "arm": arm,
                           "n_messages": len(messages)})
        content = self.replies.pop(0) if self.replies else "{}"
        return {"content": content, "model": "fake/one",
                "tokens_in": 10, "tokens_out": 5}


class TestPrompt:
    def test_prompt_carries_full_taxonomy_and_none_contract(self):
        for t in error_floor.KNOWN_TYPES:
            assert t in le.SYSTEM_PROMPT
        assert "NO label" in le.SYSTEM_PROMPT
        assert "observations" in le.EXTRACT_SCHEMA

    def test_prompt_enforces_terse_gold_style_labels(self):
        # Board ruling (option a): tighten the semantic_label instruction
        # to require terse gold-style labels rather than verbose sentences
        # -> Jaccard>=0.5 against terse golds. Regression pin so a future
        # edit can't silently relax this back to free-form prose.
        assert "3-6 words" in le.SYSTEM_PROMPT
        assert "Do NOT quote file paths, commands, commit hashes" \
            in le.SYSTEM_PROMPT

    def test_user_message_embeds_event_verbatim(self):
        ev = {"tool_name": "Bash", "tool_input": {"command": "ls src"}}
        msgs = le.build_messages(ev)
        assert json.dumps(ev) in msgs[1]["content"]
        assert msgs[0]["role"] == "system"


class TestParseObservations:
    def test_plain_fenced_and_prose_wrapped_json(self):
        good = json.dumps({"observations": [
            {"observation_type": "test_run", "label": "Ran tests",
             "properties": {"command": "pytest -q"}}]})
        assert le.parse_observations(good)[0]
        assert le.parse_observations(f"```json\n{good}\n```")[0]
        assert le.parse_observations(f"Sure! {good} hope that helps")[0]

    def test_truncated_or_shapeless_replies_are_none(self):
        assert le.parse_observations('{"observations": [{"observation_'
                                     ) is None
        assert le.parse_observations('{"decisions": []}') is None
        assert le.parse_observations("no json here at all") is None

    def test_nondict_items_dropped_but_counted(self):
        body = json.dumps({"observations": [
            {"observation_type": "commit_made", "label": "x",
             "properties": {"command": "git commit -m x"}},
            "a string item",
            42,
        ]})
        preds, dropped = le.parse_observations(body)
        assert len(preds) == 1
        assert dropped == 2


class TestLiveExtractor:
    def test_happy_path_accumulates_usage(self):
        reply = json.dumps({"observations": [
            {"observation_type": "file_touched", "label": "Modified a.py",
             "properties": {"file_path": "a.py"}}]})
        fx = FakeClient([reply])
        ex = le.LiveExtractor(fx)
        preds, meta = asyncio.run(ex.extract_async({"tool_name": "Edit"},
                                                   "ef-1"))
        assert fx.calls == [{"task_id": "ef-1", "arm": "EX",
                             "n_messages": 2}]
        assert preds[0]["observation_type"] == "file_touched"
        assert meta["calls"] == 1 and meta["model"] == "fake/one"
        assert ex.usage_totals == {"tokens_in": 10, "tokens_out": 5,
                                   "calls": 1}

    def test_one_repair_round_trip_then_success(self):
        fx = FakeClient(["garbage", json.dumps({"observations": []})])
        ex = le.LiveExtractor(fx)
        preds, meta = asyncio.run(ex.extract_async({"tool_name": "Bash"},
                                                   "ef-2"))
        assert preds == []
        assert meta["calls"] == 2
        # repair round appends assistant echo + user repair prompt
        assert fx.calls[-1]["n_messages"] == 4

    def test_unparseable_after_repair_returns_none(self):
        fx = FakeClient(["junk", "still junk"])
        ex = le.LiveExtractor(fx)
        preds, meta = asyncio.run(ex.extract_async({}, "ef-3"))
        assert preds is None
        assert meta["calls"] == 2
        assert ex.usage_totals["calls"] == 2


class TestPromptVariants:
    """CLAUDE.md follow-up (2026-08-27, no spendable balance this wave):
    --prompt-variant wiring, prepared but not yet run live. See
    semantic_label_prompt_variants.py for the candidates themselves."""

    def test_build_messages_defaults_to_shipped_system_prompt(self):
        msgs = le.build_messages({"tool_name": "Bash"})
        assert msgs[0]["content"] == le.SYSTEM_PROMPT

    def test_build_messages_accepts_an_override(self):
        msgs = le.build_messages({"tool_name": "Bash"}, system_prompt="ALT")
        assert msgs[0]["content"] == "ALT"

    def test_live_extractor_uses_its_configured_system_prompt(self):
        class RecordingClient:
            def __init__(self, reply):
                self.reply = reply
                self.sent_messages = None

            async def chat(self, messages, *, task_id="", arm=""):
                self.sent_messages = messages
                return {"content": self.reply, "model": "fake/one",
                       "tokens_in": 1, "tokens_out": 1}

        client = RecordingClient(json.dumps({"observations": []}))
        ex = le.LiveExtractor(client, system_prompt="CUSTOM PROMPT")
        asyncio.run(ex.extract_async({"tool_name": "Bash"}, "ef-1"))
        assert client.sent_messages[0]["content"] == "CUSTOM PROMPT"

    def test_live_extractor_default_matches_module_system_prompt(self):
        ex = le.LiveExtractor(FakeClient([]))
        assert ex.system_prompt == le.SYSTEM_PROMPT

    def test_cli_default_variant_is_terse_v2(self):
        ap = le.build_arg_parser()
        args = ap.parse_args([])
        assert args.prompt_variant == "terse_v2"

    def test_cli_rejects_unknown_variant(self):
        ap = le.build_arg_parser()
        with pytest.raises(SystemExit):
            ap.parse_args(["--prompt-variant", "not-a-real-variant"])

    def test_cli_accepts_every_registered_variant(self):
        import semantic_label_prompt_variants as variants
        ap = le.build_arg_parser()
        for name in variants.PROMPT_VARIANTS:
            args = ap.parse_args(["--prompt-variant", name])
            assert args.prompt_variant == name


class TestLoadDone:
    def test_parsed_rows_done_unparseable_rows_retry_torn_skipped(self,
                                                                  tmp_path):
        p = tmp_path / "preds.jsonl"
        p.write_text(
            json.dumps({"excerpt_id": "ef-a", "observations": []}) + "\n"
            + json.dumps({"excerpt_id": "ef-b", "observations": [],
                          "unparseable": True}) + "\n"
            + "{torn\n"
            + json.dumps({"excerpt_id": "ef-c"}) + "\n",
            encoding="utf-8")
        assert le.load_done(p) == {"ef-a"}


class TestEndToEndOffline:
    def _args(self, tmp_path):
        class A:  # argparse namespace stand-in
            fixtures_dir = str(error_floor.DEFAULT_FIXTURES_DIR)
            out = str(tmp_path / "preds.jsonl")
            spend_log = str(tmp_path / "spend.jsonl")
            models = None
            auto_resume = False
        return A()

    def test_full_run_writes_row_per_excerpt_and_spend(self, tmp_path,
                                                       monkeypatch):
        reply = json.dumps({"observations": [
            {"observation_type": "semantic_label", "label": "auth changed",
             "properties": {}}]})
        args = self._args(tmp_path)
        monkeypatch.setattr(ora, "resolve_api_key", lambda **k: "k")
        import openrouter_arms

        def fake_transport(api_key):
            async def send(payload):
                content = payload["messages"][0]["content"]
                return 200, {
                    "choices": [{"message": {"content": reply}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
            return send

        monkeypatch.setattr(openrouter_arms, "httpx_transport",
                            fake_transport)
        rc = asyncio.run(le.async_main(args))
        assert rc == 0
        excerpts = error_floor.load_excerpts(
            Path(args.fixtures_dir))
        rows = [json.loads(l) for l in
                Path(args.out).read_text(encoding="utf-8").splitlines() if l]
        assert len(rows) == len(excerpts)
        assert all("observations" in r for r in rows)
        spend_rows = [json.loads(l) for l in
                      Path(args.spend_log).read_text(
                          encoding="utf-8").splitlines() if l]
        assert len(spend_rows) >= len(excerpts)
        assert all(r["arm"] == "EX" for r in spend_rows)

    def test_refuses_existing_file_without_auto_resume(self, tmp_path):
        args = self._args(tmp_path)
        Path(args.out).write_text('{"excerpt_id": "ef-x"}\n',
                                  encoding="utf-8")
        rc = asyncio.run(le.async_main(args))
        assert rc == 2

    def test_missing_key_refuses_before_any_call(self, tmp_path,
                                                 monkeypatch):
        args = self._args(tmp_path)
        monkeypatch.setattr(ora, "resolve_api_key", lambda **k: None)
        called = []

        def boom(api_key):
            called.append(api_key)
            raise AssertionError("transport built without key")

        monkeypatch.setattr(ora, "httpx_transport", boom)
        rc = asyncio.run(le.async_main(args))
        assert rc == 2

    def test_excerpt_ids_restricts_which_excerpts_get_called(
            self, tmp_path, monkeypatch):
        reply = json.dumps({"observations": []})
        args = self._args(tmp_path)
        args.excerpt_ids = "ef-sem-001,ef-sem-002"
        monkeypatch.setattr(ora, "resolve_api_key", lambda **k: "k")
        import openrouter_arms

        def fake_transport(api_key):
            async def send(payload):
                return 200, {
                    "choices": [{"message": {"content": reply}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return send

        monkeypatch.setattr(openrouter_arms, "httpx_transport",
                            fake_transport)
        rc = asyncio.run(le.async_main(args))
        assert rc == 0
        rows = [json.loads(l) for l in
                Path(args.out).read_text(encoding="utf-8").splitlines() if l]
        assert {r["excerpt_id"] for r in rows} == {"ef-sem-001", "ef-sem-002"}

    def test_unknown_excerpt_id_is_a_hard_error(self, tmp_path, monkeypatch):
        args = self._args(tmp_path)
        args.excerpt_ids = "ef-not-a-real-id"
        monkeypatch.setattr(ora, "resolve_api_key", lambda **k: "k")
        rc = asyncio.run(le.async_main(args))
        assert rc == 2
        assert not Path(args.out).exists() or \
            Path(args.out).read_text(encoding="utf-8") == ""
