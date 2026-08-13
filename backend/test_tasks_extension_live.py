"""
Real test of TasksExtension -- exercises the actual, installed
mcp.server.extension.compose_tool_call_handler machinery (not a
reimplementation), with a fake slow "tool" standing in for
propose_synthesis/solve_task's real long-running work.
"""
import asyncio
import sys
from dataclasses import dataclass

sys.path.insert(0, ".")

from mcp_types import CallToolRequestParams, CLIENT_CAPABILITIES_META_KEY
from mcp.server.context import ServerRequestContext
from mcp.server.extension import compose_tool_call_handler

from app.mcp_server.tasks_extension import TasksExtension, CreateTaskResult


class FakeSession:
    pass


def make_ctx(*, declares_tasks: bool) -> ServerRequestContext:
    meta = None
    if declares_tasks:
        meta = {CLIENT_CAPABILITIES_META_KEY: {"extensions": {"io.modelcontextprotocol/tasks": {}}}}
    return ServerRequestContext(
        session=FakeSession(),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tools/call",
        meta=meta,
    )


async def slow_tool_handler(ctx, params):
    """Stands in for propose_synthesis/solve_task's real, slow work."""
    await asyncio.sleep(0.3)
    from mcp_types import CallToolResult
    from mcp_types._types import TextContent
    return CallToolResult(content=[TextContent(type="text", text="real slow result")])


async def cancellable_tool_handler(ctx, params):
    await asyncio.sleep(5)  # would never finish in this test -- we cancel it first
    raise AssertionError("should have been cancelled before reaching here")


async def main():
    ext = TasksExtension()

    # === Case 1: client declares tasks capability, tool is augmentable ===
    print("=== CASE 1: task-augmented propose_synthesis ===")
    handler = compose_tool_call_handler([ext], slow_tool_handler)
    ctx = make_ctx(declares_tasks=True)
    params = CallToolRequestParams(name="propose_synthesis", arguments={})
    result = await handler(ctx, params)
    print("Immediate result type:", type(result).__name__)
    assert isinstance(result, CreateTaskResult), "FAIL: expected CreateTaskResult"
    assert result.status == "working"
    print(f"  taskId={result.task_id}, status={result.status}, resultType={result.result_type}")

    # Poll before completion
    get_binding = next(m for m in ext.methods() if m.method == "tasks/get")
    from app.mcp_server.tasks_extension import _TaskIdParams
    poll1 = await get_binding.handler(ctx, _TaskIdParams(task_id=result.task_id))
    print(f"  poll before done: status={poll1.status}")
    assert poll1.status == "working"

    await asyncio.sleep(0.5)
    poll2 = await get_binding.handler(ctx, _TaskIdParams(task_id=result.task_id))
    print(f"  poll after done: status={poll2.status}")
    assert poll2.status == "completed", f"FAIL: expected completed, got {poll2.status}"
    assert "real slow result" in str(poll2.result)
    print("CASE 1: PASS -- real background execution, real polling, real completion.")

    # === Case 2: client does NOT declare tasks capability -> synchronous passthrough ===
    print("\n=== CASE 2: no tasks capability declared -> synchronous ===")
    ctx2 = make_ctx(declares_tasks=False)
    result2 = await handler(ctx2, params)
    print("Result type:", type(result2).__name__)
    assert not isinstance(result2, CreateTaskResult), "FAIL: should have run synchronously"
    print("CASE 2: PASS -- non-declaring client got the real synchronous result, not a task.")

    # === Case 3: non-augmentable tool (e.g. retrieve_precedent) always passes through ===
    print("\n=== CASE 3: non-augmentable tool always synchronous ===")
    params3 = CallToolRequestParams(name="retrieve_precedent", arguments={"query": "x"})
    result3 = await handler(ctx, params3)  # ctx DOES declare tasks capability
    assert not isinstance(result3, CreateTaskResult), "FAIL: retrieve_precedent should never be task-ified"
    print("CASE 3: PASS -- retrieve_precedent stayed synchronous even for a task-capable client.")

    # === Case 4: cancellation ===
    print("\n=== CASE 4: real cancellation ===")
    handler_cancel = compose_tool_call_handler([ext], cancellable_tool_handler)
    result4 = await handler_cancel(ctx, CallToolRequestParams(name="solve_task", arguments={}))
    assert isinstance(result4, CreateTaskResult)
    cancel_binding = next(m for m in ext.methods() if m.method == "tasks/cancel")
    ack = await cancel_binding.handler(ctx, _TaskIdParams(task_id=result4.task_id))
    print(f"  cancel ack resultType={ack.result_type}")
    # Poll-with-retry, not a fixed sleep -- cancellation is real, eventually
    # consistent asyncio scheduling (spec says the same: "eventually
    # consistent"), a fixed short sleep is a flaky test, not a real signal.
    poll4 = None
    for _ in range(20):
        await asyncio.sleep(0.05)
        poll4 = await get_binding.handler(ctx, _TaskIdParams(task_id=result4.task_id))
        if poll4.status == "cancelled":
            break
    print(f"  poll after cancel: status={poll4.status}")
    assert poll4.status == "cancelled", f"FAIL: expected cancelled, got {poll4.status}"
    print("CASE 4: PASS -- real asyncio cancellation propagated to real task status.")

    # === Case 5: unknown taskId -> real spec-mandated error ===
    print("\n=== CASE 5: unknown taskId ===")
    from mcp.shared.exceptions import MCPError
    try:
        await get_binding.handler(ctx, _TaskIdParams(task_id="nonexistent"))
        raise AssertionError("FAIL: should have raised MCPError")
    except MCPError as exc:
        print(f"  got real MCPError: code={exc.code}, message={exc.message}")
        assert exc.code == -32602
    print("CASE 5: PASS")

    print("\nAll 5 real cases passed against the real mcp.server.extension machinery.")


if __name__ == "__main__":
    asyncio.run(main())
