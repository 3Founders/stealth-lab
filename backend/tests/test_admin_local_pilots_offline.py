"""Offline: the step 7 / step 0-1 local pilots are reachable through the admin dispatcher, and only ever write to the
loopback database they validated -- never to the control database the dispatcher normally opens.

Why it matters: `ingest-codemods --apply` needs a pool, and the pilots' own loopback checks look at an ENVIRONMENT
VARIABLE. Handing them the control pool would let a loopback env var authorise writes somewhere else. No database and
no network here: create_pool, the preflight and the pilots' run() are replaced."""
from __future__ import annotations

import argparse
import asyncio

import pytest

from app.ingestion import admin


def _run(coro):
    return asyncio.run(coro)


def _ns(**kw):
    base = dict(cmd="ingest-codemods", apply=False, shard_dsn_env=None)
    base.update(kw)
    return argparse.Namespace(**base)


class _Pool:
    def __init__(self, dsn):
        self.dsn, self.closed = dsn, False

    async def close(self):
        self.closed = True


@pytest.fixture()
def wired(monkeypatch):
    """create_pool records the DSN it was given; the preflight passes; the pilots' run() records the pool they got."""
    seen = {"dsns": [], "pools": [], "preflight": 0}

    async def create_pool(dsn, **kw):
        seen["dsns"].append(dsn)
        pool = _Pool(dsn)
        seen["pools"].append(pool)
        return pool

    async def assert_schema_current(pool, *, command):
        seen["preflight"] += 1

    async def codemod_run(pool, a):
        seen["codemod_pool"] = pool
        return 0

    async def traj_run(pool, a):
        seen["traj_pool"] = pool
        return 0

    import app.db.session as session
    import app.ingestion.codemod_cli as codemod_cli
    import app.ingestion.preflight as preflight
    import app.ingestion.traj_pilot_cli as traj_cli

    monkeypatch.setattr(session, "create_pool", create_pool)
    monkeypatch.setattr(preflight, "assert_schema_current", assert_schema_current)
    monkeypatch.setattr(codemod_cli, "run", codemod_run)
    monkeypatch.setattr(traj_cli, "run", traj_run)
    return seen


def test_both_commands_are_registered_in_the_admin_parser():
    codemods = admin._parse(["ingest-codemods", "--source", "nodejs", "--checkout", "x", "--commit", "abc"])
    assert codemods.cmd == "ingest-codemods" and codemods.no_embed is False
    assert admin._parse(["ingest-codemods", "--source", "nodejs", "--no-embed"]).no_embed is True
    trajectories = admin._parse(["ingest-trajectories", "--shard-dsn-env", "X", "--limit", "5"])
    assert trajectories.cmd == "ingest-trajectories" and trajectories.limit == 5
    assert set(admin.LOCAL_PILOT_COMMANDS) == {"ingest-codemods", "ingest-trajectories"}


def test_a_dry_run_codemod_pass_opens_no_pool_at_all(wired):
    assert _run(admin._run_local_pilot(_ns())) == 0
    assert wired["dsns"] == [] and wired["codemod_pool"] is None


def test_apply_writes_through_a_pool_built_from_the_validated_loopback_dsn(wired, monkeypatch):
    monkeypatch.setenv("PILOT_DSN", "postgresql://u:p@127.0.0.1:5432/scratch")
    monkeypatch.setenv("CONTROL_DATABASE_URL", "postgresql://u:p@prod.example.internal/prod")
    assert _run(admin._run_local_pilot(_ns(apply=True, shard_dsn_env="PILOT_DSN"))) == 0
    assert wired["dsns"] == ["postgresql://u:p@127.0.0.1:5432/scratch"], "the pool comes from the checked DSN"
    assert wired["codemod_pool"] is wired["pools"][0] and wired["pools"][0].closed
    assert wired["preflight"] == 1, "a writing pilot refuses to start on a database with pending migrations"


def test_a_non_loopback_dsn_is_refused_before_any_pool_exists(wired, monkeypatch, capsys):
    monkeypatch.setenv("PILOT_DSN", "postgresql://u:secret@prod.example.internal/prod")
    code = _run(admin._run_local_pilot(_ns(apply=True, shard_dsn_env="PILOT_DSN")))
    out = capsys.readouterr().out
    assert code == 2 and wired["dsns"] == [] and "not loopback" in out
    assert "secret" not in out, "the DSN is never printed"


def test_trajectories_always_write_so_they_always_need_a_loopback_dsn(wired, monkeypatch):
    monkeypatch.setenv("TRAJ_DSN", "postgresql://u:p@localhost:5432/scratch")
    assert _run(admin._run_local_pilot(_ns(cmd="ingest-trajectories", shard_dsn_env="TRAJ_DSN"))) == 0
    assert wired["traj_pool"] is wired["pools"][0]
    monkeypatch.setenv("TRAJ_DSN", "postgresql://u:p@db.example.internal/prod")
    assert _run(admin._run_local_pilot(_ns(cmd="ingest-trajectories", shard_dsn_env="TRAJ_DSN"))) == 2


def test_a_missing_dsn_variable_is_a_clean_error(wired, monkeypatch, capsys):
    monkeypatch.delenv("NOPE_DSN", raising=False)
    assert _run(admin._run_local_pilot(_ns(apply=True, shard_dsn_env="NOPE_DSN"))) == 2
    assert "NOPE_DSN is not set" in capsys.readouterr().out


