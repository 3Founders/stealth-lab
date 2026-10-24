"""Claude through Bedrock as the overflow of the subscription (modal_series.py --auth subscription+bedrock): the switch after a usage limit
with the issue's state restored, the spending ledger and its cap, the auth recorded per session, the way back to the subscription, and
that no AWS credential reaches a command line, a log, a result or an error. Offline: a stub plays the shell and the runner."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modal_series as ms          # noqa: E402
import runner                      # noqa: E402
from test_modal_series_offline import Proc, fake_series  # noqa: E402,F401

KEY, SECRET_KEY, TOKEN = "AKIAFAKEFAKEFAKE1234", "wJalrFAKEsecretFAKEkeyFAKE0987654321abcd", "oauth-token-FAKE-1234567890"
ENV = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "AWS_ACCESS_KEY_ID": KEY, "AWS_SECRET_ACCESS_KEY": SECRET_KEY, "AWS_REGION": "us-east-1",
       "BEDROCK_OPUS_MODEL": "us.anthropic.claude-opus-5-5-v1:0", "BEDROCK_SONNET_MODEL": "us.anthropic.claude-sonnet-5-5-v1:0",
       "GENERAL_COMPUTE_API_KEY": "gc-FAKE-key-1234567890", "OPENROUTER_API_KEY": "or-FAKE-key-1234567890", "JEV_BASE_URL": "http://x",
       "JEV_API_KEY": "jev-FAKE-key-1234567890", "PATH": "p"}
SECRETS = (KEY, SECRET_KEY, TOKEN)


class Stub:
    """The shell: records every command with the environment it got; plays the runner (rows in the results file, a change in the
    workspace), pg_dump and psql. `limit_on` is the list of tasks whose SUBSCRIPTION session hits the usage limit."""

    def __init__(self, c_ref, limit_on=(), cost=3.0, probe_ok=False, bedrock_rc=0):
        self.c, self.calls, self.limit_on, self.cost, self.probe_ok, self.bedrock_rc = c_ref, [], set(limit_on), cost, probe_ok, bedrock_rc

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
            bedrock = (env or {}).get("ABTEST_AUTH") == "bedrock"
            limited = (task in self.limit_on and not bedrock) or (bedrock and self.bedrock_rc == 3)
            ws = c.local / "ws" / c.spec.arms[0] / "swe-mini"
            (ws / ".stealth").mkdir(parents=True, exist_ok=True)
            (ws / ".stealth" / "library.md").write_text(f"half-done {task}" if limited else f"finished {task}", encoding="utf-8")
            results = c.local / "results" / f"{c.spec.run_name()}.jsonl"
            results.parent.mkdir(parents=True, exist_ok=True)
            row = {"task_id": task, "arm": c.spec.arms[0], "auth": "bedrock" if bedrock else "subscription",
                   "status": "rate_limited" if limited else "done", "claude_cost_usd": 0.0 if limited else self.cost}
            with results.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + chr(10))
            return Proc(3 if limited else 0, err="usage limit" if limited else "")
        if text.startswith("claude -p"):
            return Proc(0 if self.probe_ok else 1, out="ok" if self.probe_ok else "usage limit reached")
        if "pg_tables" in text and "-At" in text:
            return Proc(0, out=chr(10).join([*ms.ROUTER_TABLES, "goals"]))
        if "pg_dump" in text:
            name = "db_baseline.sql.gz" if "db_baseline" in text else "pre_issue_db.sql.gz" if "pre_issue" in text else "db.sql.gz"
            (c.out / name).write_bytes(b"dump")
        return Proc(0)


def make(tmp_path, arm="C", *, limit_on=(), cost=3.0, probe_ok=False, clock=None, bedrock_rc=0, **kw):
    holder = {}
    stub = Stub(lambda: holder["c"], limit_on, cost, probe_ok, bedrock_rc)
    t = [1_000_000.0]
    spec = ms.RunSpec(**{"repos": ["mini"], "series_module": "fake_series", "arms": [arm], "unit_arm": arm, "auth": ms.COMBO, **kw})
    c = ms.Container(spec, "mini", sh=stub, vol=tmp_path / "vol", app=tmp_path / "app", environ=ENV, commit=lambda: None,
                     sleep=lambda s: None, health=lambda: True, clock=clock or (lambda: t[0]))
    holder["c"] = c
    c.stub, c.t = stub, t
    return c


def runs(c):
    return [x for x in c.stub.calls if "runner.py" in x["cmd"]]


def test_the_combined_mode_needs_the_subscription_token_and_every_bedrock_value_by_name():
    env = ms.auth_env(ms.COMBO, ENV)
    assert env["ABTEST_AUTH"] == "subscription" and "ANTHROPIC_API_KEY" not in env
    with pytest.raises(SystemExit, match="CLAUDE_CODE_OAUTH_TOKEN"):
        ms.auth_env(ms.COMBO, {k: v for k, v in ENV.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"})
    with pytest.raises(SystemExit) as e:
        ms.auth_env(ms.COMBO, {k: v for k, v in ENV.items() if k not in ("AWS_REGION", "BEDROCK_OPUS_MODEL")})
    assert "AWS_REGION" in str(e.value) and "BEDROCK_OPUS_MODEL" in str(e.value) and KEY not in str(e.value)


def test_every_session_starts_on_the_subscription_with_no_aws_credential_in_its_environment(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2)
    out = c.run()
    assert out["status"] == "done" and [r["auth"] for r in out["ran"]] == ["subscription", "subscription"]
    for r in runs(c):
        assert r["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN and r["env"]["ABTEST_AUTH"] == "subscription"
        assert not any(k.startswith("AWS_") or k.startswith("BEDROCK") or k == "CLAUDE_CODE_USE_BEDROCK" for k in r["env"])


def test_a_usage_limit_restarts_the_issue_on_bedrock_with_its_state_restored(tmp_path, fake_series):
    c = make(tmp_path, max_issues=1, limit_on={"x__mini-1"})
    ws_file = c.local / "ws" / "C" / "swe-mini" / ".stealth" / "library.md"
    c.stub.seed = ws_file                        # written when the series' data is fetched, i.e. before issue 1: part of its checkpoint
    seen_at_retry = []
    orig = c.stub.__call__

    def watch(argv, **kw):
        text = argv if isinstance(argv, str) else " ".join(map(str, argv))
        if "runner.py" in text and (kw.get("env") or {}).get("ABTEST_AUTH") == "bedrock":
            seen_at_retry.append(ws_file.read_text(encoding="utf-8"))
            seen_at_retry.append((c.local / "results" / "long-mini-1t.jsonl").exists())
        return orig(argv, **kw)

    c.sh = watch
    out = c.run()
    assert [r["auth"] for r in out["ran"]] == ["bedrock"] and out["status"] == "done"
    assert seen_at_retry == ["before the issue", False], "the half-done workspace and the rate-limited row were rolled back before the retry"
    rows = [json.loads(ln) for ln in (c.local / "results" / "long-mini-1t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["status"], r["auth"]) for r in rows] == [("done", "bedrock")], "the row says which auth served the session"
    env = [x for x in c.stub.calls if "runner.py" in x["cmd"]][-1]["env"]
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1" and env["AWS_REGION"] == "us-east-1" and env["ABTEST_BEDROCK_OPUS"] == ENV["BEDROCK_OPUS_MODEL"]
    assert env["ABTEST_BEDROCK_SONNET"] == ENV["BEDROCK_SONNET_MODEL"] and "CLAUDE_CODE_OAUTH_TOKEN" not in env, "never both in one session"
    assert env["AWS_ACCESS_KEY_ID"] == KEY
    events = (c.run_dir / "auth_events.jsonl").read_text(encoding="utf-8")
    assert "usage limit on the subscription" in events


def test_the_next_issue_stays_on_bedrock_until_the_subscription_works_again(tmp_path, fake_series):
    c = make(tmp_path, max_issues=3, limit_on={"x__mini-1"})
    c.run()
    autos = [r["env"]["ABTEST_AUTH"] for r in runs(c)]
    assert autos == ["subscription", "bedrock", "bedrock", "bedrock"], "issue 1 hit the limit; issues 2 and 3 start on Bedrock within the hour"
    assert not [x for x in c.stub.calls if x["cmd"].startswith("claude -p")], "no probe inside the hour"


def test_after_an_hour_a_tiny_call_tries_the_subscription_and_a_success_switches_back(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, limit_on={"x__mini-1"}, probe_ok=True)
    c.run_issue("x__mini-1", {})                                     # limit -> Bedrock
    assert c._auth_state()["mode"] == "bedrock"
    c.t[0] += ms.PROBE_EVERY_S + 1
    proc, rc, auth = c.run_issue("x__mini-2", {})
    assert auth == "subscription" and rc == 0, "the probe worked: back on the subscription"
    assert len([x for x in c.stub.calls if x["cmd"].startswith("claude -p")]) == 1 and c._auth_state()["mode"] == "subscription"


def test_a_failing_probe_keeps_it_on_bedrock_and_waits_another_hour(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, limit_on={"x__mini-1"}, probe_ok=False)
    c.run_issue("x__mini-1", {})
    c.t[0] += ms.PROBE_EVERY_S + 1
    _, _, auth = c.run_issue("x__mini-2", {})
    assert auth == "bedrock"
    c.t[0] += 60
    c.run_issue("x__mini-3", {})
    assert len([x for x in c.stub.calls if x["cmd"].startswith("claude -p")]) == 1, "one probe per hour, not per issue"


def test_each_bedrock_session_goes_into_the_ledger_once_at_its_reported_cost_with_the_margin(tmp_path, fake_series):
    c = make(tmp_path, max_issues=1, limit_on={"x__mini-1"}, cost=3.0, bedrock_price_factor=1.1)
    c.run()
    rows = c.ledger_entries()
    assert len(rows) == 1 and rows[0]["task_id"] == "x__mini-1" and rows[0]["arm"] == "C" and rows[0]["kind"] == "session"
    assert rows[0]["usd"] == round(3.0 * 1.1 * ms.BEDROCK_HELPER_MARGIN, 4) and rows[0]["reported_usd"] == 3.0
    assert c.ledger_add("x__mini-1") == 0.0 and len(c.ledger_entries()) == 1, "idempotent"
    assert c.ledger_total() == rows[0]["usd"]


def test_the_cap_stops_a_new_bedrock_session_and_the_unit_waits_for_the_subscription(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, limit_on={"x__mini-1"}, bedrock_cap_usd=10.0)
    c.ledger_dir.mkdir(parents=True)
    (c.ledger_dir / "other.jsonl").write_text(json.dumps({"kind": "session", "usd": 8.0, "task_id": "t", "arm": "A"}) + chr(10), encoding="utf-8")
    ok, total, room = c.bedrock_allowed()
    assert not ok and total == 8.0 and room >= ms.BEDROCK_TYPICAL_SESSION_USD
    out = c.run()
    assert out["status"] == "waiting_for_subscription" and "repeat the same command" in out["resume"]
    assert not [r for r in runs(c) if r["env"].get("ABTEST_AUTH") == "bedrock"], "no Bedrock session was started"
    assert "Bedrock cap" in (c.run_dir / "auth_events.jsonl").read_text(encoding="utf-8")
    rows = (c.local / "results" / "long-mini-1t.jsonl")
    assert not rows.exists() or "rate_limited" not in rows.read_text(encoding="utf-8"), "the half-done session left no row behind"


def test_the_cap_keeps_room_for_a_session_in_every_unit_that_could_start_one_at_once(tmp_path, fake_series):
    solo = make(tmp_path / "a", bedrock_cap_usd=20.0, units_in_flight=1)
    five = make(tmp_path / "b", bedrock_cap_usd=20.0, units_in_flight=5)
    for c in (solo, five):
        c.ledger_dir.mkdir(parents=True)
        (c.ledger_dir / "x.jsonl").write_text(json.dumps({"kind": "session", "usd": 6.0, "task_id": "t", "arm": "A"}) + chr(10), encoding="utf-8")
    assert solo.bedrock_allowed()[0] is True and five.bedrock_allowed()[0] is False


def test_arm_specs_tell_each_unit_how_many_may_start_a_bedrock_session_together():
    specs = ms.arm_specs(ms.RunSpec(repos=["mini"], auth=ms.COMBO), ["t1"])
    assert [s.units_in_flight for s in specs] == [5] * 5


def test_the_forced_overflow_moves_exactly_one_session_to_bedrock_and_lands_in_the_ledger(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, force_overflow=True, probe_ok=True)
    c.run_issue("x__mini-1", {})
    assert [r["env"]["ABTEST_AUTH"] for r in runs(c)] == ["bedrock"], "the forced limit never ran the subscription session"
    assert len(c.ledger_entries()) == 1
    c.t[0] += ms.PROBE_EVERY_S + 1
    _, _, auth = c.run_issue("x__mini-2", {})
    assert auth == "subscription" and c._auth_state()["forced_done"] is True


def test_a_bedrock_throttle_is_a_wait_not_a_loop(tmp_path, fake_series):
    c = make(tmp_path, max_issues=1, limit_on={"x__mini-1"}, bedrock_rc=3)
    out = c.run()
    assert out["status"] == "rate_limited" and len(runs(c)) == 2, "one subscription attempt, one Bedrock attempt, then it stops"


def test_no_credential_reaches_a_command_line_a_log_a_result_or_an_error(tmp_path, fake_series):
    c = make(tmp_path, max_issues=2, limit_on={"x__mini-1"})
    c.run()
    for call in c.stub.calls:
        assert not any(s in call["cmd"] for s in SECRETS), call["cmd"][:120]
    for f in (tmp_path).rglob("*"):
        if f.is_file() and f.suffix in (".jsonl", ".json", ".txt", ".log", ".md", ".sql"):
            body = f.read_text(encoding="utf-8", errors="replace")
            assert not any(s in body for s in SECRETS), f
    msg = c.scrub(f"failed with {KEY} and {SECRET_KEY} and {TOKEN} and {ENV['GENERAL_COMPUTE_API_KEY']}")
    assert not any(s in msg for s in (*SECRETS, ENV["GENERAL_COMPUTE_API_KEY"])) and msg.count("***") == 4
    with pytest.raises(RuntimeError) as e:
        c._must(Proc(1, err=f"boom {SECRET_KEY}"), "a step")
    assert SECRET_KEY not in str(e.value)


def test_the_plan_names_the_overflow_the_cap_and_the_secrets(fake_series):
    text = ms.plan_text(ms.RunSpec(repos=["mini"], series_module="fake_series", auth=ms.COMBO, arms=list(ms.ARMS)), {"mini": []})
    assert "overflowing to Bedrock" in text and "$70" in text and ms.BEDROCK_SECRET in text and ms.CLAUDE_SECRET in text


def test_the_secrets_command_creates_aws_bedrock_from_names_and_prints_no_value(monkeypatch):
    import argparse
    made = {}
    monkeypatch.setattr(ms, "modal", object())
    monkeypatch.setattr(ms, "create_secret", lambda name, vals: made.update({name: vals}))
    said = []
    assert ms.secrets_cli(argparse.Namespace(claude=False, api=False, keys=False, bedrock=True), environ=ENV, out=said.append) == 0
    assert set(made[ms.BEDROCK_SECRET]) == {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "BEDROCK_OPUS_MODEL", "BEDROCK_SONNET_MODEL"}
    assert not any(s in " ".join(said) for s in SECRETS)
    said.clear()
    assert ms.secrets_cli(argparse.Namespace(claude=False, api=False, keys=False, bedrock=True), environ={}, out=said.append) == 2


# ---------------------------------------------------------------- the runner's side

def test_the_runner_on_bedrock_uses_bedrocks_ids_drops_the_logins_and_records_the_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "LOCAL", tmp_path)
    monkeypatch.setattr(runner, "c_home", lambda: tmp_path / "home")
    for k, v in {"ABTEST_AUTH": "bedrock", "ABTEST_BEDROCK_OPUS": ENV["BEDROCK_OPUS_MODEL"], "ABTEST_BEDROCK_SONNET": ENV["BEDROCK_SONNET_MODEL"],
                 "CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k", "AWS_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    env = runner.arm_env("A", {})
    assert env["ANTHROPIC_MODEL"] == ENV["BEDROCK_OPUS_MODEL"] and env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == ENV["BEDROCK_SONNET_MODEL"] and env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == ENV["BEDROCK_OPUS_MODEL"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env and env["AWS_REGION"] == "us-east-1"
    assert runner.session_model() == ENV["BEDROCK_OPUS_MODEL"] and runner.judge_model() == ENV["BEDROCK_SONNET_MODEL"]


def test_the_runner_on_the_subscription_is_unchanged(monkeypatch):
    monkeypatch.setenv("ABTEST_AUTH", "subscription")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    env = runner.arm_env("A", {})
    assert env["ANTHROPIC_MODEL"] == runner.MODEL and "CLAUDE_CODE_USE_BEDROCK" not in env and env["CLAUDE_CODE_OAUTH_TOKEN"] == "t"
    assert runner.session_model() == runner.MODEL and runner.judge_model() == runner.ANSWER_JUDGE_MODEL
