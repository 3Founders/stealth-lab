import asyncio
import os

import pytest

pytest.importorskip("mcp")
pytest.importorskip("fastapi")

import stealthlab_connect as slc


@pytest.fixture(autouse=True)
def _server_import_env(monkeypatch):
    monkeypatch.setenv("STEALTHLAB_MCP_TOKEN", "offline-suite-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://offline:offline@127.0.0.1:1/offline")


def test_server_module_imports_without_a_database():
    module = slc.load_mcp_server_module()
    assert module.server.name == "stealthlab"
    assert module.server.version == "1.0.0"
    assert module.app is not None


def test_all_registered_tools():
    module = slc.load_mcp_server_module()
    names = sorted(tool.name for tool in module.server._tool_manager.list_tools())
    assert names == ["find_ways", "recommend_models", "report_discovery", "report_model_run", "submit_way"]


def test_token_verifier_accepts_only_the_configured_token():
    from app.mcp_server.server import OidcAwareTokenVerifier

    # oidc_config=None matches today's actual default posture (OIDC_ISSUER/
    # OIDC_AUDIENCE unset) -- shared-secret-only, same behaviour the old
    # StaticTokenVerifier this class replaced always had.
    verifier = OidcAwareTokenVerifier("correct-horse", oidc_config=None, jwks_provider=None)
    accepted = asyncio.run(verifier.verify_token("correct-horse"))
    rejected = asyncio.run(verifier.verify_token("wrong-battery"))
    assert accepted is not None
    assert accepted.scopes == ["stealthlab:tools"]
    assert accepted.client_id == "stealthlab-local"
    assert accepted.subject is None
    assert rejected is None


def test_retrieve_threshold_is_the_documented_scoped_override():
    module = slc.load_mcp_server_module()
    assert module.RETRIEVE_PRECEDENT_THRESHOLD == 0.60


def test_server_entry_preflight_and_bad_root(monkeypatch, tmp_path):
    from stealthlab_connect import server_entry

    monkeypatch.delenv("STEALTHLAB_MCP_TOKEN", raising=False)
    problems = server_entry.preflight_http()
    assert problems and "STEALTHLAB_MCP_TOKEN" in problems[0]

    monkeypatch.setenv("STEALTHLAB_MCP_TOKEN", "tok")
    assert server_entry.preflight_http() == []

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert server_entry.main(["--backend-root", str(empty_dir)]) == 1


def test_server_entry_loads_shard_connection_strings(monkeypatch, tmp_path):
    """Storage layout v2: K###/S### connection strings live in backend/.neon_shards.env, not backend/.env."""
    from stealthlab_connect import server_entry

    for name in ("K001_DATABASE_URL", "S001_DATABASE_URL", "STEALTH_SHARDS_ENV_FILE", "FROM_DOTENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("S001_DATABASE_URL", "postgresql://already-set/db")
    (tmp_path / ".env").write_text("FROM_DOTENV=1\n", encoding="utf-8")
    (tmp_path / ".neon_shards.env").write_text(
        "K001_DATABASE_URL=postgresql://k1/db\nS001_DATABASE_URL=postgresql://from-file/db\n", encoding="utf-8")
    assert server_entry.load_backend_dotenv(tmp_path) is True
    assert os.environ["FROM_DOTENV"] == "1"
    assert os.environ["K001_DATABASE_URL"] == "postgresql://k1/db"
    assert os.environ["S001_DATABASE_URL"] == "postgresql://already-set/db"     # the environment wins
    assert os.environ["STEALTH_SHARDS_ENV_FILE"] == str(tmp_path / ".neon_shards.env")
    assert server_entry.load_shards_env(tmp_path) == 2
