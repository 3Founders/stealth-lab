"""
MCP hardening B25/B27/B28: the literal Implementation Adapter contract.

B25 -- Implementation adapter architecture:

    Implementation
          v
    Adapter Resolver
          v
    Binary | MCP | HTTP/API | Container | WASM | Model | Local
          v
    concrete runtime

    Conceptual adapter contract:
        resolve() validate() prepare() invoke() collect_result()
        collect_artifacts() collect_evidence() cleanup()

    "Adapters are execution infrastructure, not new ontology."

This module adds `Adapter(ImplementationProvider)` -- an `ImplementationProvider`
subclass (see `providers.py`) that ALSO implements this literal 8-method
contract, with `execute()` (the existing, already-wired dispatch method
`implementation_executor.execute_implementation` calls) now REALLY
implemented as the composition of those 8 steps, in order -- not a
decorative addition alongside a separately-implemented `execute()`.
This is additive to `providers.PROVIDER_REGISTRY`, not a second registry:
`build_adapter()` below is the "Adapter Resolver", called from the SAME
dispatch point `execute_implementation` already uses, for the kinds that
need PER-IMPLEMENTATION resolution (`api`/`tool`, each pointing at a
different endpoint) rather than one process-wide singleton (`frontier`/
`deterministic`, which stay on the pre-existing `PROVIDER_REGISTRY`
singleton pattern unchanged -- CLAUDE.md rule 2, don't redesign what
already works).

B27 -- External implementation hosting: `HttpApiAdapter` (kind='api',
the real 'https' protocol this codebase's own `_protocol_for` already
maps 'api' to) and `McpToolAdapter` (kind='tool', the real 'mcp'
protocol `_protocol_for` already maps 'tool' to) are REAL, working
executors -- `httpx`/`mcp.client` calls a real endpoint, not a
fabricated stand-in. `execution_location='third_party_hosted'`
(migration 71) is the honest label for what these dispatch to.

B28 -- Local sandbox implementation: `LocalAdapter` (kind='deterministic')
implements the literal 8-step lifecycle B28 names (resolve concrete
artifact -> verify identity/digest -> create isolated runtime -> mount
declared inputs -> invoke -> capture outputs/artifacts -> verify ->
record evidence) around the SAME real `SubprocessSandboxExecutor`
`DeterministicProvider` already wraps (reused, not reimplemented).
"""
from __future__ import annotations

import dataclasses
import hashlib
from abc import abstractmethod
from typing import Optional

from app.execution.graph_executor import NodeResult
from app.execution.providers import ImplementationProvider, ProviderAvailability, ProviderCapabilityInfo
from app.models.plan import PlanNode
from app.services.sandbox_executor import ExecutionResult, SandboxExecutor, SubprocessSandboxExecutor


