"""General post-execution artifact validation gate (product/backend level,
not experiment-specific).

REAL DEFECT THIS CLOSES: every real "did this execution succeed" decision
in this codebase today (`app.local_agent.runner._run_local_node`, and the
equivalent `stop_reason == "finished"` closures in `app.mcp_server.server`)
is derived ENTIRELY from the executing agent's own self-reported
`stop_reason` plus "did it touch any files" -- never from actually
inspecting the artifact it produced. Confirmed live: a D1 rehearsal at
max_steps=40 finished with a real patch and was accepted as success even
though the generated module referenced `pydantic.CreateModel`, which does
not exist -- the resulting code could not even be imported. Nothing in the
real production path (LocalAgentRunner -> result determination ->
report_execution -> record_execution_outcome -> evidence -> procedure
maturation) would have caught this: a model's own "I'm done" is not
evidence that what it produced works.

DESIGN, why a registry and not a single hardcoded Python check: this
codebase already has exactly one precedent for "a closed, extensible
vocabulary of kinds, with an honestly-partial registry of real validators
behind it" -- `app.execution.implementations.IMPLEMENTATION_KINDS` /
`_REGISTERED_STRATEGIES` (only `"frontier"` has a real executor; every
other kind is valid and storable but explicitly reported unsupported,
never silently skipped or silently run anyway). This module mirrors that
shape for ARTIFACT kinds instead of EXECUTOR kinds: `ARTIFACT_VALIDATORS`
maps a file extension to a real validator function. Only `.py` has one
today -- the one real, already-demonstrated defect. A future artifact
type (a config file, a `.json` schema, a `.tf` file) gets its own real
validator registered here later; until then, an edited file with no
registered validator is honestly left unvalidated (never blocked, never
faked-passed) -- this module never fabricates a pass for a kind it cannot
actually check.

WHAT "validate" MEANS FOR PYTHON, AND WHY SYNTAX ALONE ISN'T ENOUGH: the
D1 defect (`from pydantic import CreateModel`) is grammatically valid
Python -- `ast.parse` accepts it without complaint. Only actually
importing the module surfaces that `CreateModel` does not exist on the
installed `pydantic` package. So this validator does two real, cheap,
deterministic checks in order: (1) `ast.parse` (catches a merely
unparseable file for free, no subprocess needed) then (2) a real `import`
of the edited module, run in a fresh subprocess against THIS process's own
interpreter (`sys.executable`) with the repo root on `PYTHONPATH` -- the
same installed dependency set the repo's own code already runs under,
never a second/different environment.

SCOPE, STATED HONESTLY: this derives a dotted module name from the file's
path relative to the repo root and imports exactly that module -- it does
not run the target's test suite (that needs the target's own Docker image
and can take 30s-600s+, per this project's own prior measurements,
incompatible with a per-execution gate) and it does not prove semantic
correctness, only "this artifact is not immediately, deterministically
broken in a way a real `import` would catch". A file whose relative path
does not resolve to a plausible dotted module name (e.g. it lives outside
any importable root) is honestly reported as syntax-checked-only, not
silently upgraded to "import verified".
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

_IMPORT_CHECK_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ArtifactValidationResult:
    """`validated=False` means a real, deterministic check ran and FAILED --
    never "we couldn't tell". A caller that gets `validated=True` back knows
    a real check ran and passed, OR that nothing registered could check this
    artifact at all (see `reason` either way -- never conflated silently)."""

    validated: bool
    reason: str
    kind: str


def _resolve_within_root(repo_root: str, rel_path: str) -> Optional[str]:
    """Same path-containment discipline as
    experiments/swebench_pro/agent.py::RepoSandbox._resolve -- a
    component-boundary check (`+ os.sep`), not a bare string-prefix check,
    so `repo_root=".../repo"` cannot be fooled by a sibling
    `.../repo-secret/...`. Returns None (never raises) for a path that
    escapes the root -- a caller treats that as "cannot validate", not as a
    crash, since a malformed `files_edited` entry is a data-quality issue
    upstream, not this gate's concern to enforce."""
    root = os.path.abspath(repo_root)
    full = os.path.abspath(os.path.join(root, rel_path.lstrip("/\\")))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


