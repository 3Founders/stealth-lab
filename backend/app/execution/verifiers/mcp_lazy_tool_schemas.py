"""Behavioral verifier for the "lazy-load MCP tool schemas" capability.

CAPABILITY BEING CHECKED (see the captured procedure's own `goal`/`steps`
for the full statement): an MCP-style tool-serving process should
advertise lightweight tool metadata by default and load a tool's complete
JSON schema only when a caller explicitly asks for it, rather than
sending every full schema on every listing call -- without breaking
ordinary tool invocation.

INTERFACE CONTRACT this verifier checks against -- documented here once,
and referenced by name from the procedure's own steps so a real
implementing agent (human or model) knows exactly what to build. A
solving repo must expose, at its root, an importable module named
`mcp_lazy_tools_target` with three functions:

    list_tools() -> list[dict]
        Each dict has at least "name" and "description" keys, and MUST
        NOT include a full-schema field ("inputSchema" / "parameters" /
        "schema") with real content -- this is the "lightweight by
        default" requirement.

    get_tool_schema(name: str) -> dict
        Returns tool `name`'s complete JSON Schema: a dict shaped like
        {"type": "object", "properties": {...}, ...}. Called on demand,
        never as part of list_tools().

    call_tool(name: str, args: dict) -> Any
        Invokes tool `name` exactly as before the lazy-loading change --
        called here with an empty args dict to prove the ordinary
        invocation path still works structurally. This module does not
        (and cannot generally) validate a tool's own business-logic
        correctness -- same honestly-scoped limit
        `artifact_validation.py` states for its own import-only check.

GENERALITY: this verifier never hardcodes a tool name, a benchmark task
id, or any fixture-specific detail -- it discovers a real tool name by
calling `list_tools()` itself, exactly the way a real caller would. Any
repo that implements the three functions above, correctly, passes; any
repo that does not, for any reason, fails with a specific, actionable
reason. Works identically against a hand-built test fixture (Gate 2B's
dry run) and against a real target repo (a future paid execution) --
nothing here is test-only.

ISOLATION: same real-subprocess technique
`artifact_validation._validate_python_module` uses (fresh interpreter,
repo root on PYTHONPATH, bounded timeout) -- never imported in-process,
so a broken or hanging target module cannot corrupt this process's own
import state or hang the caller indefinitely.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from app.execution.behavior_verification import (
    BehaviorVerificationResult,
    register_behavior_verifier,
)

VERIFIER_NAME = "mcp_lazy_tool_schemas"

TARGET_MODULE = "mcp_lazy_tools_target"

_TIMEOUT_SECONDS = 30

_LEAK_KEYS = ("inputSchema", "parameters", "schema")

# Executed in a fresh subprocess against the target repo -- see module
# docstring's ISOLATION note. Every branch prints exactly one JSON object
# and exits 0; a non-JSON stdout or non-zero exit is itself a failure
# the caller reports honestly (see _run_probe below), never silently
# swallowed.
_PROBE_SCRIPT = f"""
import json

def fail(reason):
    print(json.dumps({{"passed": False, "reason": reason}}))

try:
    import {TARGET_MODULE} as target
except Exception as exc:
    fail(f"could not import {TARGET_MODULE!r}: {{exc}}")
    raise SystemExit(0)

for fn in ("list_tools", "get_tool_schema", "call_tool"):
    if not hasattr(target, fn) or not callable(getattr(target, fn)):
        fail(f"{TARGET_MODULE!r} does not expose a callable {{fn}}()")
        raise SystemExit(0)

try:
    tools = target.list_tools()
except Exception as exc:
    fail(f"list_tools() raised: {{exc}}")
    raise SystemExit(0)

if not isinstance(tools, list) or not tools:
    fail("list_tools() did not return a non-empty list")
    raise SystemExit(0)

for t in tools:
    if not isinstance(t, dict) or not t.get("name") or not t.get("description"):
        fail(f"list_tools() entry missing name/description: {{t!r}}")
        raise SystemExit(0)
    for k in {_LEAK_KEYS!r}:
        if t.get(k):
            fail(f"default listing leaked a full schema field {{k!r}} for tool {{t.get('name')!r}} -- lightweight metadata only, schema must be fetched on demand")
            raise SystemExit(0)

first_name = tools[0]["name"]

try:
    schema = target.get_tool_schema(first_name)
except Exception as exc:
    fail(f"get_tool_schema({{first_name!r}}) raised: {{exc}}")
    raise SystemExit(0)

if not isinstance(schema, dict) or schema.get("type") != "object" or not schema.get("properties"):
    fail(f"get_tool_schema({{first_name!r}}) did not return a usable JSON-schema-shaped object (need dict with type=='object' and non-empty properties): {{schema!r}}")
    raise SystemExit(0)

try:
    target.call_tool(first_name, {{}})
except Exception as exc:
    fail(f"call_tool({{first_name!r}}, {{{{}}}}) raised -- ordinary tool invocation is broken: {{exc}}")
    raise SystemExit(0)

print(json.dumps({{
    "passed": True,
    "reason": (
        f"listing is lightweight ({{len(tools)}} tool(s), no leaked schema field), "
        f"get_tool_schema({{first_name!r}}) returned a usable schema, "
        f"call_tool({{first_name!r}}) still succeeds"
    ),
}}))
"""


def _run_probe(repo_root: str) -> BehaviorVerificationResult:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = repo_root + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE_SCRIPT],
            cwd=repo_root, capture_output=True, text=True,
            timeout=_TIMEOUT_SECONDS, env=env,
        )
    except subprocess.TimeoutExpired:
        return BehaviorVerificationResult(
            False, f"behavioral probe timed out after {_TIMEOUT_SECONDS}s",
        )

    if proc.returncode != 0:
        return BehaviorVerificationResult(
            False,
            f"behavioral probe crashed (exit {proc.returncode}): {proc.stderr.strip()[-2000:]}",
        )

    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return BehaviorVerificationResult(
            False, f"behavioral probe produced no parseable result: {exc} (stdout={proc.stdout!r})",
        )

    return BehaviorVerificationResult(bool(payload.get("passed")), str(payload.get("reason")))


def verify_mcp_lazy_tool_schemas(repo_root: str, files_edited: list[str]) -> BehaviorVerificationResult:
    """`files_edited` is accepted for interface-shape parity with every
    other registered verifier (see `BehaviorVerifier`'s type alias) but
    deliberately unused: what matters is the target module's real,
    current behavior, not which files a diff happened to touch -- an
    implementation could satisfy this contract from a file this verifier
    was never told about, and that is still a real pass."""
    del files_edited
    return _run_probe(repo_root)


register_behavior_verifier(VERIFIER_NAME, verify_mcp_lazy_tool_schemas)
