"""Offline tests for the operator CLI: Neon idempotency (fake transport), capacity policy, secret rendering, gate, dashboard.
Run:  cd backend && python -m pytest ../scripts/ops/tests -q"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stealth_ops import checks, core, dashboards, deploy, secrets_ops, shardops  # noqa: E402
from stealth_ops.neon import Neon, NeonError  # noqa: E402


class FakeNeon:
    """In-memory Neon API: projects + branches. Records every call so tests can assert what was NOT created."""

    def __init__(self):
        self.projects, self.branches, self.calls, self.pooled = {}, {}, [], False

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/projects?"):
            return {"projects": list(self.projects.values()), "pagination": {}}
        if method == "POST" and path == "/projects":
            pid = f"p{len(self.projects) + 1}"
            self.projects[pid] = {"id": pid, "name": body["project"]["name"]}
            self.branches[pid] = [{"id": f"{pid}-main", "name": "main", "primary": True}]
            return {"project": self.projects[pid]}
        if method == "GET" and path.endswith("/branches"):
            return {"branches": self.branches[path.split("/")[2]]}
        if method == "POST" and path.endswith("/branches"):
            pid = path.split("/")[2]
            b = {"id": f"{pid}-b{len(self.branches[pid])}", "name": body["branch"]["name"]}
            self.branches[pid].append(b)
            return {"branch": b}
        if "/databases" in path:
            return {"databases": [{"name": "neondb", "owner_name": "owner"}]}
        if "/connection_uri" in path:
            return {"uri": "postgresql://owner:pw@ep-x" + ("-pooler" if self.pooled else "") + ".neon.tech/neondb"}
        raise AssertionError(path)


def neon(fake=None):
    fake = fake or FakeNeon()
    return Neon("k", transport=fake), fake


def test_project_creation_is_idempotent_by_name():
    n, f = neon()
    a, created_a = n.ensure_project("K001")
    b, created_b = n.ensure_project("K001")
    assert created_a and not created_b and a["id"] == b["id"]
    assert len(f.projects) == 1 and n.project_name("K000") == "stealth-control" and n.project_name("K007") == "stealth-k007"


def test_ten_shards_twice_creates_ten_projects_not_twenty():
    n, f = neon()
    for _ in range(2):
        for sid in shardops.shard_ids(10):
            n.ensure_project(sid)
    assert len(f.projects) == 10
    assert sum(1 for m, p in f.calls if m == "POST" and p == "/projects") == 10


def test_duplicate_named_projects_refuse_to_guess():
    n, f = neon()
    f.projects = {"a": {"id": "a", "name": "stealth-k001"}, "b": {"id": "b", "name": "stealth-k001"}}
    with pytest.raises(NeonError):
        n.find_project("stealth-k001")


def test_connection_uri_is_direct_and_tls():
    n, f = neon()
    p, _ = n.ensure_project("K001")
    uri = n.connection_uri(p["id"])
    assert "-pooler" not in uri and uri.endswith("sslmode=require")
    f.pooled = True
    with pytest.raises(NeonError):
        n.connection_uri(p["id"])


def test_snapshot_is_idempotent_and_never_deletes():
    n, f = neon()
    p, _ = n.ensure_project("K000")
    b1, c1 = n.snapshot(p["id"], "pre-first-ingest")
    b2, c2 = n.snapshot(p["id"], "pre-first-ingest")
    assert c1 and not c2 and b1["id"] == b2["id"]
    assert not any(m in ("DELETE", "PATCH") or "restore" in path for m, path in f.calls)


REG = [{"shard_id": "K000", "status": "active"}, {"shard_id": "K001", "status": "active"}, {"shard_id": "K002", "status": "active"}]


def probe(sid, size, reachable=True):
    return {"shard_id": sid, "reachable": reachable, "size_bytes": size}


def test_capacity_below_thresholds_does_nothing():
    plan = shardops.capacity_plan([probe("K001", 10), probe("K002", 10)], REG, warn_bytes=50, rollover_bytes=100)
    assert plan["actions"] == [] and plan["warn"] == []


def test_capacity_warning_only():
    plan = shardops.capacity_plan([probe("K001", 60), probe("K002", 10)], REG, warn_bytes=50, rollover_bytes=100)
    assert plan["warn"] == ["K001"] and plan["actions"] == []


def test_rollover_marks_full_but_no_new_shard_when_another_has_room():
    plan = shardops.capacity_plan([probe("K001", 150), probe("K002", 10)], REG, warn_bytes=50, rollover_bytes=100)
    assert plan["actions"] == [{"shard_id": "K001", "do": "mark-full", "size_bytes": 150}]


def test_rollover_provisions_next_shard_when_none_left():
    reg = REG[:2]
    plan = shardops.capacity_plan([probe("K000", 5), probe("K001", 150)], reg, warn_bytes=50, rollover_bytes=100)
    assert [a["do"] for a in plan["actions"]] == ["mark-full", "provision-and-activate"]
    assert plan["actions"][-1]["shard_id"] == "K002"


def test_control_shard_is_never_marked_full_only_weighted_down():
    plan = shardops.capacity_plan([probe("K000", 500), probe("K001", 10)], REG[:2], warn_bytes=50, rollover_bytes=100)
    assert plan["actions"] == [{"shard_id": "K000", "do": "set-weight-0", "size_bytes": 500}]


def test_unconfigured_thresholds_never_act():
    assert shardops.capacity_plan([probe("K001", 10**12)], REG, warn_bytes=0, rollover_bytes=0) == {"configured": False, "actions": []}


def test_unreachable_or_inactive_shards_are_not_rolled_over():
    reg = [{"shard_id": "K001", "status": "unhealthy"}]
    assert shardops.capacity_plan([probe("K001", 10**12)], reg, warn_bytes=1, rollover_bytes=2)["actions"] == []


def test_cloudrun_render_fills_every_token_and_adds_shard_env(monkeypatch):
    monkeypatch.setattr(deploy, "git_sha", lambda: "abc123")
    out = deploy.render_job(image="r-docker.pkg.dev/p/stealth/ingest-worker:abc", env={"DAILY_LLM_BUDGET_USD": "25"},
                            shard_names=[("K001_DATABASE_URL", "stealth-k001-db-url")])
    assert not __import__("re").findall(r"@[A-Z_]+@", out)
    assert "K001_DATABASE_URL" in out and "stealth-k001-db-url" in out and "abc123" in out and '"25"' in out
    assert "postgresql://" not in out          # no credential can end up in the rendered job


def test_manifest_covers_worker_requirements():
    names = {v.env for v in secrets_ops.MANIFEST}
    assert {"CONTROL_DATABASE_URL", "GEMINI_API_KEY", "OBJECT_STORAGE_URL", "INGEST_SERVICE_TOKEN", "SERVICE_TOKEN_KEYS",
            "DAILY_LLM_BUDGET_USD", "OTEL_EXPORTER_OTLP_ENDPOINT", "SENTRY_DSN"} <= names
    assert all(not v.required for v in secrets_ops.MANIFEST if v.env in ("SENTRY_DSN", "OTEL_EXPORTER_OTLP_ENDPOINT"))  # never block ingestion


def test_shard_env_blob_is_names_and_values_lines():
    vs = [secrets_ops.Var("K001_DATABASE_URL", "g"), secrets_ops.Var("K002_DATABASE_URL", "g")]
    assert secrets_ops.shard_env_blob({"K001_DATABASE_URL": "u1", "K002_DATABASE_URL": "u2"}, vs) == "K001_DATABASE_URL=u1\nK002_DATABASE_URL=u2"


def test_dashboard_is_valid_json_and_covers_every_required_area():
    d = json.loads(json.dumps(dashboards.build()))
    titles = " | ".join(p["title"] for p in d["panels"])
    for needle in ("Queue by status", "throughput", "duration", "Failures", "calls", "Judge provider", "Retrievals", "Candidates",
                   "Shards touched", "Shard registry", "Projection lag", "Spend"):
        assert needle.lower() in titles.lower(), needle
    assert all(p["type"] == "row" or p["targets"][0]["rawSql"] for p in d["panels"])


def test_report_exit_code_only_fails_on_blocking_checks():
    r = core.Report("t", json_out=True)
    r.stage("A", lambda: (core.OK, "fine"))
    r.stage("B", lambda: (core.FAIL, "optional thing"), blocking=False)
    assert r.exit_code() == 0
    r.stage("C", lambda: (_ for _ in ()).throw(core.OpsError("boom")))
    assert r.exit_code() == 1 and [c.name for c in r.failed] == ["C"]


def test_secrets_never_reach_argv_or_output():
    assert core.redact("postgresql://u:supersecret@host/db") == "postgresql://u:***@host/db"


def test_promotion_gate_requires_matching_sha(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "OPS_HOME", tmp_path)
    monkeypatch.setattr(checks, "run", lambda *a, **k: core.Proc(0, "sha-1\n", ""))
    assert "no promotion gate" in checks.gate_ok("production")
    core.save_state({"gates": {"production": {"sha": "sha-1", "at": __import__("time").time(), "from": "staging"}}})
    assert checks.gate_ok("production") is None
    monkeypatch.setattr(checks, "run", lambda *a, **k: core.Proc(0, "sha-2\n", ""))
    assert "re-run promote" in checks.gate_ok("production")
