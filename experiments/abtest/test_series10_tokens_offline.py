"""Several Claude logins: a second one takes over when the first hits its usage limit or is near the end of its five-hour window, the issue
starts again from its beginning with its state restored, units share what they have seen of each login, and no token value is ever logged
or recorded (only a fingerprint). Offline: a stub plays the shell and the runner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modal_series as ms          # noqa: E402
import runner                      # noqa: E402
from test_modal_series_offline import Proc, fake_series  # noqa: E402,F401

T1, T2, T3 = "oauth-token-ONE-FAKE-1234567890", "oauth-token-TWO-FAKE-1234567890", "oauth-token-THREE-FAKE-123456789"
ENV = {"CLAUDE_CODE_OAUTH_TOKEN": T1, "CLAUDE_CODE_OAUTH_TOKEN_2": T2, "GENERAL_COMPUTE_API_KEY": "gc-FAKE-key-1234567890",
       "OPENROUTER_API_KEY": "or-FAKE-key-1234567890", "JEV_BASE_URL": "http://x", "JEV_API_KEY": "jev-FAKE-key-1234567890", "PATH": "p"}


class Stub:
    """The shell and the runner. `limited` maps a login's token value to the tasks that hit its usage limit on it."""

    def __init__(self, c_ref, limited=None):
        self.c, self.calls, self.limited = c_ref, [], limited or {}

    def __call__(self, argv, *, env=None, cwd=None, timeout=None, background=False, log=None):
        text = argv if isinstance(argv, str) else " ".join(map(str, argv))
        self.calls.append({"cmd": text, "env": dict(env or {}), "background": background})
        if background:
            return object()
        c = self.c()
        if "fake_series.py" in text and getattr(self, "seed", None):
            self.seed.parent.mkdir(parents=True, exist_ok=True)
            self.seed.write_text("before the issue", encoding="utf-8")
        if "runner.py" in text:
            task = argv[argv.index("--only") + 1]
            tok = (env or {}).get("CLAUDE_CODE_OAUTH_TOKEN")
            hit = task in self.limited.get(tok, ())
            ws = c.local / "ws" / c.spec.arms[0] / "swe-mini"
            (ws / ".kell").mkdir(parents=True, exist_ok=True)
            (ws / ".kell" / "library.md").write_text("half-done" if hit else "finished", encoding="utf-8")
            results = c.local / "results" / f"{c.spec.run_name()}.jsonl"
            results.parent.mkdir(parents=True, exist_ok=True)
            with results.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"task_id": task, "arm": c.spec.arms[0], "status": "rate_limited" if hit else "done",
                                    "login": (env or {}).get("ABTEST_TOKEN_ID")}) + chr(10))
            return Proc(3 if hit else 0, err="usage limit" if hit else "")
        if "pg_tables" in text and "-At" in text:
            return Proc(0, out=chr(10).join([*ms.ROUTER_TABLES, "goals"]))
        if "pg_dump" in text:
            name = "db_baseline.sql.gz" if "db_baseline" in text else "pre_issue_db.sql.gz" if "pre_issue" in text else "db.sql.gz"
            (c.out / name).write_bytes(b"dump")
        return Proc(0)


def make(tmp_path, arm="A", limited=None, env=None, t=None, **kw):
    holder = {}
    stub = Stub(lambda: holder["c"], limited)
    clock = t if t is not None else [2_000_000.0]
    spec = ms.RunSpec(**{"repos": ["mini"], "series_module": "fake_series", "arms": [arm], "unit_arm": arm, **kw})
    c = ms.Container(spec, "mini", sh=stub, vol=tmp_path / "vol", app=tmp_path / "app", environ=env or ENV, commit=lambda: None,
                     sleep=lambda s: None, health=lambda: True, clock=lambda: clock[0])
    holder["c"] = c
    c.stub, c.t = stub, clock
    return c


def logins(c):
    return [x["env"].get("CLAUDE_CODE_OAUTH_TOKEN") for x in c.stub.calls if "runner.py" in x["cmd"]]


def test_the_logins_are_found_in_order_and_identified_by_a_fingerprint_only():
    toks = ms.claude_tokens({"CLAUDE_CODE_OAUTH_TOKEN_3": T3, "CLAUDE_CODE_OAUTH_TOKEN": T1, "CLAUDE_CODE_OAUTH_TOKEN_2": T2, "OTHER": "x"})
    assert [t.rank for t in toks] == [1, 2, 3] and [t.value for t in toks] == [T1, T2, T3]
    assert all(t.id.startswith(f"t{t.rank}-") and t.value not in t.id and len(t.id) == len("t1-") + 8 for t in toks)
    assert ms.claude_tokens({}) == []


