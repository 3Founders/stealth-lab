"""Proving tests for app/execution/artifact_validation.py -- the general
post-execution artifact validation gate. No DB, no LLM, no network: real
files on a real tmp_path repo, a real subprocess import check against
THIS process's own interpreter.

Exact failure mode covered: a D1 rehearsal (max_steps=40) produced a real
patch and was accepted as success even though the generated module
referenced `pydantic.CreateModel`, which does not exist -- syntactically
valid, semantically broken, unimportable. `ast.parse` alone would not have
caught it; these tests prove the real import check does, and that a
genuinely working module still passes.
"""
from __future__ import annotations

from app.execution.artifact_validation import (
    ArtifactValidationResult,
    gate_execution_success,
    validate_edited_files,
)


def test_a_syntactically_valid_but_unimportable_module_fails_validation(tmp_path):
    """The exact D1 failure mode: `from pydantic import CreateModel` (or
    any nonexistent attribute) is grammatically valid Python -- ast.parse
    accepts it -- but does not exist. Only a real import surfaces this."""
    (tmp_path / "broken_mod.py").write_text(
        "from os import DefinitelyNotARealAttribute\n"
    )

    result = validate_edited_files(str(tmp_path), ["broken_mod.py"])

    assert result.validated is False
    assert "broken_mod" in result.reason
    assert result.kind == "python_module"


def test_a_genuinely_importable_module_passes_validation(tmp_path):
    (tmp_path / "good_mod.py").write_text("import os\n\nVALUE = 1 + 1\n")

    result = validate_edited_files(str(tmp_path), ["good_mod.py"])

    assert result.validated is True
    assert result.kind == "aggregate"


def test_a_file_that_does_not_even_parse_fails_before_any_import_attempt(tmp_path):
    (tmp_path / "unparseable.py").write_text("def broken(:\n    pass\n")

    result = validate_edited_files(str(tmp_path), ["unparseable.py"])

    assert result.validated is False
    assert "does not parse" in result.reason


def test_a_nonpython_file_has_no_registered_validator_and_is_never_blocked(tmp_path):
    (tmp_path / "notes.md").write_text("# just some prose, not an artifact type this checks\n")

    result = validate_edited_files(str(tmp_path), ["notes.md"])

    assert result.validated is True
    assert result.kind == "none"


def test_no_files_edited_is_honestly_nothing_to_validate(tmp_path):
    result = validate_edited_files(str(tmp_path), [])
    assert result.validated is True
    assert result.kind == "none"


def test_first_real_failure_wins_even_with_a_passing_file_first(tmp_path):
    (tmp_path / "good_mod.py").write_text("import os\n")
    (tmp_path / "broken_mod.py").write_text(
        "from os import DefinitelyNotARealAttribute\n"
    )

    result = validate_edited_files(str(tmp_path), ["good_mod.py", "broken_mod.py"])

    assert result.validated is False
    assert "broken_mod" in result.reason


def test_a_path_escaping_the_repo_root_is_refused_not_silently_checked(tmp_path):
    # A malformed files_edited entry (upstream data-quality issue, not this
    # gate's job to police) must never let validation read outside the
    # real repo root it was scoped to.
    outside = tmp_path.parent / "sibling_secret.py"
    outside.write_text("import os\n")

    result = validate_edited_files(str(tmp_path), ["../sibling_secret.py"])

    assert result.validated is False
    assert result.kind == "python_module"


def test_gate_never_overturns_a_declared_failure_into_a_success(tmp_path):
    """A run the raw execution mechanism already called a failure must
    never be laundered into a success just because some file happens to
    import cleanly -- there is nothing to validate on a failed run."""
    (tmp_path / "good_mod.py").write_text("import os\n")

    gated, reason = gate_execution_success(
        repo_root=str(tmp_path), declared_success=False, files_edited=["good_mod.py"],
    )

    assert gated is False
    assert reason is None


def test_gate_downgrades_a_declared_success_that_fails_real_validation(tmp_path):
    """The core regression: the OLD unsafe behavior (accept a 'finished'
    agent run with a non-empty patch as success, with no artifact
    inspection at all) must no longer be possible for a Python artifact
    this gate can actually check."""
    (tmp_path / "broken_mod.py").write_text(
        "from os import DefinitelyNotARealAttribute\n"
    )

    gated, reason = gate_execution_success(
        repo_root=str(tmp_path), declared_success=True, files_edited=["broken_mod.py"],
    )

    assert gated is False
    assert reason is not None
    assert "broken_mod" in reason


def test_gate_keeps_a_legitimate_declared_success_that_passes_real_validation(tmp_path):
    """Proves the fix does not weaken anything: a real, working artifact
    must still be recorded as success exactly as before."""
    (tmp_path / "good_mod.py").write_text("import os\n\nVALUE = 21 * 2\n")

    gated, reason = gate_execution_success(
        repo_root=str(tmp_path), declared_success=True, files_edited=["good_mod.py"],
    )

    assert gated is True
    assert reason is None


def test_gate_with_no_files_edited_at_all_keeps_a_declared_success():
    """A success with nothing to check (e.g. a no-op/informational step)
    must not be blocked by a gate that has nothing to validate."""
    gated, reason = gate_execution_success(
        repo_root="/does/not/matter/here", declared_success=True, files_edited=[],
    )
    assert gated is True
    assert reason is None


def test_result_is_a_plain_frozen_dataclass_shape():
    result = ArtifactValidationResult(validated=True, reason="ok", kind="none")
    assert result.validated is True
    assert result.reason == "ok"
    assert result.kind == "none"
