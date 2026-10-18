"""Offline tests of the multi-turn driver (multiturn.py, runner.py --turns).

No model, no server, no keys, no network: the opener / simulated-user model is a stand-in (`FakeLlm`), `claude` is a
stand-in (`runner.run_claude` replaced), `install_c` a marker. Run from experiments/abtest:
    python -m pytest test_multiturn_offline.py -q
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import multiturn  # noqa: E402
import runner  # noqa: E402
import series_toy_task as toy  # noqa: E402


@pytest.fixture(autouse=True)
def _no_minimum_followups(monkeypatch):
    """These tests pin the plain rule (a "done" ends the session); the default minimum of one follow-up is tested below."""
    monkeypatch.setattr(multiturn, "MIN_FOLLOWUPS", 0)


OPENER = "the slug thing drops digits, can you look"
GOOD = {
    "slugify": '''def slugify(text):
    """Lower-case slug."""
    import re
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
''',
}
SECRETS = {"token": "t", "gc": "k", "zai": "z"}


class FakeLlm:
    """The opener model and the simulated user, in one: told apart by their system prompts."""

    def __init__(self, opener=OPENER, followups=("it also breaks on empty input", "and with accents")):
        self.opener, self.followups, self.calls = opener, list(followups), []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if system == multiturn.OPENER_SYSTEM:
            return "```json\n" + json.dumps({"opener": self.opener, "must_keep": "digits are dropped",
                                              "followups": self.followups}) + "\n```"
        last = user.split("The assistant's last message:\n", 1)[1]
        if last.rstrip().endswith("?"):
            return json.dumps({"action": "answer", "text": "It is the slugify function."})
        if "fixed" in last.lower() or "done by" in last.lower():
            return json.dumps({"action": "done", "text": ""})
        return json.dumps({"action": "followup", "text": "THE MODEL'S OWN WORDS ARE NEVER USED"})

    def of(self, system):
        return [c for c in self.calls if c[0] == system]


# ---------------------------------------------------------------- opener

def test_check_opener_rubric():
    issue = "Feature request: textutils.py needs slugify(text): lower-case, runs of non-alphanumerics become one '-'."
    assert multiturn.check_opener(OPENER, issue) == []
    assert any("words" in p for p in multiturn.check_opener("fix it", issue))
    assert any("words" in p for p in multiturn.check_opener("word " * 70, issue))
    assert any("code block" in p for p in multiturn.check_opener("see ```x``` for the slug problem please", issue))
    assert any("reproduce" in p for p in multiturn.check_opener("slug is wrong, steps to reproduce below", issue))
    pasted = "textutils.py needs slugify(text): lower-case, runs of non-alphanumerics become one"
    assert any("copies" in p for p in multiturn.check_opener(pasted, issue))
    assert multiturn.max_shared_run("a b c d", "x b c d y") == 3


def test_make_opener_is_written_once_and_cached_per_task(tmp_path):
    llm = FakeLlm()
    first = multiturn.make_opener("toy-1-slugify", "issue text here", llm, tmp_path)
    again = multiturn.make_opener("toy-1-slugify", "issue text here", llm, tmp_path)
    assert first == again and len(llm.of(multiturn.OPENER_SYSTEM)) == 1, "the second call reads the cache"
    assert first["opener"] == OPENER and first["followups"] == ["it also breaks on empty input", "and with accents"]
    assert first["checks"]["ok"] and first["rubric"]["drop"] == "the reproduction steps" and first["must_keep"]
    assert (tmp_path / "toy-1-slugify.txt").read_text(encoding="utf-8").strip() == OPENER
    assert "Variation seed" in llm.of(multiturn.OPENER_SYSTEM)[0][1]
    assert len(first["followups"]) <= multiturn.MAX_TURNS - 1


def test_make_opener_retries_a_bad_opener_and_says_why(tmp_path):
    issue = "textutils.py needs slugify(text): lower-case, runs of non-alphanumerics become one dash and trims the ends"
    replies = [json.dumps({"opener": issue, "followups": []}), json.dumps({"opener": OPENER, "followups": ["x"]})]
    seen = []

    def llm(system, user):
        seen.append(user)
        return replies[len(seen) - 1]

    op = multiturn.make_opener("t", issue, llm, tmp_path)
    assert op["opener"] == OPENER and op["checks"]["ok"] and len(seen) == 2
    assert "rejected" in seen[1] and "copies" in seen[1]


def test_make_opener_keeps_one_that_keeps_failing_but_marks_it(tmp_path):
    llm = lambda s, u: json.dumps({"opener": "fix it", "followups": []})  # noqa: E731
    op = multiturn.make_opener("t", "an issue", llm, tmp_path)
    assert op["opener"] == "fix it" and op["checks"]["ok"] is False and op["checks"]["problems"]
    with pytest.raises(RuntimeError, match="no opener"):
        multiturn.make_opener("u", "an issue", lambda s, u: "not json at all", tmp_path)


# ---------------------------------------------------------------- simulated user

def sim(tmp_path, llm=None, followups=("first extra", "second extra")):
    return multiturn.SimUser("t-1", "C", "the whole issue", list(followups), llm or FakeLlm(), tmp_path)


def test_sim_user_answers_a_question_ends_on_done_and_scripts_the_followups(tmp_path):
    u = sim(tmp_path)
    a = u.reply(1, "Which function do you mean?", 0)
    assert a["action"] == "answer" and a["text"] == "It is the slugify function." and not a["cached"]
    d = u.reply(2, "Fixed: added the function.", 0)
    assert d["action"] == "done" and d["text"] == "thanks"
    f = u.reply(3, "I looked at the file.", 0)
    assert f["action"] == "followup" and f["text"] == "first extra", "the follow-up is the script's, not the model's"
    assert u.reply(4, "I looked again.", 1)["text"] == "second extra"
    over = u.reply(5, "I looked a third time.", 2)           # the script is used up: the user is done
    assert over["action"] == "done" and over["text"] == "thanks"


def test_sim_user_replies_are_cached_per_task_arm_turn_and_only_for_the_same_assistant_text(tmp_path):
    llm = FakeLlm()
    u = sim(tmp_path, llm)
    first = u.reply(1, "Which function?", 0)
    assert (tmp_path / "t-1.C.1.json").exists() and len(llm.of(multiturn.SIM_SYSTEM)) == 1
    again = u.reply(1, "Which function?", 0)
    assert again["cached"] and again["text"] == first["text"] and len(llm.of(multiturn.SIM_SYSTEM)) == 1
    other = u.reply(1, "All done, fixed.", 0)                 # same turn, the assistant said something else
    assert other["regenerated"] and other["action"] == "done" and len(llm.of(multiturn.SIM_SYSTEM)) == 2
    assert multiturn.SimUser("t-1", "A", "x", [], llm, tmp_path).reply(1, "Which function?", 0)["cached"] is False, "per arm"


def test_sim_user_falls_back_when_the_model_reply_is_unreadable(tmp_path):
    u = sim(tmp_path, lambda s, user: "I am not JSON")
    q = u.reply(1, "Could you tell me more?", 0)
    assert q["action"] == "answer" and q["parse_failed"] and q["text"], "a question still gets an answer"
    assert u.reply(2, "All finished.", 0)["action"] == "done"


# ---------------------------------------------------------------- the session loop

def scripted(outs):
    calls = []

    def run_turn(prompt, resume, n):
        calls.append((prompt, resume, n))
        return outs[n - 1]
    return run_turn, calls


def turn(text, cost=0.1, sid="S1", status="done", secs=1000):
    return {"status": status, "is_error": False, "claude_cost_usd": cost, "turns": 2, "duration_ms": secs, "models": ["m"],
            "model_usage": {"m": {"costUSD": cost, "inputTokens": 10}}, "final": text[:400], "text": text, "session_id": sid}


def session(tmp_path, outs, max_turns=4, **kw):
    run_turn, calls = scripted(outs)
    out = multiturn.run_session(task_id="t", arm="A", issue="issue", rule=" RULE", max_turns=max_turns, llm=kw.get("llm", FakeLlm()),
                                openers_dir=tmp_path / "o", simuser_dir=tmp_path / "s", run_turn=run_turn,
                                observe=kw.get("observe"))
    return out, calls


def test_a_question_then_an_answer_then_done(tmp_path):
    out, calls = session(tmp_path, [turn("Which function do you mean?", 0.1), turn("Fixed: added it.", 0.2)])
    assert [c[2] for c in calls] == [1, 2]
    assert calls[0] == (OPENER + " RULE", None, 1), "the rule goes on the first turn only, nothing is resumed yet"
    assert calls[1] == ("It is the slugify function.", "S1", 2), "later turns resume the session and carry no rule"
    assert out["status"] == "done" and out["user_turns"] == 2 and out["questions_asked"] == 1 and out["ended_by"] == "thanks"
    assert out["claude_cost_usd"] == pytest.approx(0.3) and out["turns"] == 4 and out["duration_ms"] == 2000
    assert out["model_usage"] == {"m": {"costUSD": pytest.approx(0.3), "inputTokens": 20}} and out["final"].startswith("Fixed")
    assert [t["user_kind"] for t in out["turn_log"]] == ["opener", "answer"]
    assert out["turn_log"][0]["sim"]["action"] == "answer" and out["turn_log"][1]["sim"]["closing"] == "thanks"
    assert out["opener"] == OPENER and out["opener_ok"]


def test_followups_continue_until_done_or_the_turn_limit(tmp_path):
    outs = [turn("Looked at it.", sid="S"), turn("Still looking.", sid="S"), turn("Looking more.", sid="S"), turn("And more.", sid="S")]
    out, calls = session(tmp_path, outs, max_turns=3)
    assert [c[0] for c in calls][1:] == ["it also breaks on empty input", "and with accents"]
    assert out["user_turns"] == 3 and out["ended_by"] == "turn_limit" and out["questions_asked"] == 0
    assert [t["user_kind"] for t in out["turn_log"]] == ["opener", "followup", "followup"]
    one, calls1 = session(tmp_path / "x", [turn("Looked at it.")], max_turns=1)
    assert one["user_turns"] == 1 and len(calls1) == 1 and one["ended_by"] == "turn_limit"
    with pytest.raises(ValueError):
        session(tmp_path / "y", [], max_turns=5)


def test_a_turn_that_does_not_finish_ends_the_session_and_decides_the_status(tmp_path):
    out, calls = session(tmp_path, [turn("Which function?"), {"status": "rate_limited", "error": "usage limit"}])
    assert out["status"] == "rate_limited" and out["error"] == "usage limit" and out["ended_by"] == "rate_limited"
    assert out["user_turns"] == 2 and "claude_cost_usd" not in out
    nosid, _ = session(tmp_path / "n", [{**turn("Which function?"), "session_id": None}])
    assert nosid["status"] == "infra" and "session_id" in nosid["error"], "a turn that cannot be resumed is infrastructure, not a score"


def test_what_the_user_reads_is_the_shown_message_when_a_cheap_model_did_the_turn(tmp_path):
    llm = FakeLlm()
    shown = "Done by gpt-oss-120b in 41 s (StealthLab dispatched it before this session's model ran)."
    out, _ = session(tmp_path, [turn("")], max_turns=2, llm=llm, observe=lambda n, a, b, o: {"shown": shown})
    sim_prompt = llm.of(multiturn.SIM_SYSTEM)[0][1]
    assert shown in sim_prompt and out["ended_by"] == "thanks"


# ---------------------------------------------------------------- runner: one task as a conversation, per arm

@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOCAL", tmp_path / ".local")
    monkeypatch.setattr(runner, "install_c", lambda ws: (ws / ".claude").mkdir(exist_ok=True) or (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8"))
    llm = FakeLlm()
    monkeypatch.setattr(runner, "sim_llm", lambda secrets: llm)
    seen = []

    def fake_claude(ws, prompt, env, extra, log_path, resume=None):
        arm = ws.parent.name
        n = 2 if resume else 1
        seen.append({"arm": arm, "prompt": prompt, "resume": resume, "log": log_path.name})
        if n == 1:                                           # the vague opener: the assistant asks which function
            text = "Which function do you mean?"
            sid = f"sess-{arm}"
        else:
            text = "Fixed: added slugify."
            sid = resume
            with (ws / "textutils.py").open("a", encoding="utf-8") as f:
                f.write("\n" + GOOD["slugify"])
        log = {"type": "system", "subtype": "hook_response", "output": json.dumps(
            {"decision": "block", "reason": "Done by gpt-oss-120b in 41 s (StealthLab dispatched it before this session's model ran, $0.0001). It passed x."})} \
            if (arm == "C" and n == 2) else {"type": "result"}
        log_path.write_text(json.dumps(log) + "\n", encoding="utf-8")
        return {"status": "done", "is_error": False, "claude_cost_usd": 0.1, "turns": 2, "duration_ms": 1000, "models": [],
                "model_usage": {}, "final": text, "text": text, "session_id": sid}

    monkeypatch.setattr(runner, "run_claude", fake_claude)
    return llm, seen


def task1():
    return runner.load_series("series_toy_task")[0]


def test_a_task_as_a_conversation_in_each_arm(sandbox):
    llm, seen = sandbox
    recs = {arm: runner.run_one(task1(), arm, "mt", SECRETS, turns=3) for arm in ("A", "C", "C0")}
    for arm, r in recs.items():
        assert r["status"] == "done" and r["user_turns"] == 2 and r["questions_asked"] == 1 and r["ended_by"] == "thanks"
        assert r["claude_cost_usd"] == pytest.approx(0.2) and r["turns"] == 4 and "text" not in r and "session_id" not in r
        assert r["resolved"] is True, (arm, r["grade"])
        assert r["opener"] == OPENER, "the same opener in every arm"
        log = r["turn_log"]
        assert [t["user_kind"] for t in log] == ["opener", "answer"] and all(t["seconds"] >= 0 for t in log)
        assert log[0]["diff"] == "" and "+def slugify" in log[1]["diff"], "the diff is what ONE turn changed"
        assert r["peeked"] == []
    assert len(llm.of(multiturn.OPENER_SYSTEM)) == 1, "the opener was written once for three arms"
    by_arm = {a: [s for s in seen if s["arm"] == a] for a in ("A", "C", "C0")}
    for arm, s in by_arm.items():
        assert s[0]["prompt"].startswith(OPENER) and s[0]["prompt"].endswith(runner.RULE) and s[0]["resume"] is None
        assert s[1]["resume"] == f"sess-{arm}" and s[1]["prompt"] == "It is the slugify function."
        assert s[1]["log"].endswith(".t2.jsonl") and s[0]["log"] == f"toy-1-slugify.{arm}.jsonl"
    # only the C arms record what a cheap model did and showed
    assert "dispatched" not in recs["A"]["turn_log"][1]
    c2 = recs["C"]["turn_log"][1]
    assert c2["dispatched"] and c2["dispatched_model"] == "gpt-oss-120b" and c2["shown"].startswith("Done by gpt-oss-120b in 41 s")
    assert recs["C"]["turn_log"][0]["dispatched"] is False and recs["C"]["turn_log"][0]["shown"] is None
    assert recs["C0"]["turn_log"][1]["dispatched"] is False, "C's message is not read into C0's record (C0 has its own logs)"


def test_a_conversation_halts_on_a_usage_limit_and_is_not_scored(sandbox, monkeypatch):
    llm, seen = sandbox
    real = runner.run_claude

    def limited(ws, prompt, env, extra, log_path, resume=None):
        if resume:
            return {"status": "rate_limited", "error": "usage limit"}
        return real(ws, prompt, env, extra, log_path, resume)

    monkeypatch.setattr(runner, "run_claude", limited)
    r = runner.run_one(task1(), "A", "mt", SECRETS, turns=3)
    assert r["status"] == "rate_limited" and "resolved" not in r and r["user_turns"] == 2 and r["ended_by"] == "rate_limited"


def test_one_turn_is_the_old_single_prompt_and_its_record_is_unchanged(sandbox):
    llm, seen = sandbox
    r = runner.run_one(task1(), "A", "single", SECRETS)               # turns defaults to 1
    assert seen[0]["prompt"].startswith("Add slugify(text)") and seen[0]["resume"] is None
    assert "turn_log" not in r and "user_turns" not in r and "text" not in r and "session_id" not in r
    assert llm.calls == [], "no opener model, no simulated user"


# ---------------------------------------------------------------- run_claude: the command and the result

class FakePopen:
    last: list = []

    def __init__(self, cmd, **kw):
        FakePopen.last = cmd
        msgs = [{"type": "system", "subtype": "init"},
                {"type": "result", "is_error": False, "result": "x" * 1000, "session_id": "abc-123", "total_cost_usd": 0.5,
                 "num_turns": 3, "duration_ms": 10, "modelUsage": {"m": {}}}]
        self.stdout = io.StringIO("".join(json.dumps(m) + "\n" for m in msgs))

    def wait(self):
        return 0

    def kill(self):
        pass


def test_run_claude_resumes_a_session_and_returns_its_id_and_full_text(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)
    out = runner.run_claude(tmp_path, "a reply", {}, [], tmp_path / "log.jsonl", resume="abc-123")
    i = FakePopen.last.index("--resume")
    assert FakePopen.last[i + 1] == "abc-123" and FakePopen.last[1:3] == ["-p", "a reply"]
    assert out["session_id"] == "abc-123" and len(out["text"]) == 1000 and len(out["final"]) == 400
    runner.run_claude(tmp_path, "first", {}, [], tmp_path / "log.jsonl")
    assert "--resume" not in FakePopen.last
    with pytest.raises(ValueError, match="one line"):
        runner.run_claude(tmp_path, "two\nlines", {}, [], tmp_path / "log.jsonl", resume="abc-123")


# ---------------------------------------------------------------- what a turn changed; what the user was shown

def git(ws, *args):
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, check=True).stdout


def test_snapshots_diff_one_turn_and_leave_the_workspace_alone(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t.invalid")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    s0 = multiturn.tree_snapshot(tmp_path)
    (tmp_path / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "new.py").write_text("new\n", encoding="utf-8")
    (tmp_path / ".stealth").mkdir()
    (tmp_path / ".stealth" / "library.md").write_text("learned\n", encoding="utf-8")
    s1 = multiturn.tree_snapshot(tmp_path)
    d = multiturn.tree_diff(tmp_path, s0, s1)
    assert "+two" in d and "+++ b/new.py" in d and ".stealth" not in d
    assert multiturn.tree_diff(tmp_path, s1, s1) == "" and multiturn.tree_diff(tmp_path, None, s1) == ""
    assert git(tmp_path, "diff", "--cached") == "" and git(tmp_path, "status", "--porcelain").count("\n") == 3, \
        "the workspace's own index is untouched (a.py, new.py, .stealth/ still uncommitted)"
    assert not list((tmp_path / ".git").glob("abtest-snap-*")), "the private index is removed"
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert "+three" in multiturn.tree_diff(tmp_path, s1, multiturn.tree_snapshot(tmp_path)) and "+two" not in multiturn.tree_diff(
        tmp_path, s1, multiturn.tree_snapshot(tmp_path)), "turn two's diff has only turn two's lines"
    assert multiturn.tree_snapshot(tmp_path / "not-a-repo") is None


def test_shown_message_is_found_in_the_stream_json_log(tmp_path):
    reason = "Done by minimax-m2-7 in 51 s (StealthLab dispatched it before this session's model ran, $0.0011). It passed the check."
    log = tmp_path / "t.jsonl"
    log.write_text("\n".join(json.dumps(x) for x in [
        {"type": "system", "subtype": "init"},
        {"type": "system", "subtype": "hook_response", "stdout": json.dumps({"decision": "block", "reason": reason + "\n\nIt fixed add()."})},
        {"type": "result", "result": ""}]) + "\nnot json\n", encoding="utf-8")
    msg = multiturn.shown_message(log)
    assert msg.startswith("Done by minimax-m2-7 in 51 s") and msg.endswith("It fixed add().")
    assert multiturn.DONE_BY.search(msg).groups() == ("minimax-m2-7", "51")
    plain = tmp_path / "p.jsonl"
    plain.write_text(json.dumps({"type": "result", "result": "I changed calc.py."}) + "\n", encoding="utf-8")
    assert multiturn.shown_message(plain) is None and multiturn.shown_message(tmp_path / "missing.jsonl") is None


# ---------------------------------------------------------------- the model behind the opener (zai_llm)

class Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_zai_llm_posts_to_the_profile_endpoint_retries_and_never_puts_the_key_in_an_error(monkeypatch):
    key = "SECRET-KEY-VALUE"
    sent, answers = [], [multiturn.urllib.error.HTTPError("u", 429, "slow", {}, None),
                         Resp({"choices": [{"message": {"content": " hello "}}]})]

    def urlopen(req, timeout):
        sent.append((req.full_url, req.get_header("Authorization"), json.loads(req.data)))
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(multiturn.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(multiturn.time, "sleep", lambda s: None)
    assert multiturn.zai_llm(key)("sys", "usr") == "hello"
    url, auth, body = sent[1]
    assert url == "https://api.z.ai/api/paas/v4/chat/completions" and auth == f"Bearer {key}"
    assert body["model"] == "glm-5.3-flash" and body["temperature"] == 0 and body["reasoning_effort"] == "low"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    monkeypatch.setattr(multiturn.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(
        multiturn.urllib.error.HTTPError("u", 401, "no", {}, None)))
    with pytest.raises(RuntimeError) as e:
        multiturn.zai_llm(key)("s", "u")
    assert key not in str(e.value) and "401" in str(e.value)


def test_parse_json_takes_the_object_out_of_a_fenced_or_chatty_reply():
    assert multiturn.parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert multiturn.parse_json("no object") is None and multiturn.parse_json("{broken") is None and multiturn.parse_json("[1]") is None


# ---------------------------------------------------------------- the flag

def test_turns_flag_dry_run_and_checks(sandbox, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runner.py", "--run", "d", "--series", "series_toy_task", "--arms", "A,C,C0", "--turns", "3", "--dry-run"])
    assert runner.main() == 0
    out = capsys.readouterr().out
    assert "up to 3 user turns per task" in out and "same for every arm" in out
    monkeypatch.setattr(sys, "argv", ["runner.py", "--run", "d", "--series", "series_toy_task", "--dry-run"])
    runner.main()
    assert "conversations" not in capsys.readouterr().out, "no --turns: the plan is the old one"
    for bad in ("0", "5"):
        monkeypatch.setattr(sys, "argv", ["runner.py", "--run", "d", "--series", "series_toy_task", "--turns", bad])
        with pytest.raises(SystemExit, match="--turns is 1-4"):
            runner.main()


def test_every_task_gets_at_least_one_followup_by_default(tmp_path, monkeypatch):
    """ABTEST_MIN_FOLLOWUPS (default 1): an assistant that is done at once still gets the first scripted follow-up, so a
    later message that refers back is always tested; after it, "done" ends the session as before."""
    monkeypatch.setattr(multiturn, "MIN_FOLLOWUPS", 1)
    out, calls = session(tmp_path, [turn("Fixed: added it.", 0.1), turn("Fixed the empty input too.", 0.1)])
    assert [c[0] for c in calls][1:] == ["it also breaks on empty input"], "the first follow-up, word for word from the script"
    assert [t["user_kind"] for t in out["turn_log"]] == ["opener", "followup"]
    assert out["user_turns"] == 2 and out["ended_by"] == "thanks"


def test_the_simulated_user_falls_back_to_gpt_oss_when_z_ai_fails(monkeypatch):
    made = []

    def fake(key, profile=multiturn.LLM_PROFILE, **kw):
        made.append(profile)
        if profile == multiturn.LLM_PROFILE:
            def bad(s, u):
                raise RuntimeError("glm-5.3-flash call failed (HTTP 429)")
            return bad
        return lambda s, u: "from gpt-oss"

    monkeypatch.setattr(multiturn, "zai_llm", fake)
    llm = runner.sim_llm({"zai": "z", "gc": "k"})
    assert llm("s", "u") == "from gpt-oss" and made == [multiturn.LLM_PROFILE, "gpt-oss-120b"]
    with pytest.raises(RuntimeError):
        runner.sim_llm({"zai": "z"})("s", "u")


def test_a_cheap_answer_is_the_shown_message_too(tmp_path):
    log = tmp_path / "t.jsonl"
    reason = "Answered by glm-5.3-flash in 7 s (StealthLab answered it before this session's model ran; checked by claude-sonnet-5-5).\n\nYes, it does."
    log.write_text(json.dumps({"type": "system", "hook_output": json.dumps({"decision": "block", "reason": reason})}) + "\n", encoding="utf-8")
    msg = multiturn.shown_message(log)
    assert msg == reason
    assert multiturn.DONE_BY.search(msg).groups() == ("glm-5.3-flash", "7")


def test_each_cheap_answer_is_graded_against_the_merged_fix_and_reported(monkeypatch):
    import types
    import report
    import runner
    seen = []

    def fake_run(cmd, input, **kw):
        seen.append((cmd, input))
        return types.SimpleNamespace(stdout=json.dumps({"result": '{"grade": "correct", "why": "matches the fix"}', "total_cost_usd": 0.01}))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    mod = types.SimpleNamespace(issue_text=lambda t: "the issue", rec=lambda t: {"patch": "diff --git a/x b/x"})
    rec = {"turn_log": [{"dispatched": True, "answered": False, "shown": "Done by m in 5 s"},
                        {"dispatched": True, "answered": True, "user_text": "so it works?", "shown": "Answered by glm-5.3-flash in 4 s ...\n\nYes."}]}
    runner.grade_answers({"task_id": "t"}, mod, rec, {})
    assert len(seen) == 1 and "The merged fix:\ndiff --git a/x b/x" in seen[0][1] and "The reply:\nAnswered by" in seen[0][1]
    assert rec["turn_log"][1]["answer_grade"]["grade"] == "correct" and "answer_grade" not in rec["turn_log"][0]
    rows = {("t", "A"): {"task_id": "t", "suite": "swe", "resolved": True, "total_cost_usd": 1.0},
            ("t", "C"): {"task_id": "t", "suite": "swe", "resolved": True, "total_cost_usd": 0.5, **rec}}
    text = report.summarise(rows)
    assert "C turns done by cheap models: 2 of 2 (1 changes, 1 answers)" in text, text
    assert "C cheap answers, graded against the merged fix: 1 correct, 0 incorrect" in text, text