def _module_name_for(repo_root: str, rel_path: str) -> Optional[str]:
    """Dotted module name for a `.py` file, derived purely from its path
    relative to the repo root -- e.g. `pkg/sub/mod.py` -> `pkg.sub.mod`,
    `pkg/__init__.py` -> `pkg`. Returns None for a path this simple,
    honest derivation cannot turn into a plausible dotted name (empty
    after stripping, or a bare `__init__.py` at the root with nothing left
    to import) -- the caller falls back to syntax-only validation rather
    than guessing."""
    norm = rel_path.replace("\\", "/").lstrip("/")
    if not norm.endswith(".py"):
        return None
    parts = norm[: -len(".py")].split("/")
    parts = [p for p in parts if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def _validate_python_module(repo_root: str, rel_path: str) -> ArtifactValidationResult:
    full_path = _resolve_within_root(repo_root, rel_path)
    if full_path is None or not os.path.isfile(full_path):
        return ArtifactValidationResult(
            False, f"{rel_path!r} does not resolve to a real file under the repo root",
            "python_module",
        )

    try:
        with open(full_path, "rb") as f:
            source = f.read()
    except OSError as exc:
        return ArtifactValidationResult(False, f"could not read {rel_path}: {exc}", "python_module")

    try:
        ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return ArtifactValidationResult(
            False, f"{rel_path} does not parse: {exc}", "python_module",
        )

    module_name = _module_name_for(repo_root, rel_path)
    if module_name is None:
        return ArtifactValidationResult(
            True, f"{rel_path}: syntax-only check passed (no importable module name derived)",
            "python_module",
        )

    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = repo_root + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    try:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=repo_root, capture_output=True, text=True,
            timeout=_IMPORT_CHECK_TIMEOUT_SECONDS, env=env,
        )
    except subprocess.TimeoutExpired:
        # Conservative by design: an import that cannot even complete in
        # this generous a window is treated as a real failure, not silently
        # skipped -- the same "never fabricate a pass" discipline as every
        # other branch here.
        return ArtifactValidationResult(
            False, f"import {module_name} timed out after {_IMPORT_CHECK_TIMEOUT_SECONDS}s",
            "python_module",
        )

    if proc.returncode != 0:
        return ArtifactValidationResult(
            False,
            f"import {module_name} failed: {proc.stderr.strip()[-2000:]}",
            "python_module",
        )
    return ArtifactValidationResult(True, f"import {module_name} succeeded", "python_module")


# Extension -> real validator. Only ".py" has one today -- the one real,
# demonstrated defect this module closes. Adding a new artifact type is
# adding one more entry here, never a branch on task/procedure identity.
ARTIFACT_VALIDATORS: dict[str, Callable[[str, str], ArtifactValidationResult]] = {
    ".py": _validate_python_module,
}


def validate_edited_files(repo_root: str, files_edited: list[str]) -> ArtifactValidationResult:
    """Runs the strongest deterministic validator this process actually has
    registered for each edited file's real extension, in the order the
    files were edited; the first real failure wins and is returned
    immediately (no point running further checks once one has already
    failed). A file with no registered validator is silently skipped --
    NOT a failure, since nothing appropriate exists to check it, and this
    gate must never manufacture a check it cannot really perform.

    `validated=True` covers two honestly-distinct cases the `reason` string
    always distinguishes: at least one file was actually checked and every
    check passed, or no edited file had a registered validator at all (this
    gate had nothing to say about this specific set of files)."""
    checked_any = False
    for rel_path in files_edited:
        ext = os.path.splitext(rel_path)[1]
        validator = ARTIFACT_VALIDATORS.get(ext)
        if validator is None:
            continue
        checked_any = True
        result = validator(repo_root, rel_path)
        if not result.validated:
            return result
    if not checked_any:
        return ArtifactValidationResult(
            True,
            "no edited file had a registered artifact validator; nothing to validate",
            "none",
        )
    return ArtifactValidationResult(
        True, "every edited file with a registered validator passed", "aggregate",
    )


def gate_execution_success(
    *, repo_root: str, declared_success: bool, files_edited: list[str],
) -> tuple[bool, Optional[str]]:
    """The real gate: `declared_success` is whatever the raw execution
    mechanism (agent stop_reason + non-empty patch, today's entire
    criterion) already decided. This function NEVER overturns a declared
    FAILURE into a success -- there is nothing to validate on a run that
    already failed, and laundering a failure into a success would be a
    much worse defect than the one this module exists to close. It only
    TIGHTENS a declared success, by running the strongest deterministic
    validation this process actually has for the real artifacts produced.

    Returns `(gated_success, failure_reason)` -- `failure_reason` is None
    exactly when `gated_success` is True, so a caller can log/record the
    real cause of a downgrade without a second lookup."""
    if not declared_success:
        return False, None
    result = validate_edited_files(repo_root, files_edited)
    if result.validated:
        return True, None
    return False, result.reason