class Adapter(ImplementationProvider):
    """B25's literal 8-method contract. Every method is real (no default
    body that fabricates a result) -- a concrete subclass implements all
    eight, or does not exist in `build_adapter`'s resolution table."""

    @abstractmethod
    async def resolve(self, implementation: dict, context: Optional[dict] = None) -> dict:
        """`implementation` row (+ optional caller `context`, for a kind
        whose concrete target is legitimately supplied per-call rather
        than pre-registered on the row -- e.g. LocalAdapter's `code`,
        matching the pre-existing `DeterministicProvider` contract this
        adapter preserves) -> a concrete RESOLVED target descriptor
        (endpoint URL, binary path, tool name -- whatever this adapter's
        kind needs). Never invents a target neither the row nor context
        actually name (B38: "invented Implementation when resolution
        fails" is forbidden) -- raises `AdapterResolutionError` instead."""

    @abstractmethod
    async def validate(self, resolved: dict) -> dict:
        """`resolved` -> `{"valid": bool, "reason": str}` -- identity/
        digest/reachability checks, never assumed true."""

    @abstractmethod
    async def prepare(self, resolved: dict, node: PlanNode, context: dict) -> dict:
        """`resolved` target + this node's real inputs -> a PREPARED
        invocation descriptor (staged files, an HTTP request body, an
        MCP tool-call payload)."""

    @abstractmethod
    async def invoke(self, prepared: dict) -> dict:
        """`prepared` -> the RAW invocation outcome (subprocess exit
        code/output, HTTP response, MCP CallToolResult) -- untranslated,
        so `collect_*` below can each look at exactly what really
        happened."""

    @abstractmethod
    async def collect_result(self, invocation: dict) -> NodeResult:
        """raw outcome -> the `NodeResult` this codebase's schedulers
        already consume."""

    @abstractmethod
    async def collect_artifacts(self, invocation: dict) -> list[dict]:
        """raw outcome -> artifact REFERENCES (B8: "large data is stored
        as artifact references/hashes"), never inline content."""

    @abstractmethod
    async def collect_evidence(self, invocation: dict) -> dict:
        """raw outcome -> a real evidence-shaped dict (`outcome_status`/
        `failure_class`/`context_key`) a caller can hand to
        `evidence`-writing code, never a bare success flag."""

    @abstractmethod
    async def cleanup(self, prepared: dict) -> None:
        """release any real resource `prepare()` created (temp dirs,
        open connections). Runs even when `invoke()` raised."""

    async def execute(self, node: PlanNode, context: dict) -> NodeResult:
        """The literal 8-step composition -- THE only `execute()` body
        for an `Adapter`, not a separately-implemented shortcut. Errors
        at `resolve`/`invoke` surface as an honest `failure` NodeResult,
        never a silently-swallowed exception (B38: no catch-all path
        that returns success)."""
        implementation = context.get("implementation") or {}
        try:
            resolved = await self.resolve(implementation, context)
        except AdapterResolutionError as exc:
            return NodeResult(status="failure", notes=f"step {node.order} ({node.goal}): resolve failed -- {exc}")

        validation = await self.validate(resolved)
        if not validation.get("valid"):
            return NodeResult(
                status="failure",
                notes=f"step {node.order} ({node.goal}): validation failed -- {validation.get('reason')}",
            )

        prepared = await self.prepare(resolved, node, context)
        try:
            invocation = await self.invoke(prepared)
        except Exception as exc:  # noqa: BLE001 -- a real invocation failure is data, reported honestly below
            await self.cleanup(prepared)
            return NodeResult(
                status="failure",
                notes=f"step {node.order} ({node.goal}): invoke raised {type(exc).__name__}: {exc}",
            )

        try:
            result = await self.collect_result(invocation)
            artifacts = await self.collect_artifacts(invocation)
            evidence = await self.collect_evidence(invocation)
            # B27: "...the concrete endpoint/tool/version". The VERSION
            # half of that triple is the same for every adapter kind (the
            # real `implementations.version` this row carries) -- recorded
            # once here rather than duplicated in each subclass's own
            # collect_evidence, which already records its own kind-specific
            # endpoint/tool half.
            evidence = {**evidence, "implementation_version": implementation.get("version")}
            # NodeResult is a frozen dataclass (graph_executor.py's own
            # invariant -- a scheduler must never see a result mutated
            # out from under it) -- replace, never assign.
            return dataclasses.replace(
                result, data={**(result.data or {}), "artifacts": artifacts, "evidence": evidence},
            )
        finally:
            await self.cleanup(prepared)


class AdapterResolutionError(ValueError):
    """`resolve()` could not name a real target from this implementation
    row -- e.g. no `locator.endpoint` for an HTTP adapter. Never
    papered over with a guessed default."""


