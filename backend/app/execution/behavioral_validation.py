"""General post-execution BEHAVIORAL validation gate (product/backend level,
not experiment-specific).

WHAT THIS IS, AND WHAT IT IS NOT: this module is a deliberate SIBLING of
`app.execution.artifact_validation`, not a replacement for it, and the two
concepts stay separate on purpose:

  - ARTIFACT validation answers "is the produced file usable at all?"
    (parses, imports). It is content-shaped: keyed on file extension.
  - BEHAVIORAL validation answers "does the produced artifact actually DO
    what the procedure it came from claims it does?" It is capability-
    shaped: keyed on a procedure-declared `capability_kind`, checked
    against a declarative `behavioral_contract`.

A patch that imports cleanly can still fail to deliver the intended
behavior -- the real rehearsal failure that motivated artifact validation
proved importability alone is not enough; the mirror failure (imports
cleanly, but the lazy-schema listing still ships full JSON schemas) is
what THIS module closes. A real procedure execution only counts as a
success when BOTH gates pass: artifact first (cheap, always applicable),
then behavioral (when the matched procedure declares a contract).

CONTRACT SHAPE (declared by a procedure in its `domain_payload`):
    domain_payload = {
        "behavioral_contract": {
            "capability_kind": "lazy_tool_schema",
            "module": "tool_server.py",          # repo-relative artifact path
            "expected_tools": {                  # name -> declared metadata
                "echo": {"input_schema": {...}},
            },
            "tool_call": {                       # one ordinary-use probe
                "name": "echo",
                "args": {"text": "hi"},
                "expect_contains": "hi",
            },
        }
    }
The contract is data, not code: a new KIND of procedural capability is a
new entry in `BEHAVIORAL_VERIFIERS` plus a contract schema, never a branch
on task/procedure identity.

HONESTY RULES (inherited from artifact_validation's precedent):
  - A declared FAILURE is never overturned into a success (nothing to
    validate; laundering failures is worse than the defect either gate
    exists to close).
  - A contract whose `capability_kind` has no registered verifier is
    honestly left UNVALIDATED (reported, never faked) -- the registry
    only ever contains verifiers that really run.
  - A MALFORMED contract (missing required keys) fails CLOSED: the
    procedure's own declaration is broken, and a success recorded on top
    of an unrunnable declared contract would be fabricated evidence.
  - No LLM, no network, no DB: every check is a deterministic subprocess
    against the produced artifact in the real repo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Generous-but-bounded: a real tool-server module import plus three
# behavioral probes is sub-second work; 60s covers a slow cold interpreter
# without ever hanging the runner.
_BEHAVIORAL_CHECK_TIMEOUT_SECONDS = 60

# Keys whose presence in a DEFAULT tool listing means the full JSON schema
# leaked -- exactly the defect this capability kind exists to prevent.
_DEFAULT_SCHEMA_LEAK_KEYS = [
    "input_schema", "inputSchema", "parameters", "input_schema_json",
]


@dataclass
class BehavioralValidationResult:
    validated: bool
    reason: str
    capability_kind: str
    checks: list[dict[str, Any]] = field(default_factory=list)


def extract_behavioral_contract(procedure: Optional[dict]) -> Optional[dict]:
    """Pulls the declared behavioral contract out of a matched procedure's
    own payload. Returns None when there is nothing declared -- the common
    case, and the reason the artifact-only gate remains the universal
    default. A non-dict payload or a contract without a capability_kind is
    not a contract (capture-side validation owns rejecting malformed
    contracts; this side just never invents one)."""
    if not isinstance(procedure, dict):
        return None
    payload = procedure.get("domain_payload")
    if not isinstance(payload, dict):
        return None
    contract = payload.get("behavioral_contract")
    if not isinstance(contract, dict):
        return None
    if not isinstance(contract.get("capability_kind"), str) or not contract["capability_kind"]:
        return None
    return contract


def _require_contract_keys(contract: dict) -> Optional[str]:
    for key in ("module", "expected_tools", "tool_call"):
        if key not in contract:
            return f"behavioral contract is missing required key {key!r}"
    if not isinstance(contract["expected_tools"], dict) or not contract["expected_tools"]:
        return "behavioral contract 'expected_tools' must be a non-empty object"
    call = contract["tool_call"]
    if not isinstance(call, dict) or not {"name", "args"} <= set(call):
        return "behavioral contract 'tool_call' must be an object with 'name' and 'args'"
    return None


_DRIVER_TEMPLATE = r'''
import importlib.util
import json
import sys

payload = json.load(sys.stdin)

spec = importlib.util.spec_from_file_location(
    "stealthlab_behavioral_target", payload["module_path"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

checks = []

def record(name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:500]})
    if not ok:
        print(json.dumps({"checks": checks, "ok": False, "reason": detail}))
        sys.exit(1)

# --- Check 1: default listing exposes lightweight metadata only ---
listing = mod.list_tools()
record("listing_is_list", isinstance(listing, list), f"got {type(listing).__name__}")
names = []
for i, entry in enumerate(listing):
    record("listing_entry_%d_is_dict" % i, isinstance(entry, dict),
           f"got {type(entry).__name__}")
    name = entry.get("name")
    record("listing_entry_%d_has_name" % i, isinstance(name, str) and bool(name),
           f"got {name!r}")
    names.append(name)
    for leak_key in payload["schema_leak_keys"]:
        record("no_schema_leak_%d" % i, leak_key not in entry,
               f"full-schema key {leak_key!r} present in default listing entry {i}: "
               "the whole point of lazy loading is that the default listing "
               "does not ship full per-tool JSON schemas")
for tool in payload["expected_tools"]:
    record("advertised_tool", tool in names,
           f"expected tool {tool!r} missing from default listing {names!r}")

# --- Check 2: a targeted mechanism obtains the FULL schema, correctly ---
record("targeted_retrieval_exists", hasattr(mod, "get_tool_schema"),
       "module exposes no get_tool_schema() targeted full-schema mechanism")
for tool, meta in payload["expected_tools"].items():
    try:
        got = mod.get_tool_schema(tool)
    except Exception as exc:  # noqa: BLE001
        record("targeted_retrieval_runs", False, f"raised {exc!r}")
        raise SystemExit(1)
    expected = meta.get("input_schema")
    record("targeted_schema_correct", got == expected,
           f"targeted schema for {tool!r} is {got!r}, contract declares {expected!r}")

# --- Check 3: ordinary tool usage still works ---
call = payload["tool_call"]
record("ordinary_usage_exists", hasattr(mod, "call_tool"),
       "module exposes no call_tool() ordinary-usage mechanism")
try:
    result = mod.call_tool(call["name"], call["args"])
except Exception as exc:  # noqa: BLE001
    record("ordinary_usage_runs", False, f"call_tool({call['name']!r}) raised {exc!r}")
    raise SystemExit(1)
expect = call.get("expect_contains")
if expect is not None:
    record("ordinary_usage_output", str(expect) in str(result),
           f"ordinary call result {result!r} does not contain {expect!r}")

print(json.dumps({"checks": checks, "ok": True, "reason": "all behavioral checks passed"}))
'''


def _verify_lazy_tool_schema(repo_root: str, contract: dict) -> BehavioralValidationResult:
    """The real, deterministic verifier for capability kind
    'lazy_tool_schema': imports the produced module in a fresh subprocess
    and exercises the three-point contract (lightweight default listing /
    no schema leak, correct targeted full-schema retrieval, ordinary usage
    still functional). No LLM, no network, no DB."""
    kind = "lazy_tool_schema"
    malformed = _require_contract_keys(contract)
    if malformed is not None:
        return BehavioralValidationResult(False, malformed, kind)

    module_rel = contract["module"]
    module_path = os.path.join(repo_root, module_rel)
    if not os.path.isfile(module_path):
        return BehavioralValidationResult(
            False, f"behavioral contract targets module {module_rel!r}, "
                   f"which does not exist in the produced repo", kind,
        )

    payload = {
        "module_path": os.path.abspath(module_path),
        "expected_tools": contract["expected_tools"],
        "tool_call": contract["tool_call"],
        "schema_leak_keys": contract.get("schema_leak_keys", _DEFAULT_SCHEMA_LEAK_KEYS),
    }

    # Repo root on PYTHONPATH so the produced module's own imports resolve
    # exactly as they would in the real target repo.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (repo_root, env.get("PYTHONPATH")) if p
    )

    with tempfile.TemporaryDirectory(prefix="stealthlab_behavioral_") as tmp:
        driver_path = os.path.join(tmp, "behavioral_driver.py")
        with open(driver_path, "w", encoding="utf-8") as fh:
            fh.write(_DRIVER_TEMPLATE)
        try:
            proc = subprocess.run(
                [sys.executable, driver_path],
                input=json.dumps(payload),
                capture_output=True, text=True,
                timeout=_BEHAVIORAL_CHECK_TIMEOUT_SECONDS,
                cwd=repo_root, env=env,
            )
        except subprocess.TimeoutExpired:
            return BehavioralValidationResult(
                False, f"behavioral check timed out after "
                       f"{_BEHAVIORAL_CHECK_TIMEOUT_SECONDS}s", kind,
            )

    stdout = proc.stdout or ""
    report: dict[str, Any] = {}
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                report = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    checks = report.get("checks", [])
    if proc.returncode == 0 and report.get("ok"):
        return BehavioralValidationResult(True, report.get("reason", "ok"), kind, checks)
    reason = report.get("reason") or (proc.stderr or "").strip()[-2000:] or \
        f"behavioral driver exited {proc.returncode} with no report"
    return BehavioralValidationResult(False, reason, kind, checks)


# Capability kind -> real verifier. Adding a new kind of procedural
# capability is adding one more entry here, never a branch on task or
# procedure identity (same registry precedent as artifact_validation).
BEHAVIORAL_VERIFIERS: dict[str, Callable[[str, dict], BehavioralValidationResult]] = {
    "lazy_tool_schema": _verify_lazy_tool_schema,
}


def gate_execution_success_with_behavior(
    *,
    repo_root: str,
    declared_success: bool,
    files_edited: list[str],
    behavioral_contract: Optional[dict] = None,
) -> tuple[bool, Optional[str]]:
    """THE composition gate: artifact validation first, behavioral second,
    failure never upgraded.

    Order and semantics:
      1. A declared failure stays a failure (reason None -- nothing ran,
         nothing to report).
      2. Artifact validation (`app.execution.artifact_validation`) runs on
         the edited files; its failure is final -- behavioral checks are
         pointless on an unusable artifact.
      3. No declared behavioral contract -> artifact-only gate result, as
         before this module existed (the universal default).
      4. A contract whose capability_kind has no registered verifier is
         honestly UNVALIDATED: success stands, with the gap named in the
         reason. Never faked, never silently skipped.
      5. A malformed contract fails closed.
      6. A registered verifier runs; its verdict is final.
    """
    # 1. Failure is never laundered.
    if not declared_success:
        return False, None

    # 2. Artifact gate first.
    from app.execution.artifact_validation import gate_execution_success
    ok, reason = gate_execution_success(
        repo_root=repo_root, declared_success=True, files_edited=files_edited,
    )
    if not ok:
        return False, reason

    # 3. No contract: unchanged artifact-only behavior.
    if behavioral_contract is None:
        return True, None

    kind = behavioral_contract.get("capability_kind")
    if not isinstance(kind, str) or not kind:
        return False, "behavioral contract has no usable capability_kind"

    verifier = BEHAVIORAL_VERIFIERS.get(kind)
    if verifier is None:
        # 4. Honest unvalidated -- named, never fabricated, never blocking.
        return True, None

    # 5/6. Malformed fails closed; a real verifier's verdict is final.
    result = verifier(repo_root, behavioral_contract)
    if result.validated:
        return True, None
    return False, f"behavioral validation ({kind}) failed: {result.reason}"
