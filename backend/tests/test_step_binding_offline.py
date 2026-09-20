"""Step bindings replace the Implementation object: dispatch, validation vocabulary, artifact gating, and guards that
the retired object stays gone."""
import importlib

import pytest

from app.execution.step_binding import execute_node, execution_spec, executor_kind, validate_invocation
from app.models.plan import PlanNode
from app.services.source_locators import SourceLocatorError, validate_binding, validate_source_artifacts


# ------------------------------------------------------------------ vocabulary
def test_binding_kinds_cover_the_addresses_ingestion_must_accept():
    ok = [
        {"kind": "http_api", "http_api": "x", "endpoint": "https://api.example/v1/run", "env_refs": ["API_KEY"]},
        {"kind": "mcp_tool", "mcp_tool": "github.search", "server_url": "https://mcp.example/mcp"},
        {"kind": "binary", "binary": "lint", "path": "bin/lint"},
        {"kind": "wasm", "wasm": "fmt", "path": "modules/fmt.wasm"},
        {"kind": "container", "container": "checker", "image": "ghcr.io/x/checker:1"},
        {"kind": "model", "model": "gemma", "endpoint": "https://models.example/v1"},
        {"kind": "source_artifact", "source_artifact": "abc", "entrypoint": "scripts/validate.py", "runtime": "python",
         "args": ["--strict"], "sandbox_policy": "isolated"},
    ]
    for b in ok:
        assert validate_binding(b)["kind"] == b["kind"]


def test_bindings_reject_junk_and_unaddressed_kinds():
    bad = [
        {"kind": "binary", "binary": "x"},                                           # no path
        {"kind": "http_api", "http_api": "x", "endpoint": "ftp://nope"},              # not an http(s) endpoint
        {"kind": "source_artifact", "source_artifact": "a", "runtime": "python", "sandbox_policy": "isolated"},  # no entrypoint
        {"kind": "source_artifact", "source_artifact": "a", "entrypoint": "x", "runtime": "python"},             # no sandbox policy
        {"kind": "command", "command": "x", "env_refs": "API_KEY"},                    # env_refs must be a list of names
        {"kind": "command", "command": "x", "password": "hunter2"},                    # unknown key: no credential drawer
    ]
    for b in bad:
        with pytest.raises(SourceLocatorError):
            validate_binding(b)


def test_source_artifact_refs_only_executable_source_may_be_executable():
    assert validate_source_artifacts([{"artifact_id": "a", "path": "scripts/v.py", "role": "executable_source", "execution_allowed": True}])
    style = {"artifact_id": "a", "path": "src/Button.tsx", "role": "style_reference", "execution_allowed": False}
    assert validate_source_artifacts([style]) == [style]
    with pytest.raises(SourceLocatorError):
        validate_source_artifacts([{**style, "execution_allowed": True}])
    with pytest.raises(SourceLocatorError):
        validate_source_artifacts([{"artifact_id": "a", "role": "made_up"}])


# ------------------------------------------------------------------ dispatch
def test_binding_kind_maps_to_executor_kind_and_spec():
    assert executor_kind(None) == "frontier"
    assert executor_kind({"kind": "command", "command": "make"}) == "deterministic"
    assert executor_kind({"kind": "mcp_tool", "mcp_tool": "t", "server_url": "https://s"}) == "tool"
    assert executor_kind({"kind": "http_api", "http_api": "x", "endpoint": "https://e"}) == "api"
    spec = execution_spec({"kind": "mcp_tool", "mcp_tool": "search", "server_url": "https://mcp.example", "parameters": {"q": "x"}})
    assert spec["locator"] == {"server_url": "https://mcp.example"} and spec["invocation"] == {"tool_name": "search", "arguments": {"q": "x"}}
    assert execution_spec({"kind": "http_api", "http_api": "x", "endpoint": "https://e/run"})["locator"] == {"endpoint": "https://e/run"}


def test_credentials_requirement_is_checked_before_dispatch():
    spec = {"requirements": {"network": True, "credentials": ["gh"]}}
    assert len(validate_invocation(spec, {})) == 2
    assert validate_invocation(spec, {"network_access": True, "credentials": ["gh"]}) == []


@pytest.mark.asyncio
async def test_kinds_without_an_executor_fail_loudly_never_substitute():
    node = PlanNode(order=0, goal="run wasm", binding={"kind": "wasm", "wasm": "m", "path": "m.wasm"})
    res = await execute_node(None, node, {})
    assert res.status == "failure" and "no registered provider" in (res.notes or "")


@pytest.mark.asyncio
async def test_source_artifact_is_refused_unless_screened_and_authorised():
    class Pool:
        def __init__(self, row):
            self.row = row

        async def fetchrow(self, *a, **k):
            return self.row

    binding = {"kind": "source_artifact", "source_artifact": "00000000-0000-0000-0000-000000000001", "entrypoint": "s.py",
               "runtime": "python", "sandbox_policy": "isolated"}
    node = PlanNode(order=0, goal="g", binding=binding)
    base = {"id": "x", "content_hash": "h", "role": "executable_source", "execution_allowed": False, "admission_decision": None,
            "content_ref": None, "language": "python"}
    r = await execute_node(Pool(base), node, {})
    assert r.status == "failure" and r.data["error_class"] == "unscreened"                       # preserved, not authorised
    r = await execute_node(Pool({**base, "execution_allowed": True}), node, {})
    assert r.status == "failure" and r.data["error_class"] == "unscreened"                       # authorised but never admitted
    r = await execute_node(Pool({**base, "execution_allowed": True, "admission_decision": "admitted"}), node, {})
    assert "no stored content" in r.notes                                                        # a bare URL is not enough to run
    r = await execute_node(Pool({**base, "role": "style_reference"}), node, {})
    assert "not 'executable_source'" in r.notes
    r = await execute_node(Pool(None), node, {})
    assert "does not exist" in r.notes


def test_plan_node_reads_old_stored_graphs_and_exposes_the_binding_hint():
    old = PlanNode(**{"order": 0, "goal": "x", "implementation_id": "u", "implementation_hint": ["tool"], "task_node_id": "t"})
    assert old.binding is None and old.executor_hint is None                                       # legacy keys are ignored, not fatal
    assert PlanNode(order=0, goal="x", binding={"kind": "command", "command": "make"}).executor_hint == ("deterministic",)


# ------------------------------------------------------------------ the object stays gone
@pytest.mark.parametrize("mod", [
    "app.execution.implementation_registry", "app.execution.implementation_selection", "app.execution.implementation_lifecycle",
    "app.execution.implementation_executor", "app.services.implementation_goals", "app.services.implementation_identification",
    "app.services.procedure_implementations", "app.services.procedure_implementation_bindings",
    "app.services.solution_implementations", "app.services.capabilities", "app.api.implementations",
])
def test_retired_modules_are_gone(mod):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(mod)


def test_no_implementation_mcp_tools_or_routes():
    from app.main import app as fastapi_app
    paths = {getattr(r, "path", "") for r in fastapi_app.routes}
    assert not any("implementation" in p for p in paths), sorted(p for p in paths if "implementation" in p)
    import app.mcp_server.server as srv
    tools = {t for t in dir(srv) if "implementation" in t}
    assert not tools, tools