# ---------------------------------------------------------------------------
# LocalAdapter (B28): the literal 8-step local-sandbox lifecycle around the
# real SubprocessSandboxExecutor.
# ---------------------------------------------------------------------------
class LocalAdapter(Adapter):
    """kind='deterministic'. Wraps `SubprocessSandboxExecutor` (reused,
    not reimplemented) with the literal B28 lifecycle:
    resolve concrete artifact -> verify identity/digest -> create
    isolated runtime -> mount declared inputs -> invoke -> capture
    outputs/artifacts -> verify -> record evidence."""

    kind = "deterministic"

    def __init__(self, executor: Optional[SandboxExecutor] = None):
        self._executor: SandboxExecutor = executor or SubprocessSandboxExecutor()

    async def discover(self, requirements: dict) -> ProviderAvailability:
        return ProviderAvailability(
            True,
            f"{type(self._executor).__name__} is constructed and ready in this process",
        )

    async def inspect(self, task_requirements: dict) -> ProviderCapabilityInfo:
        return ProviderCapabilityInfo(
            kind=self.kind, provider_name=type(self).__name__,
            description="Local sandboxed script execution via the real B28 8-step lifecycle.",
            requirements={
                "code": "str -- the script to run (required)",
                "input_files": "dict[str, bytes], optional",
                "timeout_seconds": "float, optional (default 30)",
                "network_access": "bool, optional (default False)",
            },
        )

    async def resolve(self, implementation: dict, context: Optional[dict] = None) -> dict:
        """"resolve concrete artifact" -- the script/code this
        implementation runs. `context['code']` (the PRE-EXISTING
        `DeterministicProvider` contract -- code supplied fresh per call,
        never stored on the durable row) is checked FIRST so every
        existing caller of that contract keeps working unchanged;
        `implementation.invocation.code` (a durable, pre-registered
        script) is the fallback for a real row that names one. No code
        anywhere means nothing to resolve."""
        context = context or {}
        code = context.get("code")
        if not code:
            invocation = implementation.get("invocation") or {}
            code = invocation.get("code")
        if not code:
            raise AdapterResolutionError(
                f"implementation {implementation.get('id')!r}: neither context['code'] nor "
                "invocation.code was given -- nothing to resolve as a concrete local artifact"
            )
        return {"code": code, "content_hash": implementation.get("content_hash")}

    async def validate(self, resolved: dict) -> dict:
        """"verify identity/digest" -- a REAL sha256 of the resolved
        code, compared against the implementation's own recorded
        `content_hash` when one exists. No recorded hash is an honest
        pass-through (nothing to verify against, never fabricated as a
        failure), not a silent skip disguised as success -- the reason
        says so either way."""
        digest = hashlib.sha256(resolved["code"].encode("utf-8")).hexdigest()
        expected = resolved.get("content_hash")
        if expected is None:
            return {"valid": True, "reason": "no content_hash recorded on this implementation to verify against", "digest": digest}
        if digest != expected:
            return {"valid": False, "reason": f"digest mismatch: computed {digest} != recorded {expected}"}
        return {"valid": True, "reason": "digest matches recorded content_hash", "digest": digest}

    async def prepare(self, resolved: dict, node: PlanNode, context: dict) -> dict:
        """"create isolated runtime" + "mount declared inputs": staging
        happens for real inside `SubprocessSandboxExecutor.run()` itself
        (a fresh temp dir per call) -- this step stages the REQUEST, the
        executor creates the real runtime at `invoke()` time."""
        return {
            "code": resolved["code"],
            "input_files": context.get("input_files") or {},
            "timeout_seconds": context.get("timeout_seconds", 30.0),
            "network_access": context.get("network_access", False),
        }

    async def invoke(self, prepared: dict) -> dict:
        result: ExecutionResult = await self._executor.run(
            prepared["code"], input_files=prepared["input_files"],
            timeout_seconds=prepared["timeout_seconds"], network_access=prepared["network_access"],
        )
        return {"execution_result": result}

    async def collect_result(self, invocation: dict) -> NodeResult:
        result: ExecutionResult = invocation["execution_result"]
        succeeded = result.exit_code == 0 and not result.timed_out
        return NodeResult(
            status="success" if succeeded else "failure",
            notes=f"local sandbox run exit_code={result.exit_code} timed_out={result.timed_out}",
            data={
                "exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr,
                "output_files": result.output_files, "wall_time_seconds": result.wall_time_seconds,
                "timed_out": result.timed_out,
            },
        )

    async def collect_artifacts(self, invocation: dict) -> list[dict]:
        """"capture outputs/artifacts" -- one reference per real output
        file the run actually produced, a content hash (never the
        inline bytes -- B8's own rule), never a placeholder entry for a
        file that wasn't produced."""
        result: ExecutionResult = invocation["execution_result"]
        return [
            {"kind": "output_file", "ref": name, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
            for name, content in (result.output_files or {}).items()
        ]

    async def collect_evidence(self, invocation: dict) -> dict:
        """"record evidence" -- an honest outcome_status derived from
        the SAME real exit_code/timed_out facts `collect_result` used,
        never re-derived differently."""
        result: ExecutionResult = invocation["execution_result"]
        if result.timed_out:
            return {"outcome_status": "failure", "failure_class": "environment_changed", "detail": "sandbox run timed out"}
        if result.exit_code != 0:
            return {"outcome_status": "failure", "failure_class": "implementation_wrong", "detail": f"exit_code={result.exit_code}"}
        return {"outcome_status": "success", "detail": "exit_code=0"}

    async def cleanup(self, prepared: dict) -> None:
        """`SubprocessSandboxExecutor.run()` already tears down its own
        temp directory internally (its own documented per-call
        lifecycle) -- nothing real to release here."""
        return None


# ---------------------------------------------------------------------------
# HttpApiAdapter (B27): a REAL HTTP/API executor -- kind='api'.
# ---------------------------------------------------------------------------
class HttpApiAdapter(Adapter):
    """kind='api' (`_protocol_for`'s own default protocol for 'api' is
    'https'). A real `httpx` POST to `implementation.locator.endpoint`,
    never a fabricated call."""

    kind = "api"

    async def discover(self, requirements: dict) -> ProviderAvailability:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            return ProviderAvailability(False, f"httpx is not importable: {exc}")
        return ProviderAvailability(True, "httpx is importable")

    async def inspect(self, task_requirements: dict) -> ProviderCapabilityInfo:
        return ProviderCapabilityInfo(
            kind=self.kind, provider_name=type(self).__name__,
            description="Real HTTP/API implementation invocation via httpx.",
            requirements={
                "locator.endpoint": "str -- the real URL to call (required)",
                "locator.method": "str, optional (default POST)",
                "invocation.headers": "dict[str, str], optional",
                "invocation.timeout_seconds": "float, optional (default 30)",
            },
        )

    async def resolve(self, implementation: dict, context: Optional[dict] = None) -> dict:
        locator = implementation.get("locator") or {}
        endpoint = locator.get("endpoint")
        if not endpoint:
            raise AdapterResolutionError(
                f"implementation {implementation.get('id')!r} (kind='api') has no "
                "locator.endpoint -- nothing to resolve as a concrete HTTP target"
            )
        return {
            "endpoint": endpoint, "method": (locator.get("method") or "POST").upper(),
            "invocation": implementation.get("invocation") or {},
        }

    async def validate(self, resolved: dict) -> dict:
        """"verify identity/digest": a real reachability probe is a
        NETWORK side effect this step must not perform on every dry
        validate() call (B38: no automatic external call outside an
        explicit invoke) -- so this validates SHAPE (a real http(s)
        scheme, a non-empty host), not liveness. Liveness is what
        `invoke()` itself proves, honestly, by actually calling it."""
        endpoint = resolved["endpoint"]
        if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            return {"valid": False, "reason": f"endpoint {endpoint!r} is not a real http(s) URL"}
        return {"valid": True, "reason": "endpoint has a real http(s) scheme"}

    async def prepare(self, resolved: dict, node: PlanNode, context: dict) -> dict:
        invocation = resolved["invocation"]
        body = {"node_order": node.order, "goal": node.goal, **(context.get("request_body") or {})}
        return {
            "endpoint": resolved["endpoint"], "method": resolved["method"],
            "headers": invocation.get("headers") or {}, "body": body,
            "timeout_seconds": invocation.get("timeout_seconds", 30.0),
        }

    async def invoke(self, prepared: dict) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=prepared["timeout_seconds"]) as client:
            response = await client.request(
                prepared["method"], prepared["endpoint"],
                headers=prepared["headers"], json=prepared["body"],
            )
        return {
            "status_code": response.status_code, "text": response.text,
            "headers": dict(response.headers), "method": prepared["method"],
            # B27: "Record what Stealth requested, the concrete endpoint...".
            # Echoed straight from `prepared` (the REAL request this call
            # actually sent) rather than re-derived, so collect_result/
            # collect_evidence below can record it alongside the response --
            # not just the response, which is all this adapter captured
            # before this fix.
            "requested_endpoint": prepared["endpoint"], "requested_body": prepared["body"],
        }

    async def collect_result(self, invocation: dict) -> NodeResult:
        status_code = invocation["status_code"]
        succeeded = 200 <= status_code < 300
        return NodeResult(
            status="success" if succeeded else "failure",
            notes=f"HTTP {invocation['method']} {invocation['requested_endpoint']} -> {status_code}",
            data={
                "status_code": status_code, "response_text": invocation["text"][:4000],
                "requested_endpoint": invocation["requested_endpoint"],
                "requested_method": invocation["method"],
            },
        )

    async def collect_artifacts(self, invocation: dict) -> list[dict]:
        body_hash = hashlib.sha256(invocation["text"].encode("utf-8")).hexdigest()
        return [{"kind": "http_response", "ref": f"sha256:{body_hash}", "sha256": body_hash, "size_bytes": len(invocation["text"])}]

    async def collect_evidence(self, invocation: dict) -> dict:
        status_code = invocation["status_code"]
        # B27: evidence must record the concrete endpoint requested, not
        # just the outcome -- "what was requested" is exactly this
        # requirement's own words, distinct from B26's separate
        # observed-output concern.
        base = {
            "requested_endpoint": invocation["requested_endpoint"],
            "requested_method": invocation["method"],
        }
        if 200 <= status_code < 300:
            return {**base, "outcome_status": "success", "detail": f"HTTP {status_code}"}
        return {**base, "outcome_status": "failure", "failure_class": "external_failure", "detail": f"HTTP {status_code}"}

    async def cleanup(self, prepared: dict) -> None:
        return None  # httpx.AsyncClient is already closed by its own `async with` in invoke()


