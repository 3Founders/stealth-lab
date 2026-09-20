"""Executing a plan node through its step ``binding``.

There is no global Implementation object. A concrete way of doing a step lives on the step itself
(``procedures.steps[i].binding``, validated by ``services/source_locators.py``); the compiler copies it
onto ``PlanNode.binding`` and this module dispatches on it:

    binding.kind   executor kind (providers / adapters); binary / wasm / container are storable but have no
                   executor yet (a run fails loudly)
    ------------   ------------------------------------
    command        deterministic   (sandboxed script)
    sandbox        deterministic
    mcp_tool       tool            (MCP streamable-http call; locator = server url, parameters = arguments)
    tool           tool
    adapter        api             (HTTP endpoint = locator)
    model          frontier
    slm_artifact   slm             (no executor yet -> honest failure)
    runtime        (none yet)      honest failure

A node with no binding runs on the frontier provider (the pre-existing default). A binding whose kind has no
executor fails loudly; it never silently substitutes another kind.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from app.execution import providers
from app.execution.graph_executor import NodeResult
from app.models.plan import PlanNode

EXECUTOR_KIND_FOR_BINDING: dict[str, str] = {
    "command": "deterministic", "sandbox": "deterministic", "mcp_tool": "tool", "tool": "tool",
    "adapter": "api", "http_api": "api", "model": "frontier", "slm_artifact": "slm", "runtime": "runtime",
    # storable + validated at ingestion, no executor yet -> honest failure at run time, never a silent substitute
    "binary": "binary", "wasm": "wasm", "container": "container",
    "source_artifact": "source_artifact",
}


def executor_kind(binding: Optional[Mapping]) -> str:
    """Executor kind for a binding (``frontier`` when there is none)."""
    if not binding:
        return "frontier"
    return EXECUTOR_KIND_FOR_BINDING.get(str(binding.get("kind") or ""), "frontier")


def execution_spec(binding: Mapping, *, node: Optional[PlanNode] = None) -> dict:
    """The adapter-facing spec derived from a step binding (what adapters used to read off an implementation row)."""
    kind = executor_kind(binding)
    value = binding.get(str(binding.get("kind") or ""))
    params = dict(binding.get("parameters") or {})
    locator = binding.get("locator")
    spec: dict[str, Any] = {
        "id": f"step:{node.order}" if node is not None else "step", "name": str(value or binding.get("kind") or ""),
        "kind": kind, "version": None, "content_hash": None,
        "requirements": dict(binding.get("resources") or {}), "verification_contract": dict(binding.get("verifier") or {}),
        "locator": {}, "invocation": {},
    }
    if kind == "deterministic":
        spec["invocation"] = {"code": str(value), **params}
        if locator:
            spec["locator"] = {"path": str(locator)}
    elif kind == "tool":
        server = binding.get("server_url") or locator
        spec["locator"] = {"server_url": str(server)} if server else {}
        spec["invocation"] = {"tool_name": str(value), "arguments": params}
    elif kind == "api":
        spec["locator"] = {"endpoint": str(binding.get("endpoint") or locator or value or "")}
        spec["invocation"] = params
    else:
        spec["invocation"] = params
    return spec


async def execute_node(pool: Any, node: PlanNode, context: dict, *, scope: Any = None) -> NodeResult:
    """Run one node through its step binding (frontier when it has none)."""
    binding = node.binding
    if not binding:
        frontier = providers.get_provider("frontier")
        assert frontier is not None, "frontier provider must always be registered"
        return await frontier.execute(node, context)

    if binding.get("kind") == "source_artifact":
        result = await _execute_source_artifact(pool, node, binding, context)
        await _record_telemetry(pool, node, "deterministic", result, context)
        return result

    spec = execution_spec(binding, node=node)
    problems = validate_invocation(spec, context)
    if problems:
        return NodeResult(
            status="failure", data={"error_class": "auth_required", "missing_requirements": problems},
            notes=f"step {node.order} ({node.goal}): binding requires context this caller does not provide: {'; '.join(problems)}",
        )
    kind = spec["kind"]
    from app.execution.adapters import build_adapter
    adapter = build_adapter(kind)
    if adapter is not None:
        result = await adapter.execute(node, {**context, "execution_spec": spec})
        await _record_telemetry(pool, node, kind, result, context)
        return result
    provider = providers.get_provider(kind)
    if provider is None:
        return NodeResult(
            status="failure",
            notes=(f"step {node.order} ({node.goal}): binding kind {binding.get('kind')!r} maps to executor kind {kind!r}, which has "
                   f"no registered provider (have: {sorted(providers.PROVIDER_REGISTRY)}) -- refusing to substitute another kind"),
        )
    result = await provider.execute(node, context)
    await _record_telemetry(pool, node, kind, result, context)
    return result


async def _execute_source_artifact(pool: Any, node: PlanNode, binding: Mapping, context: dict) -> NodeResult:
    """Run an ingested source artifact in the sandbox. Found source material is NOT trusted code: this refuses
    unless the artifact row is screened (`admission_decision = 'admitted'`), explicitly `execution_allowed`,
    role `executable_source`, has stored content, and its bytes still hash to `content_hash`. Only a python
    runtime has a sandbox executor today; anything else fails loudly."""
    import hashlib

    def fail(msg: str, cls: str = "not_executable") -> NodeResult:
        return NodeResult(status="failure", data={"error_class": cls}, notes=f"step {node.order} ({node.goal}): {msg}")

    if pool is None:
        return fail("source_artifact execution needs a database pool to check the artifact's screening state")
    row = await pool.fetchrow(
        "SELECT id, content_hash, role, execution_allowed, admission_decision, content_ref, language "
        "FROM ingested_artifacts WHERE id = $1::uuid AND t_invalid IS NULL", str(binding["source_artifact"]),
    )
    if row is None:
        return fail(f"artifact {binding['source_artifact']!r} does not exist (or was invalidated)")
    if row["role"] != "executable_source":
        return fail(f"artifact role is {row['role']!r}, not 'executable_source'")
    if not row["execution_allowed"]:
        return fail("artifact is preserved but execution_allowed is false (not screened/authorized)", "unscreened")
    if row["admission_decision"] != "admitted":
        return fail(f"artifact screening decision is {row['admission_decision']!r}, not 'admitted'", "unscreened")
    if str(binding.get("runtime")) != "python":
        return fail(f"runtime {binding.get('runtime')!r} has no sandbox executor yet (only 'python')")
    ref = row["content_ref"]
    if not ref:
        return fail("artifact has no stored content (content_ref) -- a bare URL is not enough to run")
    from app.services.object_storage import get_store
    store = get_store()
    if store is None:
        return fail("no object store is configured, cannot fetch the artifact bytes", "environment")
    data = await store.get(ref["locator"] if isinstance(ref, dict) else str(ref))
    if hashlib.sha256(data).hexdigest() != row["content_hash"]:
        return fail("stored bytes no longer match the artifact content_hash", "integrity")
    from app.execution.adapters import build_adapter
    adapter = build_adapter("deterministic")
    spec = {"id": str(row["id"]), "name": str(binding.get("entrypoint")), "kind": "deterministic", "content_hash": row["content_hash"],
            "invocation": {"code": data.decode("utf-8", "replace")}, "locator": {}, "requirements": {}}
    return await adapter.execute(node, {**context, "execution_spec": spec})


async def _record_telemetry(pool: Any, node: PlanNode, kind: str, result: NodeResult, context: dict) -> None:
    """Sec 13/14/32 chokepoint: a bound step dispatch records one telemetry row when the caller says which
    procedure it belongs to (`context["procedure_id"]`). Never fails the real execution on a telemetry problem."""
    pid = context.get("procedure_id")
    if not pid or pool is None:
        return
    import logging
    from app.execution.execution_telemetry import record_step_execution
    try:
        await record_step_execution(
            pool, procedure_id=str(pid), step_order=int(node.order), executor=kind,
            outcome_status="success" if result.status == "success" else "failure", node_result_data=result.data,
            goal_id=context.get("goal_id"), execution_run_id=context.get("execution_run_id"),
            execution_run_node_id=context.get("execution_run_node_id"),
        )
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("failed to record step telemetry (execution result unaffected)")


def check_requirements(spec: Mapping, available: dict) -> bool:
    """Does ``available`` carry a truthy value for every truthy key of ``spec['requirements']``."""
    for key, value in (spec.get("requirements") or {}).items():
        if value and not available.get(key):
            return False
    return True


def validate_invocation(spec: Mapping, context: dict) -> list[str]:
    """Human-readable problems if ``context`` cannot satisfy the binding's network/credential requirements."""
    problems: list[str] = []
    req = spec.get("requirements") or {}
    if req.get("network") and not context.get("network_access"):
        problems.append("requirements['network'] is true but context does not indicate network access")
    need = req.get("credentials")
    if need:
        if isinstance(need, (list, tuple)):
            missing = [c for c in need if c not in set(context.get("credentials") or [])]
            if missing:
                problems.append(f"requirements['credentials'] names {list(need)}, context is missing {missing}")
        elif not context.get("credentials"):
            problems.append("requirements['credentials'] is set but context carries no credentials")
    return problems
