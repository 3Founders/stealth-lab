"""Offline tests of the Modal app (modal_series.py) and the two runner changes it needs (keys by name from the environment,
the API-key auth mode). Nothing runs on Modal, no key is read, no network: Modal objects are only CONSTRUCTED (they are lazy),
the container's commands go through a recorder. Run from experiments/abtest:  python -m pytest test_modal_series_offline.py -q
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import modal_series as ms  # noqa: E402
import runner  # noqa: E402

TOKEN = "TOKEN-VALUE-THAT-MUST-NEVER-BE-PRINTED"
ENV_SUB = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "ANTHROPIC_API_KEY": "stale-key", "PATH": "p"}
ENV_API = {"ANTHROPIC_API_KEY": "api-key-value", "CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "PATH": "p"}


@pytest.fixture()
def fake_series(monkeypatch):
    mod = types.ModuleType("fake_series")
    mod.series = lambda name=None: [{"task_id": f"x__{name}-{i}", "suite": "swe", "workspace": f"swe-{name}"} for i in range(1, 5)]
    monkeypatch.setitem(sys.modules, "fake_series", mod)
    return mod


def spec(**kw):
    return ms.RunSpec(**{"repos": ["mini"], "series_module": "fake_series", **kw})


# ---------------------------------------------------------------- the spec and the plan

def test_validate_spec():
    assert ms.validate_spec(spec()) == []
    assert any("--repos" in p for p in ms.validate_spec(ms.RunSpec(repos=[])))
    assert any("arms are" in p for p in ms.validate_spec(spec(arms=["A", "Z"])))
    assert any("--auth" in p for p in ms.validate_spec(spec(auth="oauth")))
    assert any("--turns" in p for p in ms.validate_spec(spec(turns=5)))
    assert any("negative" in p for p in ms.validate_spec(spec(max_issues=-1)))


def test_the_run_name_is_stable_so_a_repeated_command_resumes():
    assert spec().run_name() == "long-mini-1t" and spec(run="mine").run_name() == "mine"
    assert ms.RunSpec(repos=["a", "b"], turns=3).run_name() == "long-a-b-3t"


def test_the_plan_says_what_runs_where_and_what_it_needs(fake_series):
    s = spec(max_issues=2)
    text = ms.plan_text(s, {"mini": ms.series_tasks(s, "mini")})
    assert "mini: 2 issue(s) -> 6 session(s)" in text and "6 session(s) in all" in text and "one after another" in text
    assert f"{ms.KEYS_SECRET} + {ms.CLAUDE_SECRET}" in text and "/vol/long-mini-1t/<repository>/" in text
    api = ms.plan_text(spec(auth="api"), {"mini": ms.series_tasks(spec(), "mini")})
    assert "in parallel" in api and ms.API_SECRET in api and ms.CLAUDE_SECRET not in api
    assert len(ms.series_tasks(spec(), "mini")) == 4, "0 means every issue"


# ---------------------------------------------------------------- auth

def test_subscription_needs_the_token_and_drops_an_api_key():
    env = ms.auth_env("subscription", ENV_SUB)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN and "ANTHROPIC_API_KEY" not in env and env["ABTEST_AUTH"] == "subscription"
    with pytest.raises(SystemExit) as e:
        ms.auth_env("subscription", {"ANTHROPIC_API_KEY": "k"})
    assert "CLAUDE_CODE_OAUTH_TOKEN" in str(e.value) and "k" != str(e.value)


def test_api_mode_needs_the_key_and_drops_the_subscription_token():
    env = ms.auth_env("api", ENV_API)
    assert env["ANTHROPIC_API_KEY"] == "api-key-value" and "CLAUDE_CODE_OAUTH_TOKEN" not in env and env["ABTEST_AUTH"] == "api"
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        ms.auth_env("api", {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
    with pytest.raises(SystemExit, match="--auth is one of"):
        ms.auth_env("magic", {})


def test_the_runner_keeps_the_subscription_token_and_in_api_mode_swaps_it_for_the_key(monkeypatch):
    for k, v in {**ENV_SUB, "ANTHROPIC_BASE_URL": "http://x"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ABTEST_AUTH", raising=False)
    env = runner.arm_env("A", {})
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN and "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_BASE_URL" not in env, "as before"
    monkeypatch.setenv("ABTEST_AUTH", "api")
    env = runner.arm_env("A", {})
    assert env["ANTHROPIC_API_KEY"] == "stale-key" and "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_BASE_URL" not in env


def test_keys_come_from_the_environment_first_then_backend_env(monkeypatch, tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text("FROM_FILE=file-value\nBOTH=file-both\n# c\n", encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setenv("BOTH", "env-both")
    monkeypatch.delenv("FROM_FILE", raising=False)
    monkeypatch.delenv("NOWHERE", raising=False)
    assert runner.secret_value("BOTH") == "env-both" and runner.secret_value("FROM_FILE") == "file-value" and runner.secret_value("NOWHERE") == ""
    monkeypatch.setattr(runner, "REPO", tmp_path / "no-such-repo")
    assert runner.secret_value("FROM_FILE") == "", "no backend/.env (a container): empty, not a crash"


# ---------------------------------------------------------------- what is uploaded and the app

@pytest.mark.parametrize("path, expected", [
    ("backend/.env", True), (".env", True), ("backend/.neon_shards.env", True), ("x/prod.env", True), ("experiments/abtest/.local/token.txt", True),
    ("a/signing_key.pem", True), ("demo.mov", True), ("node_modules/x/index.js", True), ("app/__pycache__/m.pyc", True), ("repo/.git/config", True),
    ("app/main.py", False), ("scripts/migrate.py", False), ("runner.py", False), ("db/01_ontology.sql", False), ("data/prior.json", False),
])
def test_what_never_reaches_the_image(path, expected):
    assert ms.ignored(path) is expected


@pytest.mark.skipif(ms.modal is None, reason="modal is not installed")
def test_the_image_and_the_app_are_built_without_touching_modal():
    assert isinstance(ms.build_image(), ms.modal.Image)
    assert hasattr(ms.run_subscription, "remote") and hasattr(ms.run_api, "starmap"), "both functions are registered on the app"
    assert ms.app.name == ms.APP_NAME


# ---------------------------------------------------------------- the container, with a recorder instead of a shell

class Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Recorder:
    """Stand-in for the shell: records every command, plays the runner (writes result rows) and pg_dump (writes the dump)."""

    def __init__(self, container_ref, rc_for=None):
        self.c, self.calls, self.rc_for, self.bad = container_ref, [], rc_for or {}, set()

    def __call__(self, argv, *, env=None, cwd=None, timeout=None, background=False):
        text = argv if isinstance(argv, str) else " ".join(map(str, argv))
        self.calls.append({"cmd": text, "env": env, "cwd": cwd, "background": background})
        if background:
            return object()
        c = self.c()
        if "runner.py" in text:
            task = argv[argv.index("--only") + 1]
            rc = self.rc_for.get(task, 0)
            results = c.local / "results" / f"{c.spec.run_name()}.jsonl"
            results.parent.mkdir(parents=True, exist_ok=True)
            with results.open("a", encoding="utf-8") as f:
                for arm in c.spec.arms[: (len(c.spec.arms) if rc == 0 else 1)]:
                    f.write(json.dumps({"task_id": task, "arm": arm, "status": "done" if rc == 0 else "rate_limited"}) + "\n")
            (c.local / "ws" / "C" / "swe-mini" / ".stealth").mkdir(parents=True, exist_ok=True)
            (c.local / "ws" / "C" / "swe-mini" / ".stealth" / "library.md").write_text(f"after {task}", encoding="utf-8")
            (c.local / "logs" / c.spec.run_name()).mkdir(parents=True, exist_ok=True)
            (c.local / "logs" / c.spec.run_name() / f"{task}.A.jsonl").write_text("{}", encoding="utf-8")
            return Proc(rc, err="usage limit" if rc else "")
        if "swe_series_task.py validate" in text.replace("\\", "/") or (isinstance(argv, list) and "validate" in argv):
            ids = argv[argv.index("validate") + 2:]
            rows = [{"instance_id": i, "usable": i not in self.bad, "gold_resolves": i not in self.bad,
                     "gold": {"error": "the gold patch does not apply"} if i in self.bad else {}} for i in ids]
            vp = c.local / "swe" / f"validate_{c.repo}.json"
            vp.parent.mkdir(parents=True, exist_ok=True)
            vp.write_text(json.dumps(rows), encoding="utf-8")
            return Proc(0)
        if "pg_dump" in text:
            (c.out / "db.sql.gz").write_bytes(b"dump")
        if "report.py" in text:
            return Proc(0, out="curve" if "--curve" in text else "summary")
        return Proc(0)


def container(tmp_path, rc_for=None, environ=None, **spec_kw):
    holder = {}
    rec = Recorder(lambda: holder["c"], rc_for)
    commits = []
    c = ms.Container(spec(**spec_kw), "mini", sh=rec, vol=tmp_path / "vol", app=tmp_path / "app",
                     environ=environ or ENV_SUB, commit=lambda: commits.append(1), sleep=lambda s: None, health=lambda: True)
    holder["c"] = c
    c.rec, c.commits = rec, commits
    return c


def test_a_fresh_run_starts_the_services_in_order_then_runs_the_runner_task_by_task(tmp_path, fake_series):
    c = container(tmp_path, max_issues=2, turns=3)
    out = c.run()
    cmds = [x["cmd"] for x in c.rec.calls]
    order = [next(i for i, t in enumerate(cmds) if key in t) for key in ("initdb", "pg_ctl", "createuser", "createdb", "migrate.py", "run_backend.py", "fake_series.py", "runner.py")]
    assert order == sorted(order), cmds
    assert [x["background"] for x in c.rec.calls if "run_backend.py" in x["cmd"]] == [True], "the backend is started in the background"
    runs = [x for x in c.rec.calls if "runner.py" in x["cmd"]]
    assert [r["cmd"].split("--only ")[1].split()[0] for r in runs] == ["x__mini-1", "x__mini-2"], "max_issues cuts the series"
    first = runs[0]["cmd"]
    for part in ("--run long-mini-3t", "--series fake_series:mini", "--arms A,C,C0", "--parallel", "--testfirst-model claude-sonnet-5-5",
                 "--turns 3", "--gc-key-name GENERAL_COMPUTE_API_KEY"):
        assert part in first, part
    assert out["status"] == "done" and out["ran"] == [{"task": "x__mini-1", "rc": 0}, {"task": "x__mini-2", "rc": 0}] and out["report"] == "summary\n\ncurve"
    assert (c.out / "report.txt").read_text(encoding="utf-8") == "summary\n\ncurve"
    assert len(c.commits) >= 3, "the volume is committed after every task and the report"


def test_a_single_turn_run_does_not_pass_turns(tmp_path, fake_series):
    c = container(tmp_path, max_issues=1)
    c.run()
    assert "--turns" not in next(x["cmd"] for x in c.rec.calls if "runner.py" in x["cmd"])


def test_the_runner_gets_the_auth_of_the_run_and_nothing_of_the_other(tmp_path, fake_series):
    c = container(tmp_path, max_issues=1)
    c.run()
    env = next(x["env"] for x in c.rec.calls if "runner.py" in x["cmd"])
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN and env["ABTEST_AUTH"] == "subscription" and "ANTHROPIC_API_KEY" not in env
    c2 = container(tmp_path / "api", environ=ENV_API, auth="api", max_issues=1)
    c2.run()
    env = next(x["env"] for x in c2.rec.calls if "runner.py" in x["cmd"])
    assert env["ANTHROPIC_API_KEY"] == "api-key-value" and "CLAUDE_CODE_OAUTH_TOKEN" not in env and env["ABTEST_AUTH"] == "api"
    with pytest.raises(SystemExit, match="CLAUDE_CODE_OAUTH_TOKEN"):
        container(tmp_path / "none", environ={"PATH": "p"})


def test_a_usage_limit_stops_the_run_after_the_snapshot_and_says_how_to_resume(tmp_path, fake_series):
    c = container(tmp_path, rc_for={"x__mini-2": ms.RC_RATE_LIMITED}, max_issues=4)
    out = c.run()
    assert out["status"] == "rate_limited" and "repeat the same command" in out["resume"]
    assert [r["task"] for r in out["ran"]] == ["x__mini-1", "x__mini-2"], "x__mini-3 never starts"
    assert (c.out / "state.tgz").exists() and (c.out / "results" / "long-mini-1t.jsonl").exists() and (c.out / "db.sql.gz").read_bytes() == b"dump"
    q = container(tmp_path / "q", rc_for={"x__mini-1": ms.RC_QUOTA}, max_issues=4).run()
    assert q["status"] == "quota" and len(q["ran"]) == 1


def test_resuming_restores_the_snapshot_and_skips_what_is_done(tmp_path, fake_series):
    first = container(tmp_path, rc_for={"x__mini-2": ms.RC_RATE_LIMITED}, max_issues=3)
    first.run()
    # a NEW container (empty local disk, same volume), the same command
    second = ms.Container(spec(max_issues=3), "mini", sh=Recorder(lambda: second, None), vol=tmp_path / "vol", app=tmp_path / "app2",
                          environ=ENV_SUB, commit=lambda: None, sleep=lambda s: None, health=lambda: True)
    assert second.restore() == "restored"
    lib = second.local / "ws" / "C" / "swe-mini" / ".stealth" / "library.md"
    assert lib.read_text(encoding="utf-8") == "after x__mini-2", "C's .stealth/ came back from the volume, as the last task left it"
    out = second.run()
    assert out["state"] == "restored" and out["already_done"] == 1
    ran = [x["cmd"].split("--only ")[1].split()[0] for x in second.sh.calls if "runner.py" in x["cmd"]]
    assert ran == ["x__mini-2", "x__mini-3"], "task 1 finished in every arm before; task 2 only in A (rate limited) and is redone"
    assert any("gunzip" in x["cmd"] and "psql" in x["cmd"] for x in second.sh.calls), "the database dump was restored"


def test_resuming_c_without_its_state_is_refused_unless_allowed(tmp_path, fake_series):
    c = container(tmp_path, max_issues=2)
    c.run()
    (c.out / "state.tgz").unlink()
    again2 = ms.Container(spec(max_issues=3), "mini", sh=Recorder(lambda: again2, None), vol=tmp_path / "vol", app=tmp_path / "app3",
                          environ=ENV_SUB, commit=lambda: None, sleep=lambda s: None, health=lambda: True)
    refused = again2.run()
    assert refused["status"] == "refused" and "learning curve" in refused["why"] and not any("runner.py" in x["cmd"] for x in again2.sh.calls)
    ok = ms.Container(spec(max_issues=3, allow_cold_resume=True), "mini", sh=Recorder(lambda: ok, None), vol=tmp_path / "vol",
                      app=tmp_path / "app4", environ=ENV_SUB, commit=lambda: None, sleep=lambda s: None, health=lambda: True)
    assert ok.run()["status"] == "done" and any("runner.py" in x["cmd"] for x in ok.sh.calls)
    a_only = ms.Container(spec(arms=["A"], max_issues=3), "mini", sh=Recorder(lambda: a_only, None), vol=tmp_path / "vol",
                          app=tmp_path / "app5", environ=ENV_SUB, commit=lambda: None, sleep=lambda s: None, health=lambda: True)
    assert a_only.run()["status"] == "done", "arm A has no .stealth/: a cold resume costs it nothing"


def test_a_backend_that_never_comes_up_is_an_error_not_a_hang(tmp_path, fake_series):
    c = container(tmp_path, max_issues=1)
    c.health = lambda: False
    with pytest.raises(RuntimeError, match="did not come up"):
        c.run()


def test_a_failing_setup_command_stops_before_any_session(tmp_path, fake_series):
    c = container(tmp_path, max_issues=1)
    inner = c.sh
    c.sh = lambda argv, **kw: Proc(1, err="boom") if "migrate.py" in str(argv) else inner(argv, **kw)
    with pytest.raises(RuntimeError, match="migrate.py failed"):
        c.run()
    assert not any("runner.py" in x["cmd"] for x in inner.calls)


def test_done_tasks_counts_only_tasks_finished_in_every_arm(tmp_path):
    f = tmp_path / "r.jsonl"
    rows = [("t1", "A", "done"), ("t1", "C", "done"), ("t2", "A", "done"), ("t2", "C", "rate_limited"), ("t3", "A", "hang")]
    f.write_text("\n".join(json.dumps({"task_id": t, "arm": a, "status": s}) for t, a, s in rows) + "\n", encoding="utf-8")
    assert ms.done_tasks(f, ["A", "C"]) == {"t1"} and ms.done_tasks(f, ["A"]) == {"t1", "t2"} and ms.done_tasks(tmp_path / "none", ["A"]) == set()


# ---------------------------------------------------------------- the secrets helper and the command line

@pytest.mark.skipif(ms.modal is None, reason="modal is not installed")
def test_secrets_are_created_from_the_environment_by_name_and_no_value_is_printed(monkeypatch, tmp_path):
    made, said = {}, []
    monkeypatch.setattr(ms, "create_secret", lambda name, values: made.update({name: values}))
    (tmp_path / "e.env").write_text("JEV_API_KEY=jev-secret\nUNRELATED_PRODUCTION_DB=postgres://prod\nZ_AI_KEY=zai-from-file\n", encoding="utf-8")
    env = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "GENERAL_COMPUTE_API_KEY": "gc-secret"}
    rc = ms.secrets_cli(argparse.Namespace(claude=True, api=False, keys=True), environ=env, env_file=tmp_path / "e.env", out=said.append)
    assert rc == 0
    assert made[ms.CLAUDE_SECRET] == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    assert made[ms.KEYS_SECRET] == {"GENERAL_COMPUTE_API_KEY": "gc-secret", "JEV_API_KEY": "jev-secret", "Z_AI_KEY": "zai-from-file"}, \
        "only the named keys: the production database URL in the same file is not shipped"
    text = "\n".join(said)
    for value in (TOKEN, "gc-secret", "jev-secret", "zai-from-file", "postgres://prod"):
        assert value not in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text and "names only" in text and "not found, so not set" in text


@pytest.mark.skipif(ms.modal is None, reason="modal is not installed")
def test_secrets_refuse_to_guess(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "create_secret", lambda *a: pytest.fail("nothing should be created"))
    said = []
    assert ms.secrets_cli(argparse.Namespace(claude=True, api=False, keys=False), environ={}, out=said.append) == 2 and "CLAUDE_CODE_OAUTH_TOKEN" in said[0]
    assert ms.secrets_cli(argparse.Namespace(claude=False, api=False, keys=True), environ={}, env_file=tmp_path / "none.env", out=said.append) == 2
    assert ms.secrets_cli(argparse.Namespace(claude=False, api=False, keys=False), environ={}, out=said.append) == 2


def test_the_plan_command_prints_and_calls_nothing(fake_series, capsys, monkeypatch):
    monkeypatch.setattr(ms, "create_secret", lambda *a: pytest.fail("no Modal call in a plan"))
    assert ms.cli(["plan", "--repos", "mini", "--series-module", "fake_series", "--max-issues", "2", "--auth", "api"]) == 0
    out = capsys.readouterr().out
    assert "mini: 2 issue(s) -> 6 session(s)" in out and "in parallel" in out
    assert ms.cli(["plan", "--repos", "mini", "--series-module", "fake_series", "--arms", "A,Z"]) == 2


def test_a_git_worktree_finds_the_main_checkouts_env_file(tmp_path):
    import subprocess
    main = tmp_path / "StealthLab"
    (main / "backend").mkdir(parents=True)
    subprocess.run(["git", "-C", str(main), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(main), "-c", "user.email=t@t.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    (main / "backend" / ".env").write_text("GENERAL_COMPUTE_API_KEY=gc-from-main"+chr(10), encoding="utf-8")
    wt = tmp_path / "ct4"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt), "-b", "wt"], check=True)
    assert not (wt / "backend" / ".env").exists(), "the ignored .env is not in a worktree"
    files = ms.default_env_files(wt)
    assert (main / "backend" / ".env").resolve() in [f.resolve() for f in files if f.exists()]
    old = ms.default_env_files
    try:
        ms.default_env_files = lambda repo=wt: old(wt)
        assert ms.read_names(("GENERAL_COMPUTE_API_KEY",), {})["GENERAL_COMPUTE_API_KEY"] == "gc-from-main"
    finally:
        ms.default_env_files = old


@pytest.mark.skipif(ms.modal is None, reason="modal is not installed")
def test_a_missing_key_names_the_files_that_were_looked_at(tmp_path):
    said = []
    ns = argparse.Namespace(claude=False, api=False, keys=True, env_file=str(tmp_path / "nope.env"))
    assert ms.secrets_cli(ns, environ={}, out=said.append) == 2
    assert "nope.env (does not exist)" in said[0] and "--env-file" in said[0] and "worktree" in said[0]


@pytest.fixture()
def swe_stub(monkeypatch):
    """A stand-in for the real series module under its real name, so the container treats the series as a SWE one."""
    mod = types.ModuleType("swe_series_task")
    mod.series = lambda name=None: [{"task_id": f"x__{name}-{i}", "suite": "swe", "workspace": f"swe-{name}"} for i in range(1, 7)]
    monkeypatch.setitem(sys.modules, "swe_series_task", mod)
    return mod


def test_the_gold_check_runs_before_any_session_and_a_refused_issue_is_replaced_by_the_next(tmp_path, swe_stub):
    c = container(tmp_path, max_issues=2, series_module="swe_series_task")
    c.rec.bad = {"x__mini-1"}
    out = c.run()
    cmds = [x["cmd"] for x in c.rec.calls]
    assert next(i for i, t in enumerate(cmds) if "validate" in t) < next(i for i, t in enumerate(cmds) if "runner.py" in t)
    checked = [x for x in c.rec.calls if "validate" in x["cmd"]][0]["cmd"].split("validate mini ")[1].split()
    assert checked == ["x__mini-1", "x__mini-2", "x__mini-3", "x__mini-4"], "up to twice as many as the run needs"
    assert [r["task"] for r in out["ran"]] == ["x__mini-2", "x__mini-3"], "the refused first issue is replaced by the next"
    assert out["gold_check"]["usable"] == ["x__mini-2", "x__mini-3"] and "does not apply" in out["gold_check"]["dropped"]["x__mini-1"]
    assert json.loads((c.out / "gold_check.json").read_text(encoding="utf-8"))["checked"] == 4


def test_no_usable_issue_is_an_error_before_any_session(tmp_path, swe_stub):
    c = container(tmp_path, max_issues=1, series_module="swe_series_task")
    c.rec.bad = {f"x__mini-{i}" for i in range(1, 7)}
    out = c.run()
    assert out["status"] == "error" and "gold check" in out["why"] and not any("runner.py" in x["cmd"] for x in c.rec.calls)


def test_the_gold_check_can_be_skipped_and_is_only_for_the_swe_series(tmp_path, swe_stub, fake_series):
    off = container(tmp_path, max_issues=1, series_module="swe_series_task", validate=False)
    off.run()
    assert not any("validate" in x["cmd"] for x in off.rec.calls)
    toy = container(tmp_path / "toy", max_issues=1)                 # fake_series: not the SWE module
    toy.run()
    assert not any("validate" in x["cmd"] for x in toy.rec.calls)
