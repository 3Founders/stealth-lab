"""
Pure, dependency-free environment probing -- deliberately split out of
environment_probe.py (which stays the DB-facing module, importing this
one) so that a caller with a genuine "must never require asyncpg/
Postgres" boundary can depend on JUST this file.

WHY THE SPLIT IS REAL, NOT COSMETIC: environment_probe.py imports
app.services.claims/state/embeddings for its one DB-write function
(assert_environment_claims), and those modules import asyncpg at their
own top level. Before this split, `import app.services.environment_probe`
transitively pulled in asyncpg even for callers that only wanted
probe_environment()/invariant_bindings_from_facts() -- exactly the
callers this file exists for: app.local_agent.runner, which
structurally must have ZERO database dependency (proven by
tests/test_local_agent_runner_offline.py's own AST-parsing test) so a
lightweight local-agent-only install never needs Postgres or asyncpg
installed at all.

Everything in this file is pure and synchronous: filesystem reads only,
no LLM, no network, no database. environment_probe.py re-exports
PROBE_PREDICATE_VOCABULARY/EnvironmentFact/probe_environment/
invariant_bindings_from_facts from here unchanged, so every existing
importer (procedure_extraction/validators.py, app/mcp_server/server.py)
keeps working without a code change.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

# The single source of truth for "which predicates can a precondition
# name and actually be checked". procedure_extraction/validators.py's V1
# rule imports this exact constant rather than re-declaring the list --
# that duplication is precisely how probe and validator would drift.
PROBE_PREDICATE_VOCABULARY: tuple[str, ...] = (
    "has_framework",
    "has_build_tool",
    "has_test_runner",
    "has_dev_server",
    "package_manager",
    "language",
    "package_version",
)


@dataclass
class EnvironmentFact:
    predicate: str
    object: str


def _read_json(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


_FRAMEWORK_DEPS = (
    ("next", "next"),
    ("react", "react"),
    ("vue", "vue"),
    ("svelte", "svelte"),
    ("@angular/core", "angular"),
)
_BUILD_TOOL_DEPS = (
    ("vite", "vite"),
    ("webpack", "webpack"),
    ("next", "next"),  # next.config.* covers this too; dep-based check first
    ("esbuild", "esbuild"),
)
_TEST_RUNNER_DEPS = (
    ("jest", "jest"),
    ("vitest", "vitest"),
    ("mocha", "mocha"),
    ("@playwright/test", "playwright"),
)
_DEV_SERVER_DEPS = (
    ("vite", "vite"),
    ("next", "next"),
    ("webpack-dev-server", "webpack-dev-server"),
)


def _js_facts(root: str) -> list[EnvironmentFact]:
    """
    package.json-derived facts. Real, not guessed: checks the ACTUAL
    dependency/devDependency keys, not filename heuristics -- a repo
    with vite.config.js but no `vite` dependency (a vendored config, a
    monorepo root) should not assert has_build_tool=vite.
    """
    pkg = _read_json(os.path.join(root, "package.json"))
    if pkg is None:
        return []

    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    facts: list[EnvironmentFact] = []

    for dep_name, value in _FRAMEWORK_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_framework", value))
            break  # first match wins -- a project has one primary framework claim,
            # not a set; ambiguous multi-framework repos are a real case this
            # first pass doesn't model, stated rather than silently guessed at.

    for dep_name, value in _BUILD_TOOL_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_build_tool", value))
            break

    for dep_name, value in _TEST_RUNNER_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_test_runner", value))
            break

    for dep_name, value in _DEV_SERVER_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_dev_server", value))
            break

    if os.path.isfile(os.path.join(root, "package-lock.json")):
        facts.append(EnvironmentFact("package_manager", "npm"))
    elif os.path.isfile(os.path.join(root, "yarn.lock")):
        facts.append(EnvironmentFact("package_manager", "yarn"))
    elif os.path.isfile(os.path.join(root, "pnpm-lock.yaml")):
        facts.append(EnvironmentFact("package_manager", "pnpm"))

    return facts


def _python_facts(root: str) -> list[EnvironmentFact]:
    """
    Language + test-runner + package-manager facts for a Python
    checkout. `language` is asserted whenever ANY real signal for it
    exists (pyproject.toml/requirements.txt/setup.py/setup.cfg) -- the
    weakest of this module's checks, deliberately: file presence alone,
    no dependency parsing, because Python's manifest format is not one
    file the way package.json is.
    """
    facts: list[EnvironmentFact] = []
    has_pyproject = os.path.isfile(os.path.join(root, "pyproject.toml"))
    has_requirements = os.path.isfile(os.path.join(root, "requirements.txt"))
    has_setup = (os.path.isfile(os.path.join(root, "setup.py"))
                 or os.path.isfile(os.path.join(root, "setup.cfg")))
    if has_pyproject or has_requirements or has_setup:
        facts.append(EnvironmentFact("language", "python"))

    if has_pyproject:
        try:
            with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = ""
        if "poetry" in content:
            facts.append(EnvironmentFact("package_manager", "poetry"))
        elif has_requirements:
            facts.append(EnvironmentFact("package_manager", "pip"))
    elif has_requirements:
        facts.append(EnvironmentFact("package_manager", "pip"))

    # Test-runner presence: real dependency-name check against
    # requirements.txt content, same discipline as _js_facts -- not
    # filename-only.
    combined_deps = ""
    if has_requirements:
        try:
            with open(os.path.join(root, "requirements.txt"), encoding="utf-8") as f:
                combined_deps = f.read().lower()
        except OSError:
            pass
    if "pytest" in combined_deps or os.path.isdir(os.path.join(root, "tests")):
        # Directory presence alone is a weaker signal than the dependency
        # check -- kept as an OR because many real repos (this one
        # included) put tests/ at the project root without pytest ever
        # appearing in a plain requirements.txt (it's in a separate
        # dev-requirements file, or installed ambiently). Flagging this
        # honestly rather than hiding the weaker branch: a caller that
        # needs high precision should prefer the dependency-based signal
        # and treat the directory-only case as advisory.
        facts.append(EnvironmentFact("has_test_runner", "pytest"))

    return facts


# Exact pins only (`name==X.Y[.Z]`) -- a range spec (`>=`, `~=`, unpinned)
# states no single real version and is skipped, honestly, rather than
# guessed at. Deterministic text parsing, same discipline as the rest of
# this module: no pip/package-manager execution just to learn a version.
_REQUIREMENTS_PIN_RE = re.compile(
    r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([0-9]+(?:\.[0-9]+){1,2})\s*(?:[;#].*)?$"
)


def _requirements_pinned_versions(root: str) -> list[EnvironmentFact]:
    """
    package_version facts from requirements.txt exact pins. One
    predicate ("package_version"), many objects -- "{name}:{version}" --
    so multiple packages can each get their own fact without the fixed
    PROBE_PREDICATE_VOCABULARY tuple needing one entry per package name.

    NOT wired through assert_environment_claims()'s DB write path:
    that function is idempotent per (subject, predicate), i.e. ONE
    current value per predicate per project -- fine for
    has_framework/language, wrong for package_version, where a repo
    legitimately pins many packages at once. These facts are consumed
    in-memory (see invariant_bindings_from_facts below) by the real
    callers that need them (find_best_way, LocalAgentRunner); persisting
    them as claims is a real follow-up, not solved here.
    """
    path = os.path.join(root, "requirements.txt")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    facts: list[EnvironmentFact] = []
    for line in lines:
        match = _REQUIREMENTS_PIN_RE.match(line)
        if not match:
            continue
        name, version = match.group(1).lower(), match.group(2)
        facts.append(EnvironmentFact("package_version", f"{name}:{version}"))
    return facts


_VERSION_MAJOR_MINOR_RE = re.compile(r"^(\d+)\.(\d+)")


def invariant_bindings_from_facts(facts: list[EnvironmentFact]) -> dict[str, float]:
    """
    The connective tissue this substrate was missing: converts probed
    package_version facts into the `invariant_bindings` dict
    check_hard_constraints()/invariants.py actually consumes, so a
    procedure's real numeric invariant (e.g.
    {"kind": "numeric", "expr": "pandas_version >= 2.0"}) can be
    evaluated against a real repository -- NOT via preconditions'
    exact-equality matching, which cannot express `>=` at all (see
    invariants.py's module docstring for why).

    Binding name convention: "{package}_version" (e.g. "pandas_version"),
    matching the plain identifier invariants.py's expression parser
    requires (no dots, no colons -- ast.Name only allows [A-Za-z_][\\w]*).

    REAL, STATED LIMITATION: only major.minor is kept, as a plain float
    (2.1.0 -> 2.1). Correct for the common "compare against a
    major.minor threshold" case this module was built to prove, but it
    collapses patch versions (2.1.0 and 2.1.9 bind identically) and
    breaks ordering once minor reaches two digits (2.10 parses as the
    float 2.1, sorting behind 2.9). A real limitation of this first
    pass, not a hidden one -- full semver comparison is a real follow-up.
    """
    bindings: dict[str, float] = {}
    for fact in facts:
        if fact.predicate != "package_version":
            continue
        name, _, version = fact.object.partition(":")
        match = _VERSION_MAJOR_MINOR_RE.match(version)
        if not match or not name:
            continue
        bindings[f"{name}_version"] = float(f"{match.group(1)}.{match.group(2)}")
    return bindings


def probe_environment(repo_root: str) -> list[EnvironmentFact]:
    """
    Pure, synchronous, filesystem-only. Every predicate returned here is
    in PROBE_PREDICATE_VOCABULARY by construction -- there is no other
    code path that produces an EnvironmentFact, so this function cannot
    drift out of sync with the vocabulary it defines.
    """
    if not os.path.isdir(repo_root):
        return []
    facts = _js_facts(repo_root) + _python_facts(repo_root) + _requirements_pinned_versions(repo_root)
    # De-duplicate same (predicate, object) pairs a repo might trigger
    # from more than one heuristic (e.g. package_manager asserted once
    # is enough); order-preserving, not a set, so facts stay reproducible
    # for tests that assert exact output.
    seen: set[tuple[str, str]] = set()
    deduped: list[EnvironmentFact] = []
    for fact in facts:
        key = (fact.predicate, fact.object)
        if key not in seen:
            seen.add(key)
            deduped.append(fact)
    return deduped
