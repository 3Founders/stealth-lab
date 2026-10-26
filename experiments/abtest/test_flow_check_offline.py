"""Offline tests of flow_check.py: the step logic against a stand-in for the hooks (a "product" that behaves as it should, and
broken ones), the sandbox, the transcript the real brief reader can parse, the preflight. No backend, no model, no keys.
Run from experiments/abtest:  python -m pytest test_flow_check_offline.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import flow_check as fc  # noqa: E402

REPO_ROOT = HERE.parents[1]
NODE = shutil.which("node")

MSG = ("Done by gpt-oss-120b in 41 s (StealthLab dispatched it before this session's model ran, $0.0001). It passed the tests "
       "written for this task in an isolated worktree and the change is applied: calc.py. To put it back as it was: kell undo."
       "\n\nIt changed add().")
FIXED = fc.CALC.replace("def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b")
STRICT = FIXED.replace("def add(a, b):\n    return a + b",
                       "def add(a, b):\n    if isinstance(a, str) or isinstance(b, str):\n        raise TypeError('no strings')\n    return a + b")
WIDE = STRICT.replace("def sub(a, b):\n    return a - b",
                      "def sub(a, b):\n    if isinstance(a, str):\n        raise TypeError('no strings')\n    return a - b")


class FakeHooks:
    """What the hooks should do, plus switches for the ways they can go wrong."""

    def __init__(self, repo, transcript, **flaws):
        self.repo, self.transcript, self.flaws = repo, transcript, flaws
        self.stack, self.prompts, self.delivered = [], [], False

    def _dispatch(self, new_calc):
        self.stack.append((self.repo / "calc.py").read_text(encoding="utf-8"))
        (self.repo / "calc.py").write_text(new_calc, encoding="utf-8")
        (self.repo / ".kell").mkdir(exist_ok=True)
        return fc.HookResult({"decision": "block", "reason": MSG}, self.flaws.get("secs", 41.0))

    def prompt(self, text):
        self.prompts.append(text)
        if text == fc.FIRST:
            return fc.HookResult({}, 0.2) if self.flaws.get("no_dispatch_first") else self._dispatch(FIXED)
        if text == fc.SECOND:
            if self.flaws.get("it_unresolved"):
                return self._dispatch(FIXED)
            return self._dispatch(WIDE if self.flaws.get("wide") else STRICT)
        if text.startswith("!opus"):
            return fc.HookResult({"decision": "block", "reason": MSG} if self.flaws.get("dispatch_opus") else {}, 0.1)
        if text == "thanks" and self.flaws.get("dispatch_thanks"):
            return fc.HookResult({"decision": "block", "reason": MSG}, 5.0)
        note = f"{fc.CONTINUITY} gpt-oss-120b changed calc.py to make it reject strings (StealthLab, check passed)."
        if not self.delivered or self.flaws.get("repeat_continuity"):
            self.delivered = True
            return fc.HookResult({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": note}}, 0.1)
        return fc.HookResult({}, 0.1)

    def undo(self):
        if self.flaws.get("undo_nothing"):
            return "nothing to undo"
        if self.flaws.get("undo_all"):
            (self.repo / "calc.py").write_text(fc.CALC, encoding="utf-8")
        elif self.flaws.get("undo_none_restored"):
            pass
        else:
            (self.repo / "calc.py").write_text(self.stack.pop(), encoding="utf-8")
        if self.flaws.get("clobber_own"):
            (self.repo / "notes.md").write_text("restored by undo", encoding="utf-8")
        return "undone u-1: restored calc.py"


def flow(tmp_path, **flaws):
    repo = fc.make_repo(tmp_path)
    transcript = tmp_path / "t" / "s.jsonl"
    hooks = FakeHooks(repo, transcript, **flaws)
    steps = fc.run_flow(hooks, repo, log=lambda m: None)
    return {s.name: s for s in steps}, steps, hooks


# ---------------------------------------------------------------- the steps

def test_a_product_that_behaves_passes_every_step(tmp_path):
    by, steps, hooks = flow(tmp_path)
    assert [s.name for s in steps] == list(fc.STEPS)
    assert all(s.status == "PASS" for s in steps), {s.name: s.notes for s in steps if s.status != "PASS"}
    assert fc.exit_code(steps) == 0
    assert hooks.prompts[:2] == [fc.FIRST, fc.SECOND] and hooks.prompts[-1].startswith("!opus")
    assert by["fix"].shown == MSG and by["it"].shown == MSG
    text = fc.report(steps)
    assert "7 pass, 0 warn, 0 fail" in text and "shown: Done by gpt-oss-120b in 41 s" in text


def test_the_transcript_carries_the_first_exchange_for_the_follow_up(tmp_path):
    _, _, hooks = flow(tmp_path)
    rows = [json.loads(x) for x in hooks.transcript.read_text(encoding="utf-8").splitlines()]
    assert [r["message"]["role"] for r in rows] == ["user", "assistant"] and rows[0]["message"]["content"] == fc.FIRST
    assert rows[1]["message"]["model"] == fc.MODEL and rows[1]["message"]["content"][0]["text"].startswith("Done by")


def test_scope_wider_than_the_request_is_a_warning_not_a_failure(tmp_path):
    by, steps, _ = flow(tmp_path, wide=True)
    assert by["it"].status == "WARN" and "sub changed too" in " ".join(by["it"].notes)
    assert fc.exit_code(steps) == 0 and "1 warn" in fc.report(steps)


def test_a_slow_dispatch_is_a_warning(tmp_path):
    by, _, _ = flow(tmp_path, secs=120.0)
    assert by["fix"].status == "WARN" and "120 s" in by["fix"].notes[0]


@pytest.mark.parametrize("flaw, step, why", [
    ({"no_dispatch_first": True}, "fix", "the first prompt was not dispatched"),
    ({"it_unresolved": True}, "it", "'it' was not resolved to add()"),
    ({"undo_nothing": True}, "undo", "undo did not say it undid anything"),
    ({"undo_all": True}, "undo", "did not restore calc.py to the state after the FIRST change"),
    ({"undo_none_restored": True}, "undo", "(it left the follow-up's change in place)"),
    ({"clobber_own": True}, "undo", "undo touched the developer's own file"),
    ({"dispatch_thanks": True}, "thanks", "'thanks' was dispatched"),
    ({"repeat_continuity": True}, "again", "the continuity note was repeated"),
    ({"dispatch_opus": True}, "opus", "'!opus ...' was dispatched"),
])
def test_each_way_the_product_can_break_fails_its_own_step(tmp_path, flaw, step, why):
    by, steps, _ = flow(tmp_path, **flaw)
    assert by[step].status == "FAIL" and why in " ".join(by[step].notes), by[step].notes
    assert fc.exit_code(steps) == 1 and step in fc.report(steps).splitlines()[-1]


def test_a_missing_first_dispatch_also_means_no_stealth_dir_and_failing_tests(tmp_path):
    by, _, _ = flow(tmp_path, no_dispatch_first=True)
    notes = " ".join(by["fix"].notes)
    assert "do not pass" in notes and "did not get its .kell/" in notes


# ---------------------------------------------------------------- the sandbox

def test_the_fresh_repo_is_a_real_git_repo_with_a_bug_no_stealth_dir_and_no_check(tmp_path):
    repo = fc.make_repo(tmp_path)
    assert not (repo / ".kell").exists() and not (repo / ".claude").exists()
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    assert not fc.tests_pass(repo), "the bug is there: add() subtracts"
    assert fc.function_source(repo / "calc.py", "mul") == "def mul(a, b):\n    return a * b" and fc.function_source(repo / "calc.py", "nope") is None
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    assert fc.raises_type_error_for_str(repo, "add") is False and fc.tests_pass(repo)
    (repo / "calc.py").write_text(STRICT, encoding="utf-8")
    assert fc.raises_type_error_for_str(repo, "add") is True


@pytest.mark.skipif(not NODE, reason="node is needed to run the product's own transcript reader")
def test_the_transcript_is_readable_by_the_products_own_brief_reader(tmp_path):
    t = tmp_path / "s.jsonl"
    fc.write_transcript(t, [("user", fc.FIRST), ("assistant", "Done by x in 3 s ..."), ("user", "and then")])
    brief = (REPO_ROOT / "packaging" / "npm" / "lib" / "brief.mjs").as_uri()
    code = f"import('{brief}').then(m => console.log(JSON.stringify(m.recentTurns({json.dumps(str(t))}))))"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60)
    assert json.loads(out.stdout) == [{"role": "user", "text": fc.FIRST}, {"role": "assistant", "text": "Done by x in 3 s ..."},
                                      {"role": "user", "text": "and then"}], out.stderr


def test_the_hook_environment_has_no_check_the_sandbox_home_and_the_keys_by_name(tmp_path):
    base = {"PATH": "p", "STEALTHLAB_DISPATCH_CHECK": "pytest", "STEALTHLAB_TOKEN": "other", "ANTHROPIC_BASE_URL": "http://x",
            "CLAUDE_CODE_OAUTH_TOKEN": "keep-me"}
    env = fc.hook_env(tmp_path / "home", "tok", {"GENERAL_COMPUTE_API_KEY": "k"}, base=base)
    assert "STEALTHLAB_DISPATCH_CHECK" not in env and env["STEALTHLAB_TOKEN"] == "tok" and "ANTHROPIC_BASE_URL" not in env
    assert env["STEALTHLAB_HOME"] == str(tmp_path / "home") and env["GENERAL_COMPUTE_API_KEY"] == "k" and env["PATH"] == "p"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me", "the session model's login still reaches the test-first call"
    cands = env["STEALTHLAB_HOOK_CANDIDATES"].split(",")
    assert "glm-5.3-flash|stealth" in cands and f"{fc.MODEL}|claude-code" in cands and env["STEALTHLAB_MCP_URL"].endswith("/mcp")


def test_keys_are_read_by_name_from_the_environment_first(monkeypatch):
    monkeypatch.setenv("GENERAL_COMPUTE_API_KEY", "from-env")
    monkeypatch.delenv("Z_AI_KEY", raising=False)
    got = fc.read_env_names(["GENERAL_COMPUTE_API_KEY", "Z_AI_KEY"])
    assert got["GENERAL_COMPUTE_API_KEY"] == "from-env" and set(got) <= {"GENERAL_COMPUTE_API_KEY", "Z_AI_KEY"}


# ---------------------------------------------------------------- running the real hook: the command and its answer

def test_the_real_hook_gets_a_claude_shaped_payload_and_its_last_json_line_is_the_answer(tmp_path, monkeypatch):
    seen = {}

    class Done:
        stdout = 'logging line\n{"decision": "block", "reason": "Done by m in 1 s ..."}\n'
        stderr = ""

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, kw=kw)
        return Done()

    monkeypatch.setattr(fc.subprocess, "run", fake_run)
    h = fc.Hooks(tmp_path / "repo", tmp_path / "home", {"X": "1"}, tmp_path / "t.jsonl", session_id="s-9")
    res = h.prompt("fix it")
    payload = json.loads(seen["kw"]["input"])
    assert seen["cmd"][0] == "node" and seen["cmd"][-1] == "hook-prompt" and seen["cmd"][1].endswith("kell.mjs")
    assert payload == {"session_id": "s-9", "transcript_path": str(tmp_path / "t.jsonl"), "cwd": str(tmp_path / "repo"),
                       "hook_event_name": "UserPromptSubmit", "prompt": "fix it"}
    assert seen["kw"]["env"] == {"X": "1"} and res.dispatched and res.reason.startswith("Done by m")


def test_parse_hook_output():
    assert fc.parse_hook_output('{"a": 1}\nnot json\n') == {"a": 1}
    assert fc.parse_hook_output("only logs\n") == {} and fc.parse_hook_output("") == {} and fc.parse_hook_output("{broken\n") == {}
    assert fc.HookResult({"hookSpecificOutput": {"additionalContext": "c"}}, 1).context == "c" and fc.HookResult({}, 1).context == ""


# ---------------------------------------------------------------- the command line

def test_preflight_lists_everything_that_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fc.shutil, "which", lambda name: None)
    monkeypatch.setattr(fc.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(fc, "read_env_names", lambda names: {})
    problems = fc.preflight(token_file=tmp_path / "missing.txt")
    text = " | ".join(problems)
    for part in ("node is not on PATH", "no test token", "test backend is not up", "GENERAL_COMPUTE_API_KEY is not set", "git is not on PATH"):
        assert part in text
    monkeypatch.setattr(fc.shutil, "which", lambda name: name)
    monkeypatch.setattr(fc.urllib.request, "urlopen", lambda *a, **k: None)
    monkeypatch.setattr(fc, "read_env_names", lambda names: {"GENERAL_COMPUTE_API_KEY": "k"})
    (tmp_path / "token.txt").write_text("t", encoding="utf-8")
    assert fc.preflight(token_file=tmp_path / "token.txt") == []


def test_dry_run_prints_the_steps_and_runs_nothing(capsys, monkeypatch):
    monkeypatch.setattr(fc, "preflight", lambda **kw: pytest.fail("a dry run must not look at the machine"))
    assert fc.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 fix" in out and "7 opus" in out and "needs: node, git" in out and "undo" in out


def test_without_the_services_it_exits_2_and_says_why(capsys, monkeypatch):
    monkeypatch.setattr(fc, "preflight", lambda **kw: ["the test backend is not up at http://x (OSError)"])
    assert fc.main([]) == 2
    assert "flow_check cannot start" in capsys.readouterr().out
