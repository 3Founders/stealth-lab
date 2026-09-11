"""
MCP hardening B25/B27/B28: the literal Adapter contract
(resolve/validate/prepare/invoke/collect_result/collect_artifacts/
collect_evidence/cleanup) against REAL targets -- a real local HTTP
server for HttpApiAdapter (kind='api'), a real local MCP server for
McpToolAdapter (kind='tool'), and the real SubprocessSandboxExecutor
for LocalAdapter (kind='deterministic'). No mocks: every adapter here
talks to a genuinely running server over a real loopback socket.

Also proves the real dispatch integration: `implementation_executor.
execute_implementation` now routes 'deterministic'/'api'/'tool' through
`app.execution.adapters.build_adapter` (the "Adapter Resolver"),
composing the full 8-step lifecycle for real, not a decorative
alternative next to the pre-existing `execute()` path.

Skips itself when DATABASE_URL is unset (execute_implementation's own
integration test needs a real implementations row); the pure-adapter
tests (no DB) run unconditionally.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.server
import json
import os
import threading
import uuid

import pytest

from app.execution.adapters import (
    AdapterResolutionError,
    HttpApiAdapter,
    LocalAdapter,
    McpToolAdapter,
    build_adapter,
)
from app.models.plan import PlanNode

DATABASE_URL = os.environ.get("DATABASE_URL")


def _node(order: int = 0, goal: str = "call the real target") -> PlanNode:
    return PlanNode(order=order, goal=goal)


# ---------------------------------------------------------------------------
# A real local HTTP server (stdlib http.server, a real bound socket, a real
# background thread) -- not a mock, not httpx.MockTransport.
# ---------------------------------------------------------------------------
class _EchoHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path == "/fail":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"synthetic upstream failure")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"echo": json.loads(body)}).encode("utf-8"))

    def log_message(self, format, *args):  # noqa: A002 -- silence stdlib's default stderr logging
        pass


@contextlib.contextmanager
def _real_http_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# A real local MCP server (mcp.server.mcpserver.MCPServer, a real uvicorn
# server on a real loopback socket) with one real tool.
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def _real_mcp_server():
    import uvicorn
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer("adapter-test-server")

    @srv.tool()
    def echo(text: str) -> str:
        return f"echo: {text}"

    @srv.tool()
    def fail_tool() -> str:
        raise RuntimeError("synthetic tool failure")

    app = srv.streamable_http_app(stateless_http=True)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


# ---------------------------------------------------------------------------
# HttpApiAdapter (B27) -- real HTTP against a real local server.
# ---------------------------------------------------------------------------
def test_http_api_adapter_full_lifecycle_against_a_real_server():
    async def _run():
        with _real_http_server() as base_url:
            adapter = HttpApiAdapter()
            implementation = {
                "id": str(uuid.uuid4()), "kind": "api", "version": 3,
                "locator": {"endpoint": f"{base_url}/echo"},
                "invocation": {"headers": {"X-Test": "1"}, "timeout_seconds": 5},
            }
            resolved = await adapter.resolve(implementation)
            assert resolved["endpoint"] == f"{base_url}/echo"

            validation = await adapter.validate(resolved)
            assert validation["valid"] is True

            prepared = await adapter.prepare(resolved, _node(), {"request_body": {"hello": "world"}})
            invocation = await adapter.invoke(prepared)
            assert invocation["status_code"] == 200

            result = await adapter.collect_result(invocation)
            assert result.status == "success"
            body = json.loads(invocation["text"])
            assert body["echo"]["hello"] == "world"

            artifacts = await adapter.collect_artifacts(invocation)
            assert len(artifacts) == 1
            assert artifacts[0]["sha256"] == hashlib.sha256(invocation["text"].encode("utf-8")).hexdigest()

            evidence = await adapter.collect_evidence(invocation)
            assert evidence["outcome_status"] == "success"
            # B27: "Record what Stealth requested, the concrete endpoint" --
            # the real endpoint actually called must be recorded alongside
            # the outcome, not just the response.
            assert evidence["requested_endpoint"] == f"{base_url}/echo"

            await adapter.cleanup(prepared)

            # The full execute() composition, end to end.
            full_result = await adapter.execute(_node(), {"implementation": implementation, "request_body": {"a": 1}})
            assert full_result.status == "success"
            assert full_result.data["artifacts"]
            assert full_result.data["evidence"]["outcome_status"] == "success"
            assert full_result.data["evidence"]["requested_endpoint"] == f"{base_url}/echo"
            # B27: "...the concrete endpoint/tool/version" -- the real
            # implementation version actually used must be recorded too.
            assert full_result.data["evidence"]["implementation_version"] == 3

    asyncio.run(_run())


def test_http_api_adapter_reports_real_upstream_failure():
    async def _run():
        with _real_http_server() as base_url:
            adapter = HttpApiAdapter()
            implementation = {
                "id": str(uuid.uuid4()), "kind": "api",
                "locator": {"endpoint": f"{base_url}/fail"}, "invocation": {},
            }
            result = await adapter.execute(_node(), {"implementation": implementation})
            assert result.status == "failure"
            assert result.data["evidence"]["outcome_status"] == "failure"
            assert result.data["evidence"]["failure_class"] == "external_failure"

    asyncio.run(_run())


def test_http_api_adapter_resolve_refuses_a_row_with_no_endpoint():
    async def _run():
        adapter = HttpApiAdapter()
        with pytest.raises(AdapterResolutionError):
            await adapter.resolve({"id": "x", "kind": "api", "locator": {}, "invocation": {}})

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# McpToolAdapter (B27) -- real MCP protocol against a real local server.
# ---------------------------------------------------------------------------
def test_mcp_tool_adapter_full_lifecycle_against_a_real_server():
    async def _run():
        async with _real_mcp_server() as server_url:
            adapter = McpToolAdapter()
            implementation = {
                "id": str(uuid.uuid4()), "kind": "tool", "version": 2,
                "locator": {"server_url": server_url},
                "invocation": {"tool_name": "echo"},
            }
            resolved = await adapter.resolve(implementation)
            assert resolved["tool_name"] == "echo"

            result = await adapter.execute(
                _node(), {"implementation": implementation, "tool_arguments": {"text": "hi"}},
            )
            assert result.status == "success", result.notes
            assert "echo: hi" in result.data["content_text"]
            assert result.data["artifacts"]
            assert result.data["evidence"]["outcome_status"] == "success"
            # B27: "Record what Stealth requested, the concrete
            # endpoint/tool/version" -- the real server/tool/version
            # actually invoked must be recorded alongside the outcome.
            assert result.data["evidence"]["requested_server_url"] == server_url
            assert result.data["evidence"]["requested_tool_name"] == "echo"
            assert result.data["evidence"]["implementation_version"] == 2

    asyncio.run(_run())


def test_mcp_tool_adapter_reports_a_real_tool_error():
    async def _run():
        async with _real_mcp_server() as server_url:
            adapter = McpToolAdapter()
            implementation = {
                "id": str(uuid.uuid4()), "kind": "tool",
                "locator": {"server_url": server_url},
                "invocation": {"tool_name": "fail_tool"},
            }
            result = await adapter.execute(_node(), {"implementation": implementation})
            assert result.status == "failure"
            assert result.data["evidence"]["outcome_status"] == "failure"

    asyncio.run(_run())


def test_mcp_tool_adapter_resolve_refuses_a_row_missing_tool_name():
    async def _run():
        adapter = McpToolAdapter()
        with pytest.raises(AdapterResolutionError):
            await adapter.resolve({"id": "x", "kind": "tool", "locator": {"server_url": "http://x"}, "invocation": {}})

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# LocalAdapter (B28) -- the literal 8-step lifecycle, real digest check.
# ---------------------------------------------------------------------------
def test_local_adapter_full_lifecycle_with_real_digest_verification():
    async def _run():
        adapter = LocalAdapter()
        code = "with open('output.txt', 'w') as f:\n    f.write('hello from local adapter')\n"
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        implementation = {"id": str(uuid.uuid4()), "kind": "deterministic", "invocation": {"code": code}, "content_hash": digest}

        resolved = await adapter.resolve(implementation)
        validation = await adapter.validate(resolved)
        assert validation["valid"] is True
        assert validation["digest"] == digest

        result = await adapter.execute(_node(), {"implementation": implementation})
        assert result.status == "success"
        assert result.data["output_files"] == {"output.txt": b"hello from local adapter"}
        assert result.data["artifacts"][0]["sha256"] == hashlib.sha256(b"hello from local adapter").hexdigest()
        assert result.data["evidence"]["outcome_status"] == "success"

    asyncio.run(_run())


def test_local_adapter_validate_rejects_a_tampered_digest():
    async def _run():
        adapter = LocalAdapter()
        implementation = {
            "id": str(uuid.uuid4()), "kind": "deterministic",
            "invocation": {"code": "print(1)"}, "content_hash": "0" * 64,
        }
        resolved = await adapter.resolve(implementation)
        validation = await adapter.validate(resolved)
        assert validation["valid"] is False
        assert "digest mismatch" in validation["reason"]

        result = await adapter.execute(_node(), {"implementation": implementation})
        assert result.status == "failure"
        assert "digest mismatch" in result.notes

    asyncio.run(_run())


def test_local_adapter_resolve_refuses_a_row_with_no_code():
    async def _run():
        adapter = LocalAdapter()
        with pytest.raises(AdapterResolutionError):
            await adapter.resolve({"id": "x", "kind": "deterministic", "invocation": {}})

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# build_adapter (the Adapter Resolver) -- kind coverage.
# ---------------------------------------------------------------------------
def test_build_adapter_covers_exactly_the_real_kinds():
    assert isinstance(build_adapter("deterministic"), LocalAdapter)
    assert isinstance(build_adapter("api"), HttpApiAdapter)
    assert isinstance(build_adapter("tool"), McpToolAdapter)
    for kind in ("slm", "human", "wasm", "computer_use", "frontier"):
        assert build_adapter(kind) is None


# ---------------------------------------------------------------------------
# Real dispatch integration: execute_implementation -> build_adapter.
# ---------------------------------------------------------------------------
pytestmark_db = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")


@pytestmark_db
def test_execute_implementation_dispatches_api_kind_through_the_adapter_resolver():
    async def _run():
        from app.db.session import create_pool
        from app.execution import implementation_registry
        from app.execution.implementation_executor import execute_implementation
        from app.services.access import AccessScope

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        impl_id = None
        try:
            with _real_http_server() as base_url:
                impl = await implementation_registry.register(
                    pool, name=f"adapter-dispatch-{uuid.uuid4().hex[:8]}", kind="api",
                    provider="adapter-e2e", created_by="adapter_e2e",
                    locator={"endpoint": f"{base_url}/echo"},
                )
                impl_id = impl["id"]
                node = PlanNode(order=0, goal="dispatch test", implementation_id=impl_id)
                result = await execute_implementation(pool, node, {"request_body": {"k": "v"}}, scope=AccessScope.unrestricted())
                assert result.status == "success", result.notes
                assert result.data["artifacts"]
        finally:
            if impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.close()

    asyncio.run(_run())


@pytestmark_db
def test_record_artifact_fires_through_the_real_durable_run_path():
    """B7's record_artifact(): a real Adapter.execute() composition
    (B25) produces real artifact references, `_node_finish` (durable_run.py)
    extracts them from the SAME dict shape `durable_resume.py::_make_runner`
    real-dispatch wrapping produces ({"data": dict(result.data), ...}),
    and records one durable `artifact_recorded` event per artifact --
    proven end to end, not just at the adapter layer."""
    async def _run():
        from app.db.session import create_pool
        from app.execution import implementation_registry
        from app.execution.durable_run import execute_run, start_run
        from app.execution.implementation_executor import execute_implementation
        from app.execution.recorder import get_run_events
        from app.services.access import AccessScope
        from app.services.procedures import capture_procedure
        from app.utils.ids import uuid7

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        impl_id = None
        row_id = None
        exec_run_id = None
        try:
            with _real_http_server() as base_url:
                impl = await implementation_registry.register(
                    pool, name=f"artifact-dispatch-{uuid.uuid4().hex[:8]}", kind="api",
                    provider="adapter-e2e", created_by="adapter_e2e",
                    locator={"endpoint": f"{base_url}/echo"},
                )
                impl_id = impl["id"]

                res = await capture_procedure(
                    pool, name=f"proc-test-artifact-dispatch-{uuid.uuid4().hex[:8]}",
                    goal="artifact dispatch probe", steps=[{"order": 0, "goal": "call the api"}],
                    provenance="prior_library", scope_type="global", created_by="adapter_e2e",
                    embedding=[0.01] * 1024,
                )
                proc_id, row_id = res["procedure_id"], res["id"]

                async with pool.acquire() as c:
                    pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
                    plan_id = await c.fetchval(
                        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                        " task_description, procedure_content_hash, content_hash, scope_type) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                        str(uuid7()), proc_id, pv, row_id, "artifact-dispatch-e2e",
                        f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
                    )
                    graph_id = await c.fetchval(
                        "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                        "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                        str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
                    )

                exec_run_id = await start_run(
                    pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                    procedure_id=proc_id, procedure_version=pv,
                    node_orders=[0], deps={0: []}, created_by="adapter_e2e",
                )
                await pool.execute(
                    "UPDATE execution_run_nodes SET implementation_id=$2 "
                    "WHERE execution_run_id=$1 AND node_order=0",
                    exec_run_id, impl_id,
                )

                async def run_node(order: int, attempt: int) -> dict:
                    node = PlanNode(order=order, goal="call the api", implementation_id=impl_id)
                    result = await execute_implementation(pool, node, {"request_body": {"probe": True}}, scope=AccessScope.unrestricted())
                    if result.status != "success":
                        raise RuntimeError(result.notes)
                    # The SAME wrapping durable_resume.py::_make_runner
                    # uses for a real NodeResult.
                    return {"notes": result.notes, "data": dict(result.data or {}), "attempt": attempt}

                outcome = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="artifact-dispatch-w1")
                assert outcome["status"] == "succeeded"

                events = await get_run_events(pool, exec_run_id)
                artifact_events = [e for e in events if e["event_type"] == "artifact_recorded"]
                assert len(artifact_events) == 1
                assert artifact_events[0]["node_order"] == 0
                assert artifact_events[0]["payload"]["kind"] == "http_response"
                assert artifact_events[0]["payload"]["ref"].startswith("sha256:")
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                deleted = await pool.execute(
                    "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
                    row_id,
                )
                if deleted == "DELETE 0":
                    await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)
            if impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.close()

    asyncio.run(_run())


@pytestmark_db
def test_externally_hosted_node_records_tool_called_and_result_and_stays_unverified():
    """MCP hardening B27 STRICT CLOSURE: "record what Stealth requested,
    the concrete endpoint/tool/version... returned results... and what
    was independently verified versus merely reported" (V4 B27), read
    together with B26's "Distinguish Stealth-observed execution from
    provider-reported... evidence" / "Never use a permanent verified=true
    as the only verification state".

    Proves, through the SAME real durable_run.execute_run path as
    test_record_artifact_fires_through_the_real_durable_run_path:
      - a real HttpApiAdapter execution durably records `tool_called`
        (endpoint/method/version) and `tool_result` (outcome_status) --
        B8's own pre-existing vocabulary, real call sites for the first
        time.
      - the node's own verification_state is 'unverified', NOT silently
        promoted to 'verified' -- the outcome is the external provider's
        own report (HTTP 200), never something Stealth itself
        independently checked.
    """
    async def _run():
        from app.db.session import create_pool
        from app.execution import implementation_registry
        from app.execution.durable_run import execute_run, start_run
        from app.execution.implementation_executor import execute_implementation
        from app.execution.recorder import get_run_events
        from app.services.access import AccessScope
        from app.services.procedures import capture_procedure
        from app.utils.ids import uuid7

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        impl_id = None
        row_id = None
        exec_run_id = None
        try:
            with _real_http_server() as base_url:
                impl = await implementation_registry.register(
                    pool, name=f"tool-called-dispatch-{uuid.uuid4().hex[:8]}", kind="api",
                    provider="adapter-e2e", created_by="adapter_e2e", version=3,
                    locator={"endpoint": f"{base_url}/echo"},
                )
                impl_id = impl["id"]

                res = await capture_procedure(
                    pool, name=f"proc-test-tool-called-dispatch-{uuid.uuid4().hex[:8]}",
                    goal="tool called dispatch probe", steps=[{"order": 0, "goal": "call the api"}],
                    provenance="prior_library", scope_type="global", created_by="adapter_e2e",
                    embedding=[0.01] * 1024,
                )
                proc_id, row_id = res["procedure_id"], res["id"]

                async with pool.acquire() as c:
                    pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
                    plan_id = await c.fetchval(
                        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                        " task_description, procedure_content_hash, content_hash, scope_type) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                        str(uuid7()), proc_id, pv, row_id, "tool-called-dispatch-e2e",
                        f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
                    )
                    graph_id = await c.fetchval(
                        "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                        "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                        str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
                    )

                exec_run_id = await start_run(
                    pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                    procedure_id=proc_id, procedure_version=pv,
                    node_orders=[0], deps={0: []}, created_by="adapter_e2e",
                )
                await pool.execute(
                    "UPDATE execution_run_nodes SET implementation_id=$2 "
                    "WHERE execution_run_id=$1 AND node_order=0",
                    exec_run_id, impl_id,
                )

                async def run_node(order: int, attempt: int) -> dict:
                    node = PlanNode(order=order, goal="call the api", implementation_id=impl_id)
                    result = await execute_implementation(pool, node, {"request_body": {"probe": True}}, scope=AccessScope.unrestricted())
                    if result.status != "success":
                        raise RuntimeError(result.notes)
                    return {"notes": result.notes, "data": dict(result.data or {}), "attempt": attempt}

                outcome = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="tool-called-dispatch-w1")
                assert outcome["status"] == "succeeded"

                events = await get_run_events(pool, exec_run_id)
                called = [e for e in events if e["event_type"] == "tool_called"]
                resulted = [e for e in events if e["event_type"] == "tool_result"]
                assert len(called) == 1
                assert called[0]["node_order"] == 0
                assert called[0]["payload"]["requested_endpoint"] == f"{base_url}/echo"
                assert called[0]["payload"]["requested_method"] == "POST"
                assert called[0]["payload"]["implementation_version"] == 3
                assert len(resulted) == 1
                assert resulted[0]["payload"]["outcome_status"] == "success"

                node_state = await pool.fetchval(
                    "SELECT verification_state FROM execution_run_nodes "
                    "WHERE execution_run_id=$1 AND node_order=0",
                    exec_run_id,
                )
                assert node_state == "unverified"
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                deleted = await pool.execute(
                    "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
                    row_id,
                )
                if deleted == "DELETE 0":
                    await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)
            if impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.close()

    asyncio.run(_run())


@pytestmark_db
def test_locally_executed_node_still_verifies_since_stealth_observes_it_directly():
    """The counterpoint to the above: LocalAdapter's subprocess sandbox
    runs under Stealth's own direct observation (real exit_code it reads
    itself, no third-party report in between) -- B26's "Stealth-observed
    execution" half. Its NodeResult carries no `requested_endpoint`/
    `requested_server_url` (LocalAdapter's collect_evidence never sets
    them), so `_node_finish` must still mark it 'verified', not silently
    downgrade every successful node."""
    async def _run():
        from app.db.session import create_pool
        from app.execution import implementation_registry
        from app.execution.durable_run import execute_run, start_run
        from app.execution.implementation_executor import execute_implementation
        from app.services.access import AccessScope
        from app.services.procedures import capture_procedure
        from app.utils.ids import uuid7

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        impl_id = None
        row_id = None
        exec_run_id = None
        try:
            impl = await implementation_registry.register(
                pool, name=f"local-verified-dispatch-{uuid.uuid4().hex[:8]}", kind="deterministic",
                provider="adapter-e2e", created_by="adapter_e2e",
                invocation={"code": "print('ok')"},
            )
            impl_id = impl["id"]

            res = await capture_procedure(
                pool, name=f"proc-test-local-verified-dispatch-{uuid.uuid4().hex[:8]}",
                goal="local verified dispatch probe", steps=[{"order": 0, "goal": "run the script"}],
                provenance="prior_library", scope_type="global", created_by="adapter_e2e",
                embedding=[0.01] * 1024,
            )
            proc_id, row_id = res["procedure_id"], res["id"]

            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), proc_id, pv, row_id, "local-verified-dispatch-e2e",
                    f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
                )
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
                )

            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=proc_id, procedure_version=pv,
                node_orders=[0], deps={0: []}, created_by="adapter_e2e",
            )
            await pool.execute(
                "UPDATE execution_run_nodes SET implementation_id=$2 "
                "WHERE execution_run_id=$1 AND node_order=0",
                exec_run_id, impl_id,
            )

            async def run_node(order: int, attempt: int) -> dict:
                node = PlanNode(order=order, goal="run the script", implementation_id=impl_id)
                result = await execute_implementation(pool, node, {}, scope=AccessScope.unrestricted())
                if result.status != "success":
                    raise RuntimeError(result.notes)
                return {"notes": result.notes, "data": dict(result.data or {}), "attempt": attempt}

            outcome = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="local-verified-dispatch-w1")
            assert outcome["status"] == "succeeded"

            node_state = await pool.fetchval(
                "SELECT verification_state FROM execution_run_nodes "
                "WHERE execution_run_id=$1 AND node_order=0",
                exec_run_id,
            )
            assert node_state == "verified"
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                deleted = await pool.execute(
                    "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
                    row_id,
                )
                if deleted == "DELETE 0":
                    await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)
            if impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.close()

    asyncio.run(_run())


@pytestmark_db
def test_implementation_bound_event_fires_through_the_real_resume_run_by_id_path():
    """MCP hardening B8 STRICT CLOSURE: `implementation_bound` was
    declared in EVENT_TYPES but had zero real emission call sites --
    `execution_run_nodes.implementation_id` is never written by any
    production code path (the real binding lives on the compiled
    PlanNode from `bind_plan_implementations`, persisted into
    `task_graphs.nodes`, never copied onto the row). The real, correct
    place to record the fact is the one real dispatch point every
    context-free-resumed node goes through: `durable_resume.py::
    _make_runner`'s own `_run_node`, right before it hands the already-
    bound node to `execute_implementation`. Proven through the real
    public entry point (`resume_run_by_id`), not by calling the
    private runner directly."""
    async def _run():
        from app.db.session import create_pool
        from app.execution import implementation_registry
        from app.execution.durable_resume import resume_run_by_id
        from app.execution.durable_run import start_run
        from app.execution.plan_persistence import persist_compiled_plan
        from app.execution.plans import compile_plan
        from app.execution.recorder import get_run_events
        from app.services.procedures import capture_procedure

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        impl_id = None
        row_id = None
        exec_run_id = None
        try:
            impl = await implementation_registry.register(
                pool, name=f"implbound-dispatch-{uuid.uuid4().hex[:8]}", kind="deterministic",
                provider="adapter-e2e", created_by="adapter_e2e",
                invocation={"code": "print('ok')"},
            )
            impl_id = impl["id"]

            res = await capture_procedure(
                pool, name=f"proc-test-implbound-dispatch-{uuid.uuid4().hex[:8]}",
                goal="implementation bound dispatch probe", steps=[{"order": 0, "goal": "run it"}],
                provenance="prior_library", scope_type="global", created_by="adapter_e2e",
                embedding=[0.01] * 1024,
            )
            proc_id, row_id = res["procedure_id"], res["id"]
            pv = await pool.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)

            compiled = compile_plan(
                procedure_id=proc_id, procedure_version=pv, procedure_row_id=row_id,
                procedure_payload=res, task_description="implbound-dispatch-e2e",
                nodes=[PlanNode(order=0, goal="run it", implementation_id=impl_id)],
                extractor_version="test_adapters_e2e@1", created_by="adapter_e2e",
            )
            compiled, _ = await persist_compiled_plan(pool, compiled)

            exec_run_id = await start_run(
                pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
                procedure_id=proc_id, procedure_version=pv,
                node_orders=[0], deps={0: []}, created_by="adapter_e2e",
            )

            outcome = await resume_run_by_id(
                pool, exec_run_id, worker_id="implbound-dispatch-w1", actor_id=None,
            )
            assert outcome["status"] == "succeeded", outcome

            events = await get_run_events(pool, exec_run_id)
            bound = [e for e in events if e["event_type"] == "implementation_bound"]
            assert len(bound) == 1
            assert bound[0]["node_order"] == 0
            assert bound[0]["payload"]["implementation_id"] == str(impl_id)
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                deleted = await pool.execute(
                    "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
                    row_id,
                )
                if deleted == "DELETE 0":
                    await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)
            if impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.close()

    asyncio.run(_run())
