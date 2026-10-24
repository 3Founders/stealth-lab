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
# the keys stealthlab-keys holds for arm C's server: a judge (JEV) and the codebook's embeddings (OpenRouter)
SERVER_KEYS = {"JEV_BASE_URL": "https://jev.example", "JEV_API_KEY": "jev-key", "OPENROUTER_API_KEY": "or-key"}
ENV_SUB = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "ANTHROPIC_API_KEY": "stale-key", "PATH": "p", **SERVER_KEYS}
ENV_API = {"ANTHROPIC_API_KEY": "api-key-value", "CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "PATH": "p", **SERVER_KEYS}


@pytest.fixture()
def fake_series(monkeypatch):
    mod = types.ModuleType("fake_series")
    mod.series = lambda name=None: [{"task_id": f"x__{name}-{i}", "suite": "swe", "workspace": f"swe-{name}"} for i in range(1, 5)]
    monkeypatch.setitem(sys.modules, "fake_series", mod)
    return mod


def spec(**kw):
    return ms.RunSpec(**{"repos": ["mini"], "series_module": "fake_series", "arms": ["A", "C", "C0"], **kw})


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
    assert f"{ms.KEYS_SECRET} + {ms.CLAUDE_SECRET}" in text and "/mnt/series/long-mini-1t/<repository>/" in text
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
    assert hasattr(ms.run_unit, "remote") and hasattr(ms.run_unit, "starmap"), "the function is registered on the app"
    assert ms.MODAL_AUTH == "subscription", "only the subscription's secret is attached unless ABTEST_MODAL_AUTH=api"
    assert ms.app.name == ms.APP_NAME


# ---------------------------------------------------------------- the container, with a recorder instead of a shell

class Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Recorder:
    """Stand-in for the shell: records every command, plays the runner (writes result rows) and pg_dump (writes the dump)."""

    def __init__(self, container_ref, rc_for=None):
        self.c, self.calls, self.rc_for, self.bad = container_ref, [], rc_for or {}, set()

    def __call__(self, argv, *, env=None, cwd=None, timeout=None, background=False, log=None):
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
        if "pg_tables" in text and "-At" in text:
            return Proc(0, out=chr(10).join([*ms.ROUTER_TABLES, "goals", "knowledge_nodes"]))
        if "pg_dump" in text:
            (c.out / ("db_baseline.sql.gz" if "--data-only" in text else "db.sql.gz")).write_bytes(b"dump")
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
    backends = [x for x in c.rec.calls if "run_backend.py" in x["cmd"]]
    assert [x["background"] for x in backends] == [True, True], "the backend and the router's worker run in the background"
    assert "--worker" not in backends[0]["cmd"] and backends[1]["cmd"].endswith("run_backend.py --worker"), "the worker starts once the server is up"
    assert "the router's worker is running" in (c.out / "providers.txt").read_text(encoding="utf-8")
    runs = [x for x in c.rec.calls if "runner.py" in x["cmd"]]
    assert [r["cmd"].split("--only ")[1].split()[0] for r in runs] == ["x__mini-1", "x__mini-2"], "max_issues cuts the series"
    first = runs[0]["cmd"]
    for part in ("--run long-mini-3t", "--series fake_series:mini", "--arms A,C,C0", "--parallel", "--testfirst-model glm-5.3",
                 "--turns 3", "--gc-key-name GENERAL_COMPUTE_API_KEY"):
        assert part in first, part
    assert out["status"] == "done" and out["ran"] == [{"task": "x__mini-1", "rc": 0}, {"task": "x__mini-2", "rc": 0}] and out["report"] == "summary\n\ncurve"
    assert (c.out / "report.txt").read_text(encoding="utf-8") == "summary\n\ncurve"
    assert len(c.commits) >= 3, "the volume is committed after every task and the report"


def test_the_backend_gets_a_fresh_mcp_token_the_runner_does_not(tmp_path, fake_series):
    c = container(tmp_path, max_issues=1)
    c.run()
    backend = next(x for x in c.rec.calls if "run_backend.py" in x["cmd"])
    assert len(backend["env"]["STEALTHLAB_MCP_TOKEN"]) >= 32 and backend["env"]["ABTEST_DATABASE_URL"].startswith("postgresql://kel_owner@127.0.0.1")
    runner_env = next(x["env"] for x in c.rec.calls if "runner.py" in x["cmd"])
    assert "STEALTHLAB_MCP_TOKEN" not in runner_env


def test_a_single_turn_run_does_not_pass_turns(tmp_path, fake_series):
    c = container(tmp_path, max_issues=1)
    c.run()
    assert "--turns" not in next(x["cmd"] for x in c.rec.calls if "runner.py" in x["cmd"])


def test_the_container_builds_with_the_default_string_paths(fake_series):
    c = ms.Container(spec(), "mini", sh=lambda *a, **k: None, environ=ENV_SUB)
    assert c.ab == Path("/app/experiments/abtest") and c.out == Path("/mnt/series/long-mini-1t/mini")


def test_the_caches_are_on_the_volume_and_set_at_run_time_not_in_the_image(tmp_path, fake_series):
    c = container(tmp_path, max_issues=1)
    assert c.environ["HF_HOME"] == str(tmp_path / "vol" / "cache" / "hf")
    assert "UV_CACHE_DIR" not in c.environ, "uv cannot use a Volume for its cache (Modal Volumes refuse its temp-file rename)"


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


def test_a_backend_that_dies_is_reported_with_the_end_of_its_output(tmp_path, fake_series):
    class Dead:
        def poll(self):
            return 1

    c = container(tmp_path, max_issues=1)
    c.health = lambda: False
    inner = c.sh

    def sh(argv, **kw):
        if kw.get("background"):
            kw["log"].write_text("ModuleNotFoundError: No module named 'jwt'"+chr(10), encoding="utf-8")
            return Dead()
        return inner(argv, **kw)

    c.sh = sh
    with pytest.raises(RuntimeError, match="No module named 'jwt'"):
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
    assert ms.cli(["plan", "--repos", "mini", "--series-module", "fake_series", "--max-issues", "2", "--auth", "api", "--arms", "A,C,C0"]) == 0
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


# ---------------------------------------------------------------- split arms: one gold check, one container per arm, one merged report

def unit(tmp_path, arm, ids, **kw):
    """The one-arm container of a split run (what orchestrate hands to run_unit)."""
    return container(tmp_path, arms=[arm], unit_arm=arm, task_ids=ids, **kw)


def commands(c):
    return [x["cmd"] for x in c.rec.calls]


def test_a_split_plan_says_so_and_sequential_arms_says_the_old_thing(fake_series):
    tasks = {"mini": ms.series_tasks(spec(max_issues=2), "mini")}
    text = ms.plan_text(spec(max_issues=2), tasks)
    assert "the arms run at the same time, one container per arm" in text and "ONE gold check" in text and "do not share server state" in text
    assert "<arm>/results" in text
    old = ms.plan_text(spec(max_issues=2, split_arms=False), tasks)
    assert "one after another inside a repository" in old and "<arm>" not in old
    one = ms.plan_text(spec(max_issues=2, arms=["A"]), tasks)
    assert "one container per arm" in one, "even one arm gets its own directory, so a later run can add the other arms to the same run"


def test_arm_specs_are_one_arm_each_with_the_same_gold_checked_issues():
    specs = ms.arm_specs(spec(), ["a", "b"])
    assert [s.arms for s in specs] == [["A"], ["C"], ["C0"]] and [s.unit_arm for s in specs] == ["A", "C", "C0"]
    assert all(s.task_ids == ["a", "b"] for s in specs)
    assert ms.validate_spec(specs[0]) == []
    assert any("one-arm unit" in p for p in ms.validate_spec(spec(unit_arm="A")))


def orch(spec_, **fns):
    calls = []

    def prepare(d, r):
        calls.append(("prepare", r))
        return {"task_ids": [f"x__{r}-1", f"x__{r}-2"], "gold_check": {"usable": 2}}

    def run_arms(units):
        calls.append(("run_arms", [(d["unit_arm"] or "-", r) for d, r in units]))
        return [{"arm": d["unit_arm"], "status": "done"} for d, r in units]

    def finish(d, r):
        calls.append(("finish", r))
        return {"repo": r, "report": "rep"}

    def one(d, r):
        calls.append(("one", r))
        return {"repo": r, "status": "done"}

    out = ms.orchestrate(spec_, prepare=prepare, run_arms=run_arms, finish=finish, run_one_container=one)
    return out, calls


def test_a_split_run_with_a_subscription_goes_repository_by_repository_with_the_arms_at_once():
    out, calls = orch(ms.RunSpec(repos=["a", "b"], arms=["A", "C", "C0"]))
    assert calls == [("prepare", "a"), ("prepare", "b"), ("run_arms", [("A", "a"), ("C", "a"), ("C0", "a")]), ("finish", "a"),
                     ("run_arms", [("A", "b"), ("C", "b"), ("C0", "b")]), ("finish", "b")]
    assert [o["repo"] for o in out] == ["a", "b"] and out[0]["gold_check"] == {"usable": 2} and [x["arm"] for x in out[0]["arms"]] == ["A", "C", "C0"]


def test_a_split_run_with_an_api_key_starts_every_repositorys_arms_together():
    _, calls = orch(ms.RunSpec(repos=["a", "b"], auth="api", arms=["A", "C", "C0"]))
    assert calls[0:2] == [("prepare", "a"), ("prepare", "b")]
    assert calls[2] == ("run_arms", [("A", "a"), ("C", "a"), ("C0", "a"), ("A", "b"), ("C", "b"), ("C0", "b")]) and calls[3:] == [("finish", "a"), ("finish", "b")]


def test_without_splitting_one_container_does_everything_as_before():
    out, calls = orch(ms.RunSpec(repos=["a", "b"], split_arms=False))
    assert calls == [("one", "a"), ("one", "b")] and [o["repo"] for o in out] == ["a", "b"]
    _, calls = orch(ms.RunSpec(repos=["a"], arms=["A"]))
    assert calls == [("prepare", "a"), ("run_arms", [("A", "a")]), ("finish", "a")], "one arm is still a unit: its results can be joined by later arms"
    _, calls = orch(ms.RunSpec(repos=["a", "b"], split_arms=False, auth="api"))
    assert calls == [("run_arms", [("-", "a"), ("-", "b")])]


def test_arm_a_runs_without_postgres_or_a_backend_on_the_issues_it_is_given(tmp_path, fake_series):
    c = unit(tmp_path, "A", ["x__mini-3", "x__mini-1"])
    out = c.run()
    cmds = commands(c)
    assert not any(k in t for t in cmds for k in ("initdb", "pg_ctl", "createuser", "migrate.py", "run_backend.py", "pg_dump")), cmds
    runs = [t for t in cmds if "runner.py" in t]
    assert [r.split("--only ")[1].split()[0] for r in runs] == ["x__mini-1", "x__mini-3"], "the given issues, in series order"
    assert all("--arms A " in r for r in runs) and all("--series fake_series:mini" in r for r in runs)
    assert out["status"] == "done" and "report" not in out, "a split run's report is made once, by finish"
    assert (c.out / "state.tgz").exists() and c.out == tmp_path / "vol" / "long-mini-1t" / "mini" / "A" and not (c.out / "db.sql.gz").exists()


def test_arm_c_starts_its_own_backend_and_keeps_its_snapshot_in_its_own_directory(tmp_path, fake_series):
    c = unit(tmp_path, "C", ["x__mini-1"])
    c.run()
    cmds = commands(c)
    assert any("initdb" in t for t in cmds) and any("run_backend.py" in t for t in cmds) and any("pg_dump" in t for t in cmds)
    assert (c.out / "db.sql.gz").exists() and c.out.name == "C"
    assert all("--arms C " in t for t in cmds if "runner.py" in t)


def test_a_unit_never_runs_the_gold_check_itself_even_for_the_swe_series(tmp_path, swe_stub):
    c = unit(tmp_path, "A", ["x__mini-2"], series_module="swe_series_task")
    c.run()
    assert not any("validate" in t for t in commands(c))
    assert [t.split("--only ")[1].split()[0] for t in commands(c) if "runner.py" in t] == ["x__mini-2"]


def test_prepare_runs_the_gold_check_once_with_no_services_and_remembers_the_answer(tmp_path, swe_stub):
    c = container(tmp_path, max_issues=2, series_module="swe_series_task")
    c.rec.bad = {"x__mini-1"}
    got = c.prepare()
    cmds = commands(c)
    assert got["task_ids"] == ["x__mini-2", "x__mini-3"] and got["reused"] is False
    assert any("fetch mini" in t for t in cmds) and any("validate" in t for t in cmds)
    assert not any(k in t for t in cmds for k in ("initdb", "run_backend.py", "runner.py")), "no Postgres, no backend, no session"
    rec = json.loads((tmp_path / "vol" / "long-mini-1t" / "mini" / "gold_check.json").read_text(encoding="utf-8"))
    assert rec["usable"] == ["x__mini-2", "x__mini-3"] and rec["max_issues"] == 2 and "x__mini-1" in rec["dropped"]
    again = container(tmp_path, max_issues=2, series_module="swe_series_task")
    got2 = again.prepare()
    assert got2["task_ids"] == got["task_ids"] and got2["reused"] is True and commands(again) == [], "a repeated command reuses the answer"
    more = container(tmp_path, max_issues=3, series_module="swe_series_task")
    assert more.prepare()["reused"] is False, "a different --max-issues is a different question"


def test_prepare_for_a_series_without_a_gold_check_just_takes_the_first_issues(tmp_path, fake_series):
    c = container(tmp_path, max_issues=2)
    got = c.prepare()
    assert got["task_ids"] == ["x__mini-1", "x__mini-2"] and not any("validate" in t for t in commands(c))


def test_finish_merges_every_arms_results_and_reports_over_all_of_them(tmp_path, fake_series):
    c = container(tmp_path)
    for arm, n in (("A", 2), ("C", 2)):                       # C0's container produced nothing
        d = c.run_dir / arm / "results"
        d.mkdir(parents=True)
        (d / "long-mini-1t.jsonl").write_text("".join(json.dumps({"task_id": f"t{i}", "arm": arm, "status": "done"}) + chr(10) for i in range(n)), encoding="utf-8")
    out = c.finish()
    merged = (c.run_dir / "results" / "long-mini-1t.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(merged) == 4 and out["rows"] == 4 and out["arms_without_results"] == ["C0"] and out["arms"] == ["A", "C"]
    reports = [x for x in c.rec.calls if "report.py" in x["cmd"]]
    assert len(reports) == 2 and "--curve" in reports[1]["cmd"] and all("long-mini-1t" in x["cmd"] for x in reports)
    assert all(x["env"]["ABTEST_RESULTS_DIR"] == str(c.run_dir / "results") for x in reports)
    assert out["report"] == "summary" + chr(10) + chr(10) + "curve" and (c.run_dir / "report.txt").read_text(encoding="utf-8") == out["report"]
    assert c.commits, "the volume is committed"


def test_the_container_mains_for_prepare_and_finish_build_from_plain_dicts(tmp_path, fake_series, monkeypatch):
    seen = {}
    monkeypatch.setattr(ms.Container, "prepare", lambda self: seen.update(prepare=self.repo) or {"task_ids": []})
    monkeypatch.setattr(ms.Container, "finish", lambda self: seen.update(finish=self.repo) or {"report": ""})
    monkeypatch.setattr(ms, "auth_env", lambda mode, environ: dict(environ))
    d = {"repos": ["mini"], "series_module": "fake_series"}
    ms.prepare_main(d, "mini")
    reloaded = []
    ms.finish_main(d, "mini", reload=lambda: reloaded.append(1))
    assert seen == {"prepare": "mini", "finish": "mini"} and reloaded == [1], "finish reloads the volume to see the other containers' commits"


def test_the_gold_checks_environment_findings_reach_the_arms_containers(tmp_path, swe_stub):
    prep = container(tmp_path, max_issues=2, series_module="swe_series_task")
    prep.prepare()
    shared = tmp_path / "vol" / "long-mini-1t" / "mini" / "validate.json"
    assert shared.exists(), "prepare keeps validate_<series>.json (the PASS_TO_PASS exclusions) on the volume"
    expected = shared.read_text(encoding="utf-8")
    arm = unit(tmp_path / "other", "A", ["x__mini-2"], series_module="swe_series_task")
    arm.vol = tmp_path / "vol"                                   # the same volume, a different container (empty local disk)
    arm.run_dir = arm.vol / "long-mini-1t" / "mini"
    arm.out = arm.run_dir / "A"
    arm.run()
    assert (arm.local / "swe" / "validate_mini.json").read_text(encoding="utf-8") == expected


def test_arm_a_alone_needs_no_test_server_token_but_c_and_c0_do(tmp_path, monkeypatch):
    import local_identity
    monkeypatch.setattr(local_identity, "paths", lambda: {"token": tmp_path / "token.txt"})
    assert runner.read_token(["A"]) == "", "a split run's A container starts no server, so there is no token file"
    for arms in (["C"], ["A", "C0"]):
        with pytest.raises(SystemExit, match="start the test server"):
            runner.read_token(arms)
    (tmp_path / "token.txt").write_text("tok" + chr(10), encoding="utf-8")
    assert runner.read_token(["C"]) == "tok" and runner.read_token(["A"]) == "tok"


def test_a_later_run_with_other_arms_joins_the_earlier_arms_results_in_the_report(tmp_path, fake_series):
    first = container(tmp_path, arms=["A"])
    d = first.run_dir / "A" / "results"
    d.mkdir(parents=True)
    (d / "long-mini-1t.jsonl").write_text(json.dumps({"task_id": "t0", "arm": "A", "status": "done"}) + chr(10), encoding="utf-8")
    later = container(tmp_path, arms=["C", "C0"])                  # same run name, same volume: only C and C0 were asked for
    for arm in ("C", "C0"):
        e = later.run_dir / arm / "results"
        e.mkdir(parents=True)
        (e / "long-mini-1t.jsonl").write_text(json.dumps({"task_id": "t0", "arm": arm, "status": "done"}) + chr(10), encoding="utf-8")
    out = later.finish()
    assert out["arms"] == ["A", "C0", "C"] and out["rows"] == 3 and out["arms_without_results"] == []


@pytest.mark.skipif(ms.modal is None, reason="modal is not installed")
def test_create_secret_replaces_an_existing_secret_instead_of_keeping_the_old_values(monkeypatch):
    calls = []

    class FakeObjects:
        def delete(self, name, allow_missing=False, **kw):
            calls.append(("delete", name, allow_missing))

        def create(self, name, values, **kw):
            assert "allow_existing" not in kw, "allow_existing=True keeps the old values: the update would be lost"
            calls.append(("create", name, dict(values)))

    monkeypatch.setattr(ms.modal.Secret, "objects", FakeObjects())
    ms.create_secret("stealthlab-keys", {"Z_AI_KEY": "new"})
    assert calls == [("delete", "stealthlab-keys", True), ("create", "stealthlab-keys", {"Z_AI_KEY": "new"})]


def test_the_test_server_embeds_in_the_routing_codebooks_space():
    # backend/.env never reaches the image: without these the server used gemini-embedding-001 and refused every code (409)
    assert ms.codebook_embedding_env({"OPENROUTER_API_KEY": "k"}) == {"EMBEDDING_PROVIDER_CHAIN": "openrouter",
                                                                      "GEMINI_EMBEDDING_MODEL": "gemini-embedding-2"}
    assert ms.codebook_embedding_env({"GEMINI_API_KEY": "k"})["EMBEDDING_PROVIDER_CHAIN"] == "gemini"
    assert ms.codebook_embedding_env({"EMBEDDING_PROVIDER_CHAIN": "voyage", "OPENROUTER_API_KEY": "k"}) == {}, "a given setting wins"
    assert ms.codebook_embedding_env({}) == {}


def test_the_provider_report_says_which_judge_and_embeddings_the_server_gets():
    env = {"JEV_BASE_URL": "u", "JEV_API_KEY": "k", **ms.codebook_embedding_env({"OPENROUTER_API_KEY": "k"}), "OPENROUTER_API_KEY": "k"}
    lines = ms.provider_report(env)
    assert "embeddings openrouter (gemini-embedding-2); semantic judge jev" in lines[0] and len(lines) == 1
    lines = ms.provider_report({"GEMINI_API_KEY": "k", **ms.codebook_embedding_env({"GEMINI_API_KEY": "k"})})
    assert "semantic judge gemini" in lines[0] and any("JEV is not configured" in l for l in lines)
    lines = ms.provider_report({})
    assert any(l.startswith("WARNING: no semantic judge") for l in lines) and any("routing codes are refused" in l for l in lines)


def test_arm_c_is_refused_when_its_server_has_no_judge_or_no_embeddings(tmp_path, fake_series):
    with pytest.raises(RuntimeError, match="not set up for arm C"):
        container(tmp_path / "nokeys", environ={"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "PATH": "p"}).run()


def test_a_conversation_runs_openers_are_written_once_by_prepare_for_every_arm(tmp_path, fake_series):
    c = container(tmp_path, max_issues=2, turns=3)
    c.prepare()
    made = [t for t in commands(c) if "make_openers.py" in t]
    assert len(made) == 1 and made[0].endswith("x__mini-1 x__mini-2"), made
    assert str(c.run_dir / "openers") in made[0] and f" {c.spec.gc_key_name} " in made[0]
    again = container(tmp_path, max_issues=2, turns=3)
    again.prepare()
    assert any("fetch mini" in t for t in commands(again)) and any("make_openers.py" in t for t in commands(again)), \
        "a reused gold check still has its openers made (cached per issue), from the fetched issues"
    one = container(tmp_path / "one", max_issues=2)
    one.prepare()
    assert not any("make_openers.py" in t for t in commands(one)), "a one-turn run has no opener"


def test_a_unit_starts_from_its_own_state_only_and_takes_the_runs_shared_openers(tmp_path, fake_series):
    c = unit(tmp_path, "C", ["x__mini-1"], turns=3)
    (c.local / "logs").mkdir(parents=True, exist_ok=True)
    (c.local / "logs" / "hooks.log").write_text("another unit's lines\n", encoding="utf-8")   # a reused container
    (c.local / "openers").mkdir(parents=True, exist_ok=True)
    (c.local / "openers" / "x__mini-1.json").write_text('{"opener": "this container\'s own"}', encoding="utf-8")
    shared = c.run_dir / "openers"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "x__mini-1.json").write_text('{"opener": "the run\'s"}', encoding="utf-8")
    c.run()
    assert "another unit's lines" not in ((c.local / "logs" / "hooks.log").read_text(encoding="utf-8")
                                          if (c.local / "logs" / "hooks.log").exists() else "")
    assert json.loads((c.local / "openers" / "x__mini-1.json").read_text(encoding="utf-8"))["opener"] == "the run's"
