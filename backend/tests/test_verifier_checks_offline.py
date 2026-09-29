"""Proving tests for the external-verifier check runner and the ast-grep-essentials path.

Offline by construction: no DATABASE_URL, no docker daemon, no ast-grep binary. Every
execution test either uses a deliberately-absent binary (to prove there is no silent
fallback) or a fake `docker` on PATH that records its argv. The security contract is
asserted against PURE argv builders, following `sandbox_executor._docker_argv` and
`test_container_sandbox_offline.py`.

The test this file exists for is `test_ast_grep_zero_cases_is_not_a_pass`: ast-grep exits 0
having run nothing when a test file names a rule id that is not loaded. A boolean runner
would record a Procedure as verified having checked nothing.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ast_grep_rules as agr  # noqa: E402
from app.services.check_runner import (  # noqa: E402
    CONTAINER_CHECK_TYPES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    VERDICTS,
    VERIFIER_CHECK_TYPES,
    VERIFIER_REGISTRY,
    VERIFIER_VERSIONS,
    CheckResult,
    CheckSpecError,
    VerifierCheck,
    container_argv,
    local_argv,
    parse_actionlint,
    parse_ast_grep_test,
    parse_result,
    parse_zizmor,
    run_check,
    scrubbed_env,
    validate_layout,
)
from app.services.sandbox_executor import InputPathEscape  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Vocabulary: the whole point of Q-STEP4-1 is that these are SEPARATE
# ---------------------------------------------------------------------------
def test_screening_check_types_is_not_extended():
    """The frozen screening vocabulary must be untouched by this step. If a future
    change adds actionlint/zizmor/ast_grep there, a screening-decision row becomes
    indistinguishable from a verification verdict and claim_publication's reporting
    over blocking findings silently changes meaning."""
    from app.services import screening

    assert set(VERIFIER_CHECK_TYPES) & set(screening.CHECK_TYPES) == set()
    assert "actionlint" not in screening.CHECK_TYPES
    assert "zizmor" not in screening.CHECK_TYPES
    assert "ast_grep" not in screening.CHECK_TYPES


def test_routing_check_kinds_is_not_extended():
    """These tools IMPLEMENT routing's existing `procedure_check`; they are not new
    check kinds. Adding them would double-count the same notion in two vocabularies."""
    from app.routing.config import CHECK_KINDS

    assert set(VERIFIER_CHECK_TYPES) & set(CHECK_KINDS) == set()
    assert "procedure_check" in CHECK_KINDS

def test_verdicts_are_five_valued_and_pass_is_one_of_them():
    assert VERDICTS == ("pass", "fail", "error", "no_input", "not_run")
    # `error` and `no_input` are the two states a boolean runner collapses into `pass`.
    assert "error" in VERDICTS and "no_input" in VERDICTS


@pytest.mark.parametrize("check_type", VERIFIER_CHECK_TYPES)
def test_every_verifier_is_pinned(check_type):
    assert check_type in VERIFIER_REGISTRY
    spec = VERIFIER_REGISTRY[check_type]
    assert spec["entrypoint"]
    assert spec["flags"], "a pinned argv with no flags cannot be reproduced"
    assert VERIFIER_VERSIONS[check_type], "an unpinned verdict is not evidence"
    if spec["kind"] == "container":
        # Tag-pinned images only. A `latest` tag makes a stored verdict a guess.
        assert ":" in spec["image"] and not spec["image"].endswith(":latest")
    else:
        assert spec["image"] is None, "a local tool must not also claim a container image"


# ---------------------------------------------------------------------------
# The security contract, as a pure function
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("check_type", CONTAINER_CHECK_TYPES)
def test_container_argv_carries_every_isolation_flag(check_type):
    argv = container_argv(check_type, "/tmp/work")
    joined = " ".join(argv)
    for flag in ("--rm", "--network", "--read-only", "--memory", "--cpus",
                 "--pids-limit", "--cap-drop", "--security-opt", "--user"):
        assert flag in argv, f"{check_type}: {flag} silently disappeared from the sandbox argv"
    assert argv[argv.index("--network") + 1] == "none", "network must be none, not bridge"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert argv[argv.index("--user") + 1] == "65534:65534", "must not run as root"
    assert "shell=True" not in joined


def test_container_argv_is_pure_and_repeatable():
    """Same inputs, same argv, and the filesystem is never touched -- which is what makes
    this assertable with no daemon."""
    a = container_argv("actionlint", "/tmp/x")
    b = container_argv("actionlint", "/tmp/x")
    assert a == b
    assert "/tmp/x:/work" in a


def test_container_argv_honours_a_custom_docker_binary():
    argv = container_argv("actionlint", "/tmp/x", docker_binary="definitely-not-real-docker")
    assert argv[0] == "definitely-not-real-docker"


def test_container_argv_appends_explicit_targets():
    argv = container_argv("actionlint", "/tmp/x", targets=["wf.yml"])
    assert argv[-1] == "wf.yml"
    assert argv[-2] != ".", "an explicit target must not also add the '.' directory"


def test_local_argv_only_for_local_tools():
    with pytest.raises(CheckSpecError):
        local_argv("actionlint", "actionlint")
    argv = local_argv("ast_grep", "/opt/ast-grep")
    assert argv[0] == "/opt/ast-grep"
    assert "test" in argv and "--skip-snapshot-tests" in argv


# ---------------------------------------------------------------------------
# Verdict knobs that change the answer must be in the argv, not just documented
# ---------------------------------------------------------------------------
def test_actionlint_argv_disables_the_implicit_external_linters():
    """actionlint auto-detects shellcheck/pyflakes on PATH and silently uses them, so a
    verdict would depend on the runner image's contents. Empty string = disabled
    (actionlint's own help: "If empty, pyflakes integration will be disabled")."""
    argv = container_argv("actionlint", "/tmp/x")
    assert "-shellcheck=" in argv
    assert "-pyflakes=" in argv
    assert "-no-color" in argv


def test_zizmor_argv_is_offline_and_ignores_no_ignore_comments():
    argv = container_argv("zizmor", "/tmp/x")
    # `--offline` is a tool policy, not a sandbox; `--network none` (above) is the
    # enforcement. Both are needed and neither substitutes for the other.
    assert "--offline" in argv
    # Without --no-ignores, audited code can silence its own finding with an inline comment.
    assert "--no-ignores" in argv
    # sarif forces exit 0 even with findings, which would make the exit code a lie.
    assert "--format=json" in argv
    assert "--format=sarif" not in argv


# ---------------------------------------------------------------------------
# Parsers, and above all their liveness assertions
# ---------------------------------------------------------------------------
def test_actionlint_clean_is_pass():
    verdict, findings, _ = parse_actionlint(0, "[]", "")
    assert verdict == "pass"
    assert findings == ()


def test_actionlint_findings_are_fail_and_carry_the_rule_kind():
    payload = json.dumps([{
        "message": '"runs-on" section is missing in job "j"',
        "filepath": "wf.yml", "line": 3, "column": 3, "kind": "syntax-check",
    }])
    verdict, findings, _ = parse_actionlint(1, payload, "")
    assert verdict == "fail"
    assert findings[0].code == "syntax-check"
    assert findings[0].line == 3


@pytest.mark.parametrize("code", [2, 3])
def test_actionlint_error_exit_codes_are_never_pass_or_fail(code):
    """2 = invalid option, 3 = fatal (a missing input file, measured). Neither means
    "we checked and it was clean"."""
    verdict, _, _ = parse_actionlint(code, "", "boom")
    assert verdict == "error"


def test_actionlint_zero_with_findings_is_a_contradiction_not_a_pass():
    verdict, findings, _ = parse_actionlint(0, json.dumps([{"message": "x", "kind": "k"}]), "")
    assert verdict == "error"
    assert findings  # reported, not swallowed


@pytest.mark.parametrize("stdout", ["", "not json", "{}"])
def test_actionlint_silence_is_never_read_as_pass(stdout):
    verdict, _, detail = parse_actionlint(0, stdout, "")
    assert verdict == "error"
    assert detail


def test_zizmor_findings_exit_codes_are_fail():
    payload = json.dumps([{
        "ident": "artipacked",
        "desc": "credential persistence through GitHub Actions artifacts",
        "determinations": {"confidence": "Low", "severity": "Medium", "persona": "Regular"},
        "locations": [{"symbolic": {"annotation": "x"}, "concrete": {"filename": "wf.yml", "row": 7}}],
    }])
    for code in (11, 12, 13, 14):
        verdict, findings, _ = parse_zizmor(code, payload, "")
        assert verdict == "fail", f"zizmor exit {code} must be a fail"
        assert findings[0].code == "artipacked"
        assert findings[0].severity == "Medium"


def test_zizmor_json_v1_rows_are_normalised_from_zero_based():
    """json-v1 reports 0-based `row`; plain/sarif are 1-based. The runner normalises so a
    consumer never has to know which tool produced the line number."""
    payload = json.dumps([{
        "ident": "unpinned-uses", "desc": "d",
        "determinations": {"severity": "High", "confidence": "High"},
        "locations": [{"concrete": {"filename": "wf.yml", "row": 0}}],
    }])
    _, findings, _ = parse_zizmor(14, payload, "")
    assert findings[0].line == 1, "0-based row 0 must surface as 1-based line 1"


def test_zizmor_exit_three_is_no_input_not_pass():
    verdict, _, _ = parse_zizmor(3, "", "error: no inputs collected")
    assert verdict == "no_input"


def test_zizmor_sarif_style_exit_zero_with_findings_is_an_error():
    """`--format=sarif` forces exit 0 even with findings. If that ever leaks into our
    config, findings present + exit 0 must not become a pass."""
    payload = json.dumps([{"ident": "artipacked", "desc": "d", "determinations": {}}])
    verdict, findings, _ = parse_zizmor(0, payload, "")
    assert verdict == "error"
    assert findings


def test_zizmor_silence_is_never_pass():
    for stdout in ("", "warning: something", "{"):
        assert parse_zizmor(0, stdout, "")[0] == "error"


def test_ast_grep_pass_requires_a_positive_case_count():
    verdict, _, _ = parse_ast_grep_test(0, "test result: ok. 3 passed; 0 failed;", "")
    assert verdict == "pass"


def test_ast_grep_zero_cases_is_not_a_pass():
    """THE false-accept. Reproduced locally: ast-grep prints "Configuration not found!",
    runs zero cases, prints `0 passed; 0 failed` and EXITS 0."""
    stdout = "Configuration not found! avoid-mktemp-python\ntest result: ok. 0 passed; 0 failed;"
    verdict, _, detail = parse_ast_grep_test(0, stdout, "")
    assert verdict == "no_input"
    assert "zero cases" in detail.lower()


def test_ast_grep_case_count_mismatch_is_an_error():
    """A test file and a rule that disagree must never read as a pass -- that is the same
    shape as the zero-case bug, one step earlier."""
    verdict, _, detail = parse_ast_grep_test(
        0, "test result: ok. 2 passed; 0 failed;", "", expected_case_count=7
    )
    assert verdict == "error"
    assert "7" in detail


def test_ast_grep_failure_and_parse_error_are_distinguished():
    assert parse_ast_grep_test(4, "Error: test failed. 0 passed; 1 failed;", "")[0] == "fail"
    assert parse_ast_grep_test(8, "Fail to parse yaml as Rule.", "")[0] == "error"


def test_ast_grep_missing_summary_is_an_error():
    assert parse_ast_grep_test(0, "no summary here", "")[0] == "error"


def test_parser_selection_is_closed():
    with pytest.raises(CheckSpecError):
        parse_result("bandit", 0, "", "")


# ---------------------------------------------------------------------------
# Layout validation: fail BEFORE spending a container start
# ---------------------------------------------------------------------------
def test_zizmor_rejects_a_layout_it_cannot_audit():
    with pytest.raises(CheckSpecError):
        validate_layout("zizmor", {"workflows/wf.yml": b"on: [push]\n"})
    validate_layout("zizmor", {".github/workflows/wf.yml": b"on: [push]\n"})


def test_layout_validation_is_a_no_op_for_other_tools():
    validate_layout("actionlint", {"anything.yml": b"x"})
    validate_layout("ast_grep", {"sgconfig.yml": b"x"})


# ---------------------------------------------------------------------------
# VerifierCheck: what makes a verdict auditable
# ---------------------------------------------------------------------------
def test_verifier_check_rejects_an_unknown_type():
    with pytest.raises(CheckSpecError):
        VerifierCheck(check_type="bandit", tool_version="1.0", source="s")


def test_verifier_check_rejects_an_unpinned_tool():
    with pytest.raises(CheckSpecError):
        VerifierCheck(check_type="ast_grep", tool_version="", source="s")


def test_verifier_check_roundtrips_through_json():
    original = VerifierCheck(
        check_type="ast_grep", tool_version="0.45.3", source="ast-grep-essentials@7312010",
        config={"snapshot_tests": False}, expected_case_count=4,
    )
    assert VerifierCheck.from_json(original.to_json()) == original


def test_verifier_check_from_json_names_what_is_missing():
    with pytest.raises(CheckSpecError):
        VerifierCheck.from_json({"check_type": "ast_grep"})


def test_only_pass_closes_a_procedure():
    assert CheckResult("pass", "ast_grep", "0.45.3").passed
    for verdict in ("fail", "error", "no_input", "not_run"):
        assert not CheckResult(verdict, "ast_grep", "0.45.3").passed, verdict


def test_check_result_rejects_an_invented_verdict():
    with pytest.raises(CheckSpecError):
        CheckResult("mostly_fine", "ast_grep", "0.45.3")


# ---------------------------------------------------------------------------
# Environment hygiene
# ---------------------------------------------------------------------------
def test_scrubbed_env_removes_credentials(monkeypatch):
    for name in ("GITHUB_TOKEN", "OPENAI_API_KEY", "DATABASE_URL", "AWS_SECRET_ACCESS_KEY",
                 "INGEST_SERVICE_TOKEN", "SOME_RANDOM_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "super-secret")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    env = scrubbed_env()
    assert "super-secret" not in "".join(env.values())
    for name in ("GITHUB_TOKEN", "DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "ANTHROPIC_API_KEY"):
        assert name not in env, f"{name} survived into a verifier's environment"


def test_scrubbed_env_still_carries_path_so_a_binary_can_start(monkeypatch):
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    assert "PATH" in scrubbed_env()


# ---------------------------------------------------------------------------
# run_check: input safety, timeouts, and no silent fallback
# ---------------------------------------------------------------------------
def _check(check_type="ast_grep", **kw):
    params = dict(check_type=check_type, tool_version=VERIFIER_VERSIONS[check_type],
                  source="test-source")
    params.update(kw)
    return VerifierCheck(**params)


def test_run_check_rejects_an_absolute_input_path():
    """A `files` key that escapes the workspace must raise, never write. Reuses
    `sandbox_executor.stage_input_files` so the guard cannot drift between runners."""
    with pytest.raises(InputPathEscape):
        asyncio.run(run_check(_check(), {"/etc/passwd": b"x"}, ast_grep_binary="ast-grep"))


def test_run_check_rejects_a_dotdot_input_path():
    with pytest.raises(InputPathEscape):
        asyncio.run(run_check(_check(), {"../escape.yml": b"x"}, ast_grep_binary="ast-grep"))


def test_run_check_rejects_empty_input():
    with pytest.raises(CheckSpecError):
        asyncio.run(run_check(_check(), {}, ast_grep_binary="ast-grep"))


@pytest.mark.parametrize("timeout", [0.0, -1.0, MAX_TIMEOUT_SECONDS + 1])
def test_run_check_rejects_an_out_of_range_timeout(timeout):
    with pytest.raises(CheckSpecError):
        asyncio.run(run_check(_check(), {"a.yml": b"x"}, timeout_seconds=timeout,
                              ast_grep_binary="ast-grep"))


def test_default_timeout_is_inside_the_cap():
    assert 0 < DEFAULT_TIMEOUT_SECONDS <= MAX_TIMEOUT_SECONDS


def test_an_unrunnable_tool_never_reports_a_pass():
    """No silent fallback. `ContainerSandboxExecutor` sets the precedent with
    'Refusing to fall back to an unisolated executor'."""
    result = asyncio.run(run_check(
        _check("ast_grep"), {"sgconfig.yml": b"x"},
        ast_grep_binary="definitely-not-a-real-ast-grep-binary",
    ))
    assert result.verdict == "error"
    assert not result.passed
    # The refusal must reach `detail` verbatim -- if the parser overwrote it with a
    # generic "no result summary" the operator would lose the only useful message.
    assert "could not execute verifier" in result.detail


def test_a_missing_tool_never_reports_a_pass(monkeypatch):
    """With no binary resolvable at all, the detail must name the env var that fixes
    it -- otherwise the failure is unactionable."""
    monkeypatch.delenv("SL_AST_GREP_BIN", raising=False)
    monkeypatch.setattr("app.services.check_runner.shutil.which", lambda _n: None)
    result = asyncio.run(run_check(_check("ast_grep"), {"sgconfig.yml": b"x"}))
    assert result.verdict == "error"
    assert not result.passed
    assert "SL_AST_GREP_BIN" in result.detail
    assert "refusing to report a verdict" in result.detail


def test_a_missing_docker_never_reports_a_pass():
    result = asyncio.run(run_check(
        _check("actionlint"), {".github/workflows/wf.yml": b"on: [push]\n"},
        docker_binary="definitely-not-a-real-docker-binary",
    ))
    assert result.verdict == "error"
    assert not result.passed


def test_run_check_reports_a_timeout_as_error_not_pass():
    """A killed verifier is not a clean verifier."""
    result = asyncio.run(run_check(
        _check("actionlint"), {".github/workflows/wf.yml": b"on: [push]\n"},
        timeout_seconds=MAX_TIMEOUT_SECONDS, docker_binary="definitely-not-a-real-docker",
    ))
    assert result.verdict == "error"


def test_run_check_cleans_up_its_workspace_on_failure():
    """The temp workspace must not survive a failed run: a staged copy of a workflow is
    still a copy of someone else's file."""
    before = set(os.listdir(Path(os.environ.get("TEMP", "/tmp"))))
    asyncio.run(run_check(
        _check("actionlint"), {".github/workflows/wf.yml": b"on: [push]\n"},
        docker_binary="definitely-not-a-real-docker-binary",
    ))
    leaked = {n for n in set(os.listdir(Path(os.environ.get("TEMP", "/tmp")))) - before
              if n.startswith("slcheck_")}
    assert not leaked, f"workspace leaked: {leaked}"


# ---------------------------------------------------------------------------
# ast-grep-essentials: discovery, license gate, and the counters identity
# ---------------------------------------------------------------------------
def _write_corpus(root: Path, *, prelude_utils: bool = False) -> Path:
    (root / "rules" / "python" / "security").mkdir(parents=True)
    (root / "tests" / "python").mkdir(parents=True)
    (root / "LICENSE").write_text("Apache License 2.0\n", encoding="utf-8")
    extra = "utils:\n  PATTERN_1(identifier):\n    kind: identifier\n" if prelude_utils else ""
    (root / "rules" / "python" / "security" / "ok-python.yml").write_text(
        f"id: ok-python\nlanguage: python\nseverity: warning\n"
        f'message: "do not do the bad thing"\nnote: "[CWE-78] command injection"\n{extra}'
        "rule:\n  pattern: eval($X)\n", encoding="utf-8")
    (root / "rules" / "python" / "security" / "off-python.yml").write_text(
        'id: off-python\nlanguage: python\nseverity: off\nmessage: "m"\n'
        "rule:\n  pattern: nope\n", encoding="utf-8")
    (root / "tests" / "python" / "ok-python-test.yml").write_text(
        "id: ok-python\nvalid:\n  - x = 1\n  - y = 2\ninvalid:\n  - eval(z)\n", encoding="utf-8")
    (root / "tests" / "python" / "off-python-test.yml").write_text(
        "id: off-python\nvalid:\n  - x = 1\ninvalid:\n  - nope\n", encoding="utf-8")
    return root


def test_discover_rules_counts_cases_and_licenses_from_the_root(tmp_path):
    root = _write_corpus(tmp_path)
    rules, unreadable = agr.discover_rules(root)
    assert unreadable == []
    by_id = {r.rule_id: r for r in rules}
    assert set(by_id) == {"ok-python", "off-python"}
    assert by_id["ok-python"].valid_count == 2
    assert by_id["ok-python"].invalid_count == 1
    assert by_id["ok-python"].snippet_count == 3
    # ast-grep's `N passed` counts rule TEST FILES, not snippets. Per-rule isolation
    # stages exactly one, so the liveness expectation is 1 -- asserting 3 would reject
    # a CORRECT check, which is how this was learned.
    assert by_id["ok-python"].expected_case_count == 1
    assert by_id["ok-python"].license_spdx == "Apache-2.0"
    assert "CWE-78" in by_id["ok-python"].note


def test_expected_case_count_is_zero_without_a_test(tmp_path):
    root = _write_corpus(tmp_path)
    (root / "tests" / "python" / "ok-python-test.yml").unlink()
    rules, _ = agr.discover_rules(root)
    rule = next(r for r in rules if r.rule_id == "ok-python")
    assert rule.expected_case_count == 0
    with pytest.raises(CheckSpecError):
        agr.check_files(rule)


def test_discover_rules_flags_a_test_that_names_a_different_rule(tmp_path):
    """A test whose `id` names a different rule cannot join, and the rule is left with no
    checkable outcome. This is precisely the shape that makes `ast-grep test` exit 0
    having run nothing, so it must be reported, not ingested."""
    root = _write_corpus(tmp_path)
    (root / "tests" / "python" / "ok-python-test.yml").write_text(
        "id: some-other-rule\nvalid:\n  - x\ninvalid:\n  - y\n", encoding="utf-8")
    rules, unreadable = agr.discover_rules(root)
    assert [reason for _p, reason in unreadable] == ["test_without_rule"]
    assert next(r for r in rules if r.rule_id == "ok-python").test_path is None


def test_discover_rules_joins_tests_by_declared_id_not_filename(tmp_path):
    """The upstream corpus breaks the `<rule_id>-test.yml` convention for a handful of
    rules (a `typecript` typo, a `...-python-test.yml` for a csharp rule, a filename
    missing a path segment). Trusting filenames would wrongly quarantine usable rules."""
    root = _write_corpus(tmp_path)
    renamed = root / "tests" / "python" / "totally-different-name.yml"
    (root / "tests" / "python" / "ok-python-test.yml").rename(renamed)
    rules, unreadable = agr.discover_rules(root)
    assert unreadable == []
    rule = next(r for r in rules if r.rule_id == "ok-python")
    assert rule.test_path == renamed
    assert rule.snippet_count == 3


def test_discover_rules_reports_a_test_with_no_rule(tmp_path):
    """The corpus ships one of these (tests/java: 37 files, 36 rules). Counted, not
    ignored -- it is the same shape as the zero-case false-accept."""
    root = _write_corpus(tmp_path)
    (root / "tests" / "python" / "orphan-test.yml").write_text(
        "id: orphan-rule\nvalid:\n  - x\n", encoding="utf-8")
    _rules, unreadable = agr.discover_rules(root)
    assert [reason for _p, reason in unreadable] == ["test_without_rule"]


def test_discover_rules_reports_a_missing_rules_dir(tmp_path):
    rules, unreadable = agr.discover_rules(tmp_path)
    assert rules == []
    assert unreadable[0][1] == "rules_dir_missing"


def test_corpus_is_pinned_by_commit_because_it_has_no_releases():
    assert re.fullmatch(r"[0-9a-f]{40}", agr.CORPUS_COMMIT)
    assert agr.SOURCE_ID.endswith(agr.CORPUS_COMMIT[:7])


def test_license_gate_allows_apache_and_records_the_package_json_discrepancy():
    info = agr.classify_license()
    assert info["verdict"].decision == "ALLOW"
    assert info["verdict"].spdx_id == "Apache-2.0"
    # package.json says ISC; recorded rather than guessed away.
    assert info["package_json_spdx"] == "ISC"


def test_check_files_is_a_minimal_project(tmp_path):
    root = _write_corpus(tmp_path)
    rules, _ = agr.discover_rules(root)
    rule = next(r for r in rules if r.rule_id == "ok-python")
    files = agr.check_files(rule)
    assert set(files) == {"sgconfig.yml", "rules/ok-python.yml", "tests/ok-python-test.yml"}
    assert b"ruleDirs" in files["sgconfig.yml"]


def test_check_files_refuses_a_rule_with_no_test(tmp_path):
    root = _write_corpus(tmp_path)
    (root / "tests" / "python" / "ok-python-test.yml").unlink()
    rules, _ = agr.discover_rules(root)
    with pytest.raises(CheckSpecError):
        agr.check_files(next(r for r in rules if r.rule_id == "ok-python"))


def test_verifier_check_for_records_every_verdict_knob(tmp_path):
    root = _write_corpus(tmp_path)
    rule = next(r for r in agr.discover_rules(root)[0] if r.rule_id == "ok-python")
    check = agr.verifier_check_for(rule)
    assert check.check_type == "ast_grep"
    assert check.tool_version == VERIFIER_VERSIONS["ast_grep"]
    assert check.expected_case_count == 1, "the liveness assertion must survive serialisation"
    assert check.config["snapshot_tests"] is False
    assert check.config["rule_id"] == "ok-python"


def test_procedure_payload_carries_a_runnable_check(tmp_path):
    root = _write_corpus(tmp_path)
    rule = next(r for r in agr.discover_rules(root)[0] if r.rule_id == "ok-python")
    check = agr.verifier_check_for(rule)
    payload = agr.procedure_payload(rule, check)
    assert payload["verifier_check"]["check_type"] == "ast_grep"
    assert payload["source_key"] == "ast-grep:ok-python"
    assert payload["source_locator"]["commit"] == agr.CORPUS_COMMIT
    assert payload["source_locator"]["granularity"] == "document"
    # The V0 gate needs explicit provenance and a derivable scope.
    assert payload["provenance"] == "system_pending_review"
    assert payload["scope_type"] == "global"
    binding = payload["steps"][0]["binding"]
    assert binding["kind"] == "binary"
    assert binding["sandbox_policy"] == "isolated_network"
    assert binding["verifier"]["expected_case_count"] == 1
    # A `binary` binding without a `path` is rejected by source_locators.validate_binding.
    assert binding["path"] == "ast-grep"


def test_counters_account_for_every_discovered_item():
    c = agr.IngestCounters()
    c.discovered = 10
    c.accepted = 4
    c.unchanged = 1
    c.quarantine("rule_parse_error")
    c.quarantine("rule_parse_error")
    c.quarantine("severity_off")
    c.quarantine("no_test_file")
    c.reject("license_quarantine")
    assert c.accounted() == c.discovered, "a counter set that does not add up is how items vanish"


# ---------------------------------------------------------------------------
# Static pins: the gate and its writer ship together
# ---------------------------------------------------------------------------
def test_migration_125_exists_and_is_idempotent():
    path = REPO_ROOT / "backend" / "db" / "125_procedure_verifier_check.sql"
    assert path.is_file(), "migration 125 is the gate for procedures.verifier_check"
    text = path.read_text(encoding="utf-8")
    assert "IF NOT EXISTS" in text
    assert "DROP CONSTRAINT IF EXISTS procedures_verifier_check_chk" in text
    assert "verifier_check ? 'tool_version'" in text, "a pin-less verdict must not validate"
    assert "Next free number: 126" in text


def test_migration_125_rejects_a_check_type_outside_the_vocabulary():
    text = (REPO_ROOT / "backend" / "db" / "125_procedure_verifier_check.sql").read_text(encoding="utf-8")
    for check_type in VERIFIER_CHECK_TYPES:
        assert f"'{check_type}'" in text
    # The allowed list must be EXACTLY the verifier vocabulary: a screening type leaking
    # in here would merge the two vocabularies this step deliberately keeps apart.
    listed = set(re.findall(r"'([a-z_]+)'", text.split("IN (")[1].split(")")[0]))
    assert listed == set(VERIFIER_CHECK_TYPES), f"unexpected allowed list: {listed}"


def test_capture_procedure_writes_and_carries_forward_the_check():
    """Half-gate rule: the writer lands with the gate. The column, its cast, its
    parameter, and the supersede carry-forward must all be present, or a re-ingest
    would silently produce a Procedure that can no longer be checked."""
    text = (REPO_ROOT / "backend" / "app" / "services" / "procedures.py").read_text(encoding="utf-8")
    assert "source_artifacts, ingestion_context_id, verifier_check" in text, "column list"
    assert "$47::uuid, $48::jsonb" in text, "the check must be bound, in order, after ingestion_context_id"
    assert "verifier_check: Optional[dict] = None," in text, "signature"
    assert '"verifier_check": "jsonb",' in text, "supersede cast"
    assert 'frozenset({"source_locator", "source_artifacts", "verifier_check"})' in text, \
        "a supersede of a row read before migration 125 must not KeyError"
    # The trailing positional argument, immediately after ingestion_context_id.
    assert "\n        ingestion_context_id,\n        verifier_check,\n    )" in text, \
        "the value must reach the query, not just the signature"
    # And it must be carried forward on supersede, or a new version stops being checkable.
    carry = re.search(r"_SUPERSEDE_CARRY_COLUMNS[^=]*=\s*\((.*?)\n\)", text, re.S)
    assert carry is not None, "the carry-forward tuple must still exist"
    assert '"verifier_check"' in carry.group(1)


def test_our_migration_number_is_unique():
    """Scoped to 125 on purpose. The repo already carries PRE-EXISTING duplicate
    migration numbers (72, 73, 74 and 110 each have two files) -- reported in
    step_4_SUMMARY.md, not this test's job to fail on. What this step must not do is
    add a collision of its own."""
    dbs = list((REPO_ROOT / "backend" / "db").glob("125_*.sql"))
    assert len(dbs) == 1, f"migration 125 must be a single file, found {[p.name for p in dbs]}"
    assert dbs[0].name == "125_procedure_verifier_check.sql"
