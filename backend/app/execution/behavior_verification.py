"""General post-execution BEHAVIORAL verification gate (Gate 2B), deliberately
separate from `app.execution.artifact_validation`.

WHY A SECOND, SEPARATE GATE RATHER THAN EXTENDING ARTIFACT VALIDATION:
`artifact_validation.py` answers one narrow, capability-agnostic question --
"is the edited file even a valid artifact of its own type" (for `.py`:
parses, imports). That question is the same for every capability and every
procedure; it has no idea what the code is FOR. Some capabilities need a
stronger claim checked before their evidence is trusted: not just "this
file imports" but "this file's own advertised behavior actually holds"
(e.g., for "lazy-load MCP tool schemas": does the default listing really
omit full schemas, does the on-demand fetch really return a correct one).
That is task/behavior semantics, and it does not belong bolted onto
artifact_validation's generic per-extension checks -- doing so would make
every future artifact-kind validator carry capability-specific baggage
that has nothing to do with "is this a valid Python module."

DESIGN, same registry shape as `implementations.py` (KIND -> real
executor) and `artifact_validation.py` (extension -> real validator):
a name -> real verifier function registry. A capability with no
registered verifier is completely unaffected (behavioral verification is
OPT-IN per capability, never a universal gate bolted onto every
execution) -- this module never fabricates a check it cannot really
perform, matching artifact_validation's own discipline.

WHERE THIS SITS IN THE PIPELINE (see `app.local_agent.runner`):
  agent result (declared success/failure)
    -> artifact_validation.gate_execution_success   (generic: is the artifact valid at all)
    -> behavior_verification.gate_behavioral_success (opt-in: does THIS capability's claimed behavior hold)
    -> final success/failure
    -> report_execution / record_local_execution_outcome
    -> evidence / verification_stats

Both gates share the exact same non-negotiable rule: NEVER launder a
declared failure into a success. Behavioral verification only runs, and
can only downgrade, an already-artifact-validated declared success.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

BehaviorVerifier = Callable[[str, list[str]], "BehaviorVerificationResult"]


@dataclass(frozen=True)
class BehaviorVerificationResult:
    """`passed=False` means a real, deterministic behavioral check ran and
    FAILED -- never "we couldn't tell". Mirrors
    `artifact_validation.ArtifactValidationResult`'s shape on purpose."""

    passed: bool
    reason: str


# name -> real verifier function. Empty by default; concrete capabilities
# register themselves (see app.execution.verifiers.mcp_lazy_tool_schemas
# for the first one) rather than this module knowing about every
# capability that will ever exist.
_REGISTRY: dict[str, BehaviorVerifier] = {}


class BehaviorVerifierAlreadyRegistered(ValueError):
    """A second module tried to register a verifier under a name already
    taken -- a producer-side bug (two capabilities silently colliding on
    one name), surfaced immediately rather than letting the second
    registration silently shadow the first."""


def register_behavior_verifier(name: str, verifier: BehaviorVerifier) -> None:
    if name in _REGISTRY:
        raise BehaviorVerifierAlreadyRegistered(
            f"a behavior verifier is already registered under {name!r}"
        )
    _REGISTRY[name] = verifier


def get_behavior_verifier(name: str) -> Optional[BehaviorVerifier]:
    return _REGISTRY.get(name)


def registered_verifier_names() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def gate_behavioral_success(
    *,
    name: Optional[str],
    repo_root: str,
    declared_success: bool,
    files_edited: list[str],
) -> tuple[bool, Optional[str]]:
    """The real gate. `declared_success` is whatever the caller already
    decided AFTER artifact validation (i.e. this must be called with
    `gate_execution_success`'s own output, never the raw agent-reported
    value) -- this function NEVER overturns a declared failure into a
    success, the same discipline `artifact_validation.gate_execution_
    success` enforces. It only tightens an already-artifact-valid
    declared success, by running the one real behavioral verifier
    registered under `name`, if any.

    `name=None` is a no-op pass-through: most procedures/capabilities
    have no behavioral contract worth checking beyond "the artifact is
    valid," and this gate must never become mandatory machinery bolted
    onto every execution path. `name` set but nothing registered under
    it is a real, actionable failure (a caller asked for a specific
    behavioral guarantee this process cannot actually check) -- never
    silently treated as "nothing to check."

    Returns `(gated_success, failure_reason)` -- `failure_reason` is None
    exactly when `gated_success` is True."""
    if not declared_success:
        return False, None
    if name is None:
        return True, None
    verifier = _REGISTRY.get(name)
    if verifier is None:
        return False, (
            f"no behavior verifier registered under {name!r} "
            f"(registered: {tuple(_REGISTRY)!r})"
        )
    result = verifier(repo_root, files_edited)
    if result.passed:
        return True, None
    return False, result.reason
