"""scripts/provision_neon_shards.py against a simulated Neon API (no network, no databases)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "provision_neon_shards", Path(__file__).resolve().parents[1] / "scripts" / "provision_neon_shards.py")
prov = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prov)


class FakeNeon:
    """Just enough of Neon API v2: list, create, connection_uri; one 429 on the first create."""

    def __init__(self, existing: tuple[str, ...] = ()):
        self.projects = {name: {"id": f"id-{name}", "name": name} for name in existing}
        self.creates: list[dict] = []
        self.rate_limited_once = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.method == "GET" and request.url.path == "/api/v2/projects":
            return httpx.Response(200, json={"projects": list(self.projects.values()), "pagination": {}})
        if request.method == "POST" and request.url.path == "/api/v2/projects":
            if not self.rate_limited_once:
                self.rate_limited_once = True
                return httpx.Response(429, json={"message": "slow down"})
            body = json.loads(request.content)["project"]
            self.creates.append(body)
            project = {"id": f"id-{body['name']}", "name": body["name"]}
            self.projects[body["name"]] = project
            return httpx.Response(201, json={"project": project, "connection_uris": [
                {"connection_uri": f"postgresql://owner:pw@{body['name']}.neon.tech/neondb?sslmode=require"}]})
        if request.method == "GET" and request.url.path.endswith("/connection_uri"):
            project_id = request.url.path.split("/")[-2]
            assert request.url.params["pooled"] == "false"          # migrations need the direct endpoint
            return httpx.Response(200, json={"uri": f"postgresql://owner:pw@{project_id}.neon.tech/neondb"})
        return httpx.Response(404)


@pytest.fixture
def neon(monkeypatch):
    fake = FakeNeon(existing=("stealthlab-k002",))
    real = prov.Neon

    def factory(api_key, **kwargs):
        return real(api_key, transport=httpx.MockTransport(fake.handler), **kwargs)

    monkeypatch.setattr(prov, "Neon", factory)
    monkeypatch.setattr(prov.time, "sleep", lambda _s: None)
    monkeypatch.setenv("NEON_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://control.invalid/db")
    return fake


def test_dry_run_changes_nothing(neon, tmp_path, capsys):
    env_file = tmp_path / "shards.env"
    assert prov.main(["--count", "3", "--dry-run", "--env-file", str(env_file)]) == 0
    out = capsys.readouterr().out
    assert "K001  stealthlab-k001" in out and "exists" in out and "dry run" in out
    assert neon.creates == [] and not env_file.exists()


def test_creates_migrates_registers_and_resumes(neon, tmp_path, monkeypatch):
    env_file = tmp_path / "shards.env"
    migrated, registered = [], []
    monkeypatch.setattr(prov, "migrate", lambda dsn: migrated.append(dsn))

    async def fake_register(control_dsn, index, dsn, *, weight, capacity_bytes):
        registered.append((prov.shard_id(index), weight, capacity_bytes))

    monkeypatch.setattr(prov, "register", fake_register)

    assert prov.main(["--count", "3", "--env-file", str(env_file), "--region", "aws-eu-central-1"]) == 0

    # K002 already existed: not re-created, its DSN fetched instead; the 429 was retried
    assert [c["name"] for c in neon.creates] == ["stealthlab-k001", "stealthlab-k003"]
    assert all(c["region_id"] == "aws-eu-central-1" and c["pg_version"] == 16 for c in neon.creates)
    env = prov.read_env_file(env_file)
    assert set(env) == {"K001_DATABASE_URL", "K002_DATABASE_URL", "K003_DATABASE_URL"}
    assert "id-stealthlab-k002" in env["K002_DATABASE_URL"]
    assert len(migrated) == 3
    assert registered == [(f"K00{i}", 100, 500 * 1024 * 1024) for i in (1, 2, 3)]

    # re-running resumes from the env file: no new project, no new DSN lookups needed
    neon.creates.clear()
    assert prov.main(["--count", "3", "--env-file", str(env_file), "--skip-migrate", "--skip-register"]) == 0
    assert neon.creates == []


def test_a_failing_shard_does_not_stop_the_others(neon, tmp_path, monkeypatch):
    env_file = tmp_path / "shards.env"

    def flaky_migrate(dsn):
        if "k001" in dsn:
            raise RuntimeError("connection refused")

    monkeypatch.setattr(prov, "migrate", flaky_migrate)
    assert prov.main(["--count", "3", "--env-file", str(env_file), "--skip-register"]) == 1
    assert set(prov.read_env_file(env_file)) == {"K001_DATABASE_URL", "K002_DATABASE_URL", "K003_DATABASE_URL"}


def test_refuses_without_an_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    assert prov.main(["--count", "1", "--env-file", str(tmp_path / "x.env")]) == 2
