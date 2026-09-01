import asyncio

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
    assert names == [
        "apply_change_set",
        "check_applicability",
        "check_procedure",
        "decide_decomposition",
        "decide_procedure",
        "decompose_task",
        "detect_conflict_trigger",
        "find_best_way",
        "get_implementation_capability",
        "get_procedure",
        "inspect_implementation",
        "list_task_implementations",
        "propose_synthesis",
        "report_execution",
        "reproduce_procedure",
        "resolve_implementation",
        "retrieve_precedent",
        "search_procedures",
        "submit_approval",
        "submit_procedure",
    ]


def test_token_verifier_accepts_only_the_configured_token():
    from app.mcp_server.server import StaticTokenVerifier

    verifier = StaticTokenVerifier("correct-horse")
    accepted = asyncio.run(verifier.verify_token("correct-horse"))
    rejected = asyncio.run(verifier.verify_token("wrong-battery"))
    assert accepted is not None
    assert accepted.scopes == ["stealthlab:tools"]
    assert accepted.client_id == "stealthlab-local"
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