# ---------------------------------------------------------------------------
# McpToolAdapter (B27): a REAL MCP tool executor -- kind='tool'.
# ---------------------------------------------------------------------------
class McpToolAdapter(Adapter):
    """kind='tool' (`_protocol_for`'s own default protocol for 'tool' is
    'mcp'). A real `mcp.client` streamable-HTTP session calling a real
    remote MCP server's real tool, never a fabricated call."""

    kind = "tool"

    async def discover(self, requirements: dict) -> ProviderAvailability:
        try:
            import mcp  # noqa: F401
            from mcp.client.streamable_http import streamable_http_client  # noqa: F401
        except ImportError as exc:
            return ProviderAvailability(False, f"mcp client is not importable: {exc}")
        return ProviderAvailability(True, "mcp client is importable")

    async def inspect(self, task_requirements: dict) -> ProviderCapabilityInfo:
        return ProviderCapabilityInfo(
            kind=self.kind, provider_name=type(self).__name__,
            description="Real MCP tool invocation via mcp.client.streamable_http + ClientSession.",
            requirements={
                "locator.server_url": "str -- the real MCP server URL (required)",
                "invocation.tool_name": "str -- the real tool name to call (required)",
                "invocation.arguments": "dict, optional -- static arguments merged with the node's own",
            },
        )

    async def resolve(self, implementation: dict, context: Optional[dict] = None) -> dict:
        locator = implementation.get("locator") or {}
        invocation = implementation.get("invocation") or {}
        server_url = locator.get("server_url")
        tool_name = invocation.get("tool_name")
        if not server_url or not tool_name:
            raise AdapterResolutionError(
                f"implementation {implementation.get('id')!r} (kind='tool') needs both "
                f"locator.server_url and invocation.tool_name -- got server_url={server_url!r}, "
                f"tool_name={tool_name!r}"
            )
        return {"server_url": server_url, "tool_name": tool_name, "static_arguments": invocation.get("arguments") or {}}

    async def validate(self, resolved: dict) -> dict:
        server_url = resolved["server_url"]
        if not (server_url.startswith("http://") or server_url.startswith("https://")):
            return {"valid": False, "reason": f"server_url {server_url!r} is not a real http(s) URL"}
        return {"valid": True, "reason": "server_url has a real http(s) scheme"}

    async def prepare(self, resolved: dict, node: PlanNode, context: dict) -> dict:
        arguments = {**resolved["static_arguments"], **(context.get("tool_arguments") or {})}
        return {"server_url": resolved["server_url"], "tool_name": resolved["tool_name"], "arguments": arguments}

    async def invoke(self, prepared: dict) -> dict:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(prepared["server_url"]) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(prepared["tool_name"], prepared["arguments"])
        content_text = "\n".join(
            block.text for block in result.content if hasattr(block, "text")
        )
        return {
            "is_error": bool(result.is_error), "content_text": content_text,
            # B27: "Record what Stealth requested, the concrete
            # endpoint/tool/version" -- echoed from `prepared` (the REAL
            # request this call actually sent), not re-derived.
            "requested_server_url": prepared["server_url"],
            "requested_tool_name": prepared["tool_name"],
            "requested_arguments": prepared["arguments"],
        }

    async def collect_result(self, invocation: dict) -> NodeResult:
        return NodeResult(
            status="failure" if invocation["is_error"] else "success",
            notes=(
                f"MCP tool {invocation['requested_tool_name']!r} @ "
                f"{invocation['requested_server_url']} isError={invocation['is_error']}"
            ),
            data={
                "content_text": invocation["content_text"][:4000],
                "requested_server_url": invocation["requested_server_url"],
                "requested_tool_name": invocation["requested_tool_name"],
            },
        )

    async def collect_artifacts(self, invocation: dict) -> list[dict]:
        text = invocation["content_text"]
        if not text:
            return []
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return [{"kind": "mcp_tool_result", "ref": f"sha256:{digest}", "sha256": digest, "size_bytes": len(text)}]

    async def collect_evidence(self, invocation: dict) -> dict:
        # B27: evidence must record the concrete tool/endpoint requested,
        # not just the outcome.
        base = {
            "requested_server_url": invocation["requested_server_url"],
            "requested_tool_name": invocation["requested_tool_name"],
        }
        if invocation["is_error"]:
            return {**base, "outcome_status": "failure", "failure_class": "external_failure", "detail": "MCP tool reported isError=true"}
        return {**base, "outcome_status": "success", "detail": "MCP tool call succeeded"}

    async def cleanup(self, prepared: dict) -> None:
        return None  # the streamable_http/ClientSession context managers in invoke() already close everything


# ---------------------------------------------------------------------------
# Adapter Resolver (B25's own name for this): implementation row -> Adapter.
# ---------------------------------------------------------------------------
_ADAPTER_CLASSES: dict[str, type[Adapter]] = {
    "deterministic": LocalAdapter,
    "api": HttpApiAdapter,
    "tool": McpToolAdapter,
}


def build_adapter(kind: str) -> Optional[Adapter]:
    """The "Adapter Resolver": `kind` -> a fresh `Adapter` instance, or
    `None` for a kind with no real adapter (unchanged from `providers.py`'s
    own honest-unavailable posture for `slm`/`human`/`wasm`/`computer_use`
    -- Binary/Container/WASM adapters are NOT built here: no real runtime
    for them exists in this codebase, and building one with no real
    target to invoke would be exactly the fabricated-machinery B38
    forbids)."""
    cls = _ADAPTER_CLASSES.get(kind)
    return cls() if cls is not None else None
