"""
Real tests for app/services/environment_probe.py -- against real temp
directory fixtures with real files (same convention as
test_import_deps.py/test_related_tests.py), and this repo's OWN real
files where that's the stronger test.
"""
import json
from pathlib import Path

from app.services.environment_probe import (
    PROBE_PREDICATE_VOCABULARY,
    EnvironmentFact,
    invariant_bindings_from_facts,
    probe_environment,
)


def _write_json(root: Path, rel_path: str, data: dict) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data))


def _write(root: Path, rel_path: str, content: str = "") -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_every_real_fact_predicate_is_in_the_vocabulary(tmp_path):
    """The vocabulary V1 validates against must actually cover
    everything this module can produce -- structurally guaranteed by
    probe_environment() only ever constructing EnvironmentFact from
    these helper functions, checked here as a real regression test
    rather than trusted by inspection alone."""
    _write_json(tmp_path, "package.json", {
        "dependencies": {"react": "^18.0.0", "next": "^14.0.0"},
        "devDependencies": {"vitest": "^1.0.0"},
    })
    _write(tmp_path, "package-lock.json")
    _write(tmp_path, "requirements.txt", "pytest\n")
    _write(tmp_path, "pyproject.toml", "[tool.poetry]\n")

    facts = probe_environment(str(tmp_path))
    assert facts, "fixture should have produced at least one fact"
    for fact in facts:
        assert fact.predicate in PROBE_PREDICATE_VOCABULARY


def test_missing_manifests_produce_no_facts(tmp_path):
    assert probe_environment(str(tmp_path)) == []


def test_nonexistent_root_produces_no_facts():
    assert probe_environment("/does/not/exist/anywhere") == []


def test_framework_asserted_only_from_real_dependency_not_filename(tmp_path):
    """A vendored/stray config file must not fabricate a framework
    claim -- only a real dependency entry does."""
    _write(tmp_path, "vite.config.js", "export default {}")
    _write_json(tmp_path, "package.json", {"dependencies": {}})
    facts = probe_environment(str(tmp_path))
    assert EnvironmentFact("has_build_tool", "vite") not in facts


def test_js_project_real_facts(tmp_path):
    _write_json(tmp_path, "package.json", {
        "dependencies": {"next": "^14.0.0"},
        "devDependencies": {"jest": "^29.0.0"},
    })
    _write(tmp_path, "pnpm-lock.yaml")

    facts = {(f.predicate, f.object) for f in probe_environment(str(tmp_path))}
    assert ("has_framework", "next") in facts
    assert ("has_build_tool", "next") in facts
    assert ("has_dev_server", "next") in facts
    assert ("has_test_runner", "jest") in facts
    assert ("package_manager", "pnpm") in facts


def test_python_project_real_facts(tmp_path):
    _write(tmp_path, "requirements.txt", "pytest\nasyncpg\n")
    facts = {(f.predicate, f.object) for f in probe_environment(str(tmp_path))}
    assert ("language", "python") in facts
    assert ("package_manager", "pip") in facts
    assert ("has_test_runner", "pytest") in facts


def test_probe_is_deterministic_and_deduplicated(tmp_path):
    _write_json(tmp_path, "package.json", {"dependencies": {"react": "^18.0.0"}})
    _write(tmp_path, "package-lock.json")

    first = probe_environment(str(tmp_path))
    second = probe_environment(str(tmp_path))
    assert first == second
    seen = [(f.predicate, f.object) for f in first]
    assert len(seen) == len(set(seen))


def test_against_this_repos_own_backend():
    """Real regression against this repo's own real files, same
    discipline import_deps.py's tests used to catch a real bug against
    real files rather than synthetic fixtures alone."""
    import os
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    facts = {(f.predicate, f.object) for f in probe_environment(backend_root)}
    assert ("language", "python") in facts
    assert ("has_test_runner", "pytest") in facts


def test_pinned_requirements_produce_package_version_facts(tmp_path):
    """Phase 3, Step 1: the failing probe test the plan calls for --
    a real requirements.txt pin must produce a real package_version
    fact, using the fixed 'package_version' predicate (not a
    per-package predicate name, which would need one vocabulary entry
    per package ever seen)."""
    _write(tmp_path, "requirements.txt", "pandas==2.1.0\nrequests>=2.28.0\n")
    facts = {(f.predicate, f.object) for f in probe_environment(str(tmp_path))}
    assert ("package_version", "pandas:2.1.0") in facts
    # requests is a range spec (>=), not an exact pin -- states no single
    # real version, so it must NOT be guessed at as a package_version fact.
    assert not any(obj.startswith("requests:") for pred, obj in facts if pred == "package_version")


def test_unpinned_or_ranged_requirements_produce_no_version_fact(tmp_path):
    _write(tmp_path, "requirements.txt", "pandas\npandas~=2.0\npandas>=2.0,<3.0\n")
    facts = [f for f in probe_environment(str(tmp_path)) if f.predicate == "package_version"]
    assert facts == []


def test_package_version_predicate_is_in_the_vocabulary():
    assert "package_version" in PROBE_PREDICATE_VOCABULARY


def test_environment_facts_module_never_imports_the_database():
    """Structural boundary test, same discipline as
    test_local_agent_runner_offline.py's AST check: environment_facts.py
    is the pure half app.local_agent.runner depends on, so it must never
    import asyncpg or app.services.claims/state/embeddings -- any of
    which would drag asyncpg back in transitively and reintroduce the
    exact bug this split fixed (see environment_facts.py's own
    docstring)."""
    import ast
    import inspect

    import app.services.environment_facts as ef_module

    tree = ast.parse(inspect.getsource(ef_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    forbidden = {"asyncpg", "app.services.claims", "app.services.state", "app.services.embeddings"}
    hit = forbidden & imported_names
    assert not hit, f"environment_facts.py must stay dependency-free, found: {hit}"


def test_invariant_bindings_from_pandas_pin():
    facts = [EnvironmentFact("package_version", "pandas:2.1.0")]
    assert invariant_bindings_from_facts(facts) == {"pandas_version": 2.1}


def test_invariant_bindings_ignores_non_version_facts():
    facts = [EnvironmentFact("language", "python"), EnvironmentFact("package_manager", "pip")]
    assert invariant_bindings_from_facts(facts) == {}


def test_invariant_bindings_from_multiple_packages():
    facts = [
        EnvironmentFact("package_version", "pandas:2.1.0"),
        EnvironmentFact("package_version", "numpy:1.26.0"),
    ]
    assert invariant_bindings_from_facts(facts) == {"pandas_version": 2.1, "numpy_version": 1.26}


def test_against_this_repos_own_frontend():
    import os
    frontend_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
    )
    if not os.path.isdir(frontend_root):
        return  # frontend/ isn't present in every checkout of this repo
    facts = {(f.predicate, f.object) for f in probe_environment(frontend_root)}
    assert ("has_framework", "next") in facts
    assert ("package_manager", "npm") in facts