def test_the_amain_dispatch_reaches_the_local_pilot_before_the_control_pool(monkeypatch):
    called = {}

    async def fake(a):
        called["cmd"] = a.cmd
        return 0

    async def boom(*a, **k):
        raise AssertionError("the control database must not be opened for a local pilot")

    import app.db.session as session

    monkeypatch.setattr(admin, "_run_local_pilot", fake)
    monkeypatch.setattr(session, "create_pool", boom)
    assert _run(admin._amain(_ns(cmd="ingest-trajectories", shard_dsn_env="X"))) == 0 and called["cmd"] == "ingest-trajectories"


# ------------------------------------------------------------------------------------ how --apply writes

class _Artifact:
    def __init__(self, path):
        self.path, self.uri, self.content = path, "uri:" + path, "---\nname: x\n---\n1. step\n"


def test_the_deterministic_path_embeds_by_default_and_no_embed_is_an_explicit_opt_out(monkeypatch):
    """embed=False stored a procedure with NO vector and NO duplicate check: invisible to semantic search."""
    from app.ingestion import codemod_cli

    calls = []

    async def ingest_skill_md(pool, content, **kw):
        calls.append(kw["embed"])
        return {"status": "captured"}

    import app.services.skill_ingestion as skill_ingestion

    monkeypatch.setattr(skill_ingestion, "ingest_skill_md", ingest_skill_md)
    assert _run(codemod_cli._apply(object(), [_Artifact("a")], deterministic=True)) == {"captured": 1}
    _run(codemod_cli._apply(object(), [_Artifact("b")], deterministic=True, embed=False))
    assert calls == [True, False]


@pytest.fixture()
def compiler(monkeypatch):
    """compile_skill_artifact, the budget, and the client are replaced; records what the real call would have seen."""
    import app.services.ingest_budget as ingest_budget
    import app.services.ingestion_jobs as ingestion_jobs
    import app.services.skill_ingestion as skill_ingestion

    seen = {"calls": [], "budget_active_during_call": [], "installed": [], "uninstalled": 0, "statuses": []}

    async def compile_skill_artifact(pool, artifact, **kw):
        seen["calls"].append((artifact.path, kw["created_by"]))
        seen["budget_active_during_call"].append(ingest_budget.active() is not None)
        nxt = seen["statuses"].pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return type("Outcome", (), {"status": nxt})()

    real_install, real_uninstall = ingest_budget.install, ingest_budget.uninstall

    def install(pool, cap_usd=None):
        seen["installed"].append(cap_usd)
        return real_install(pool, cap_usd=cap_usd)

    def uninstall():
        seen["uninstalled"] += 1
        return real_uninstall()

    monkeypatch.setattr(skill_ingestion, "compile_skill_artifact", compile_skill_artifact)
    monkeypatch.setattr(ingestion_jobs, "_general_compute_client", lambda: object())
    monkeypatch.setattr(ingest_budget, "install", install)
    monkeypatch.setattr(ingest_budget, "uninstall", uninstall)
    real_uninstall()                      # start from a clean slate WITHOUT counting it
    yield seen
    real_uninstall()


def test_apply_compiles_through_the_real_compiler_under_an_installed_budget(compiler):
    """The default path: provenance, IngestionContext, content-hash idempotency and a budget-guarded model call. The raw
    ingest_skill_md path had none of them (a re-run duplicated all 39 rows)."""
    from app.ingestion import codemod_cli

    compiler["statuses"] = ["captured", "unchanged", "new_version"]
    out = _run(codemod_cli._apply(object(), [_Artifact("a"), _Artifact("b"), _Artifact("c")], max_usd=2.5))
    assert out == {"captured": 1, "unchanged": 1, "new_version": 1}
    assert [c[1] for c in compiler["calls"]] == ["codemod_ingestion"] * 3
    assert compiler["budget_active_during_call"] == [True, True, True], "the guard only works when a budget is installed"
    assert compiler["installed"] == [2.5] and compiler["uninstalled"] == 1


def test_apply_stops_at_the_budget_and_never_disguises_it_as_an_error(compiler):
    from app.ingestion import codemod_cli
    from app.services.governance import BudgetExceeded

    compiler["statuses"] = ["captured", BudgetExceeded("cap"), "captured"]
    out = _run(codemod_cli._apply(object(), [_Artifact("a"), _Artifact("b"), _Artifact("c")], concurrency=1))
    assert out == {"captured": 1, "budget_exceeded": 1}, "the third artifact is not attempted after the cap"
    assert len(compiler["calls"]) == 2 and compiler["uninstalled"] == 1


def test_one_failing_recipe_is_reported_and_the_rest_continue(compiler):
    from app.ingestion import codemod_cli

    compiler["statuses"] = [RuntimeError("boom"), "captured"]
    assert _run(codemod_cli._apply(object(), [_Artifact("a"), _Artifact("b")])) == {"error": 1, "captured": 1}


def test_recipes_are_compiled_concurrently_but_never_more_than_the_bound(monkeypatch):
    """Compiling 39 recipes one at a time took over two hours: each is several model calls."""
    from app.ingestion import codemod_cli

    import app.services.ingest_budget as ingest_budget
    import app.services.ingestion_jobs as ingestion_jobs
    import app.services.skill_ingestion as skill_ingestion

    live = {"now": 0, "peak": 0}

    async def compile_skill_artifact(pool, artifact, **kw):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.02)
        live["now"] -= 1
        return type("Outcome", (), {"status": "captured"})()

    monkeypatch.setattr(skill_ingestion, "compile_skill_artifact", compile_skill_artifact)
    monkeypatch.setattr(ingestion_jobs, "_general_compute_client", lambda: object())
    ingest_budget.uninstall()
    try:
        out = _run(codemod_cli._apply(object(), [_Artifact(str(i)) for i in range(12)], concurrency=3))
    finally:
        ingest_budget.uninstall()
    assert out == {"captured": 12} and live["peak"] == 3