def test_only_the_chosen_login_is_in_a_sessions_environment(tmp_path, fake_series):
    c = make(tmp_path, max_issues=1)
    assert "CLAUDE_CODE_OAUTH_TOKEN_2" not in c.environ and [t.rank for t in c.tokens] == [1, 2]
    c.run()
    for x in c.stub.calls:
        assert "CLAUDE_CODE_OAUTH_TOKEN_2" not in x["env"], "the second login never sits in an environment next to the first"
    assert logins(c) == [T1]


def test_a_usage_limit_moves_the_issue_to_the_next_login_with_its_state_restored(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, limited={T1: {"x__mini-1"}})
    seen = []
    c.stub.seed = c.local / "ws" / "A" / "swe-mini" / ".kell" / "library.md"
    orig = c.stub.__call__

    def watch(argv, **kw):
        text = argv if isinstance(argv, str) else " ".join(map(str, argv))
        if "runner.py" in text and (kw.get("env") or {}).get("CLAUDE_CODE_OAUTH_TOKEN") == T2 and not seen:
            seen.append(c.stub.seed.read_text(encoding="utf-8"))
            seen.append((c.local / "results" / "long-mini-1t.jsonl").exists())
        return orig(argv, **kw)

    c.sh = watch
    out = c.run()
    assert out["status"] == "done"
    assert logins(c) == [T1, T2, T2], "issue 1: login 1 hit its limit, login 2 ran it again; issue 2 stays on login 2 (login 1 is limited)"
    assert seen == ["before the issue", False], "the half-done workspace and the rate-limited row were rolled back before the retry"
    rows = [json.loads(ln) for ln in (c.local / "results" / "long-mini-1t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows] == ["done", "done"] and rows[0]["login"].startswith("t2-") and rows[0]["login"] == rows[1]["login"]
    events = (c.out.parent / "auth_events.jsonl").read_text(encoding="utf-8") if (c.out.parent / "auth_events.jsonl").exists() else ""
    assert "takes over after a usage limit" in events or "takes over" in (c.run_dir / "auth_events.jsonl").read_text(encoding="utf-8")


def test_every_login_at_its_limit_stops_the_run_like_before(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, limited={T1: {"x__mini-1"}, T2: {"x__mini-1"}})
    out = c.run()
    assert out["status"] == "rate_limited" and "repeat the same command" in out["resume"]
    assert logins(c) == [T1, T2], "each login was tried once"
    rows = c.local / "results" / "long-mini-1t.jsonl"
    assert not rows.exists() or "done" not in rows.read_text(encoding="utf-8")


def test_a_login_near_the_end_of_its_window_gives_way_to_a_fresh_one(tmp_path, fake_series):
    c = make(tmp_path, max_issues=1)
    toks = c.tokens
    c._save_token(toks[0].id, window_start=c.clock() - ms.ROTATE_AFTER_S - 60)
    assert c.pick_token().rank == 2, "login 1 is 4.5 h into its window: login 2 is fresh"
    c._save_token(toks[1].id, window_start=c.clock() - ms.ROTATE_AFTER_S - 60)
    assert c.pick_token().rank == 1, "both near the end: the first (it resets soonest) is still better than stopping"


def test_a_login_that_is_comfortable_stays_first_and_a_window_that_ran_out_is_fresh_again(tmp_path, fake_series):
    c = make(tmp_path)
    t1, t2 = c.tokens
    c._save_token(t1.id, window_start=c.clock() - 3600)
    assert c.pick_token().rank == 1 and c.token_room(t1, c.token_state()[t1.id], c.clock()) == "ok"
    c._save_token(t1.id, limited_at=c.clock() - 60)
    assert c.pick_token().rank == 2, "limited a minute ago"
    c.t[0] += ms.WINDOW_S + 1
    assert c.pick_token().rank == 1, "five hours later its window has run out: it is first again"


def test_units_share_what_they_have_seen_of_each_login(tmp_path, fake_series):
    a = make(tmp_path, arm="A", max_issues=1, limited={T1: {"x__mini-1"}})
    a.run()
    b = make(tmp_path, arm="C", max_issues=1)           # another arm's container, same volume: it must not start on the limited login
    b.run()
    assert logins(b)[0] == T2


def test_a_session_never_changes_login_half_way_and_the_row_names_the_login(tmp_path, fake_series):
    c = make(tmp_path, max_issues=1)
    c.run()
    env = [x["env"] for x in c.stub.calls if "runner.py" in x["cmd"]][0]
    assert env["ABTEST_TOKEN_ID"] == c.tokens[0].id and env["CLAUDE_CODE_OAUTH_TOKEN"] == T1


def test_no_token_value_reaches_a_log_a_result_a_state_file_or_an_error(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, limited={T1: {"x__mini-1"}})
    c.run()
    for call in c.stub.calls:
        assert not any(tok in call["cmd"] for tok in (T1, T2))
    for f in tmp_path.rglob("*"):
        if f.is_file() and f.suffix in (".jsonl", ".json", ".txt", ".log", ".md"):
            body = f.read_text(encoding="utf-8", errors="replace")
            assert T1 not in body and T2 not in body, f
    assert T2 not in c.scrub(f"failed {T2} and {T1}") and c.scrub(f"{T2}").count("***") == 1


def test_one_login_is_the_old_behaviour_with_nothing_remembered(tmp_path, fake_series):
    one = {k: v for k, v in ENV.items() if k != "CLAUDE_CODE_OAUTH_TOKEN_2"}
    c = make(tmp_path, env=one, max_issues=2, limited={T1: {"x__mini-1"}})
    out = c.run()
    assert out["status"] == "rate_limited" and logins(c) == [T1]
    assert not (tmp_path / "vol" / "tokens").exists(), "no state files for a single login"


def test_the_secrets_command_stores_every_login_by_name_and_prints_no_value(monkeypatch, tmp_path):
    made, said = {}, []
    monkeypatch.setattr(ms, "modal", object())
    monkeypatch.setattr(ms, "create_secret", lambda name, vals: made.update({name: vals}))
    env = {**ENV, "CLAUDE_CODE_OAUTH_TOKEN_3": T3}
    assert ms.secrets_cli(argparse.Namespace(claude=True, api=False, keys=False, bedrock=False), environ=env, env_file=tmp_path / "none.env", out=said.append) == 0
    assert made[ms.CLAUDE_SECRET] == {"CLAUDE_CODE_OAUTH_TOKEN": T1, "CLAUDE_CODE_OAUTH_TOKEN_2": T2, "CLAUDE_CODE_OAUTH_TOKEN_3": T3}
    assert not any(t in " ".join(said) for t in (T1, T2, T3)) and "CLAUDE_CODE_OAUTH_TOKEN_2" in " ".join(said)


def test_the_runner_row_carries_the_login_id(monkeypatch):
    src = Path(runner.__file__).read_text(encoding="utf-8")
    assert '"login": os.environ.get("ABTEST_TOKEN_ID")' in src


def test_the_logins_come_from_backend_env_first_and_the_secret_is_not_replaced_without_the_first_one(monkeypatch, tmp_path):
    made, said = {}, []
    monkeypatch.setattr(ms, "modal", object())
    monkeypatch.setattr(ms, "create_secret", lambda name, vals: made.update({name: vals}))
    f = tmp_path / ".env"
    f.write_text(f"CLAUDE_CODE_OAUTH_TOKEN_2={T2}" + chr(10) + "OTHER=1" + chr(10), encoding="utf-8")
    ns = argparse.Namespace(claude=True, api=False, keys=False, bedrock=False)
    assert ms.secrets_cli(ns, environ={}, env_file=f, out=said.append) == 2 and not made, "the first login is missing: nothing is replaced"
    assert "first login" in said[0] and T2 not in " ".join(said)
    f.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={T1}" + chr(10) + f"CLAUDE_CODE_OAUTH_TOKEN_2={T2}" + chr(10), encoding="utf-8")
    assert ms.secrets_cli(ns, environ={"CLAUDE_CODE_OAUTH_TOKEN": "shell-token-IGNORED-1234567890"}, env_file=f, out=said.append) == 0
    assert made[ms.CLAUDE_SECRET] == {"CLAUDE_CODE_OAUTH_TOKEN": T1, "CLAUDE_CODE_OAUTH_TOKEN_2": T2}, "the file wins over the shell"
