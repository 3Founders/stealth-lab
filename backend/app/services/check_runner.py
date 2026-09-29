"""Sandboxed runner for EXTERNAL static verifiers, returning a five-valued verdict.

WHY THIS EXISTS
    The product direction is "routing with a check": knowledge that carries a
    *checkable outcome* is worth the most. Until now nothing in the substrate
    could actually RUN a check -- `routing.CHECK_KINDS` only carries a Beta prior
    on how much to distrust a self-reported outcome, and
    `execution/verification.py:71-73` names the missing `method` field as a
    known limitation. This module is that missing executor, for the three
    verifiers that can decide a Procedure's outcome without a network or a
    language toolchain of our own.

WHY NOT `screening.CHECK_TYPES`
    That vocabulary (`services/screening.py:74-83`) is 1:1 with the DB CHECK
    `check_type_chk_screening_decisions` and records *admission screening of
    untrusted source text* -- "we refused this document: prompt_injection /
    pii / license". A verifier verdict answers a different question about a
    different subject at a different moment. Board question Q-STEP4-1 records
    the proposal to keep them separate; see migration 125 for the storage.

THE FIVE-VALUED VERDICT IS THE WHOLE POINT
    All three tools conflate "clean", "crashed", and "found nothing to inspect"
    in ways that `exit == 0` reads as pass. This is not hypothetical here --
    it is REPRODUCED on ast-grep: `ast-grep test` prints
    `Configuration not found! <rule>`, runs zero cases, prints
    `test result: ok. 0 passed; 0 failed;` and EXITS 0
    (`.scratch/ingestion/step_4_research.md` §4.3). A boolean runner would
    record a Procedure as verified having checked nothing at all. The corpus
    literature agrees the accept side is where the errors are: every study
    that measured both directions found false-accept >> false-reject
    (7.8%, 11.0%, 19.78%, 24%), and CodeQL's autobuild failed on 71% of repos
    -- i.e. the dangerous state is "no output", not "wrong output".

    So: `error` and `no_input` are NEVER pass, and every parser carries a
    liveness assertion proving the tool actually consumed the input.

SCOPE LIMITS, STATED NOT HIDDEN
    - `ast_grep` runs as a LOCAL pinned binary, not in a container: ast-grep
      publishes no anonymously-pullable image (`ghcr.io/ast-grep/cli` returns
      `denied`, and `ast-grep/cli` does not exist on Docker Hub). It gets a
      scrubbed environment, a throwaway cwd and a hard timeout, but NOT
      container isolation. actionlint and zizmor DO get the full flag set.
    - We never let the subject's own report reach the verdict. Per SWE-bench
      #538, a submitted patch that overwrites the test file flips the eval to
      `resolved: true`; per arXiv:2603.25764 self-report and executed
      verification disagree on up to 56 points (99% submit vs 18% resolve).
    - No measured false-accept/false-reject rate exists for these three tools
      (Q-STEP4-5). The stored config exists so a rate becomes measurable on
      our own corpus instead of borrowed from other tools on other corpora.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from app.services.sandbox_executor import InputPathEscape, stage_input_files
from app.utils.aio import run_blocking

# ---------------------------------------------------------------------------
# Exported vocabulary
# ---------------------------------------------------------------------------
# NOTE: deliberately NOT `screening.CHECK_TYPES`. See module docstring + Q-STEP4-1.
VERIFIER_CHECK_TYPES: tuple[str, ...] = ("actionlint", "zizmor", "ast_grep")

# The five-valued verdict. Order is meaningful only for readability.
# `pass` is the ONLY value that may close a Procedure.
VERDICTS: tuple[str, ...] = ("pass", "fail", "error", "no_input", "not_run")

# Pinned, and pinned on purpose. A stored verdict that cannot name the exact
# tool version is not reproducible: SWE-bench maintainers documented 30/300
# instances where the EVALUATOR non-deterministically marked the GOLD patch
# wrong. Pinning is a correctness control here, not hygiene.
VERIFIER_VERSIONS: dict[str, str] = {
    "actionlint": "1.7.12",
    "zizmor": "1.29.0",
    "ast_grep": "0.45.3",
}

CONTAINER_CHECK_TYPES: tuple[str, ...] = ("actionlint", "zizmor")
LOCAL_CHECK_TYPES: tuple[str, ...] = ("ast_grep",)

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 300.0

# Sandbox contract. Mirrors `sandbox_executor.ContainerSandboxExecutor`'s
# defaults deliberately: one place to weaken isolation, one place to notice.
SANDBOX_MEMORY = "512m"
SANDBOX_CPUS = "1.0"
SANDBOX_PIDS_LIMIT = 128
SANDBOX_USER = "65534:65534"

# zizmor only collects inputs from these locations. A check that stages a
# workflow anywhere else finds nothing and exits 3 -- which is why the layout
# is validated rather than trusted.
_ZIZMOR_WORKFLOW_PREFIX = ".github/workflows/"

_AST_GREP_SUMMARY_RE = re.compile(r"(\d+)\s+passed;\s*(\d+)\s+failed")
_AST_GREP_PARSE_ERROR_RE = re.compile(r"Cannot parse rule|not a valid ast-grep rule")
_ACTIONLINT_JSON_PREFIX = "["

# Env var names scrubbed from a LOCAL verifier's environment. This is the same
# class of leak the ingestion audit found in `github_corpus._default_http_get`
# (Authorization reused across redirects): a verifier that can read a token is
# a verifier whose output we may publish.
_SCRUBBED_ENV_PREFIXES: tuple[str, ...] = (
    "GH_TOKEN", "GITHUB_TOKEN", "ZIZMOR_GITHUB_TOKEN", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "VERTEX_API_KEY",
    "OPENROUTER_API_KEY", "AWS_", "AZURE_", "SUPABASE_", "DATABASE_URL",
    "K001_DATABASE_URL", "INGEST_SERVICE_TOKEN", "SERVICE_TOKEN_KEYS",
)


class CheckSpecError(ValueError):
    """An unusable check spec -- unknown check type, unpinned tool, or a
    file layout the tool cannot audit. Raised BEFORE anything executes, so a
    bad spec can never half-run."""


@dataclass(frozen=True)
class VerifierCheck:
    """The stored, reproducible description of a check. This is what lands in
    `procedures.verifier_check` (migration 125) and what an evidence row must
    be able to name to be auditable.

    `config` is not decoration: it holds every knob that CHANGES the verdict.
    zizmor honours inline `# zizmor: ignore` comments by default, so audited
    code can silence its own finding; actionlint's v1.7.11 changed a verdict
    by disabling shellcheck's SC2153; ast-grep silently skips `severity: off`
    rules. A verdict that does not record these is not a verdict.
    """

    check_type: str
    tool_version: str
    source: str                      # provenance, e.g. "ast-grep-essentials@7312010"
    config: Mapping[str, Any] = field(default_factory=dict)
    expected_case_count: Optional[int] = None   # liveness assertion (ast_grep)

    def __post_init__(self) -> None:
        if self.check_type not in VERIFIER_CHECK_TYPES:
            raise CheckSpecError(
                f"check_type must be one of {VERIFIER_CHECK_TYPES!r}, "
                f"got {self.check_type!r}"
            )
        if not self.tool_version:
            raise CheckSpecError("tool_version is required: an unpinned verdict is not evidence")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "check_type": self.check_type,
            "tool_version": self.tool_version,
            "source": self.source,
            "config": dict(self.config),
        }
        if self.expected_case_count is not None:
            out["expected_case_count"] = self.expected_case_count
        return out

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "VerifierCheck":
        missing = [k for k in ("check_type", "tool_version", "source") if not raw.get(k)]
        if missing:
            raise CheckSpecError(f"verifier_check is missing {missing}")
        return cls(
            check_type=str(raw["check_type"]),
            tool_version=str(raw["tool_version"]),
            source=str(raw["source"]),
            config=dict(raw.get("config") or {}),
            expected_case_count=raw.get("expected_case_count"),
        )


@dataclass(frozen=True)
class CheckFinding:
    """One finding. `line` is 1-BASED for every tool: zizmor's `json-v1`
    reports 0-based rows under the key `row`, and the runner normalises so a
    consumer never has to know which tool produced the row."""

    code: str
    message: str
    severity: Optional[str] = None
    path: Optional[str] = None
    line: Optional[int] = None

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
        }


@dataclass(frozen=True)
class CheckResult:
    verdict: str
    check_type: str
    tool_version: str
    findings: tuple[CheckFinding, ...] = ()
    exit_code: int = -1
    timed_out: bool = False
    duration_ms: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise CheckSpecError(
                f"verdict must be one of {VERDICTS!r}, got {self.verdict!r}"
            )

    @property
    def passed(self) -> bool:
        """The ONLY property allowed to close a Procedure."""
        return self.verdict == "pass"

    def to_json(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "check_type": self.check_type,
            "tool_version": self.tool_version,
            "findings": [f.to_json() for f in self.findings],
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Registry: pinned images, binaries and entrypoints
# ---------------------------------------------------------------------------
# `entrypoint` is set explicitly rather than inherited from the image, so the
# argv a test asserts is the argv that runs.
VERIFIER_REGISTRY: dict[str, dict[str, Any]] = {
    "actionlint": {
        "image": "rhysd/actionlint:1.7.12",
        "entrypoint": "actionlint",
        "kind": "container",
        # `-shellcheck=`/`-pyflakes=` disable integrations that are otherwise
        # auto-detected on PATH and SILENTLY used (actionlint's own help:
        # "If empty, pyflakes integration will be disabled"). Leaving them on
        # makes the verdict depend on the runner image's contents.
        "flags": ["-no-color", "-shellcheck=", "-pyflakes=", "-format", "{{json .}}"],
    },
    "zizmor": {
        "image": "ghcr.io/zizmorcore/zizmor:1.29.0",
        "entrypoint": "zizmor",
        "kind": "container",
        # `--offline` is a TOOL policy, not a sandbox: the docs promise no
        # GitHub API use but say nothing about process-level egress, so the
        # `--network none` in container_argv is what actually enforces it.
        # `--no-ignores` stops audited code silencing its own finding.
        # JSON (not sarif) because `--format=sarif` forces exit 0 even with
        # findings, which would make the exit code a lie.
        "flags": ["--offline", "--no-ignores", "--no-progress", "--format=json"],
    },
    "ast_grep": {
        "image": None,
        "entrypoint": "ast-grep",
        "kind": "local",
        "binary_env": "SL_AST_GREP_BIN",
        "flags": ["test", "--skip-snapshot-tests", "-c", "./sgconfig.yml"],
    },
}


def pinned_check_type(check_type: str) -> dict[str, Any]:
    if check_type not in VERIFIER_REGISTRY:
        raise CheckSpecError(f"unknown check_type {check_type!r}")
    return VERIFIER_REGISTRY[check_type]


# ---------------------------------------------------------------------------
# argv construction -- the security contract, as pure functions
# ---------------------------------------------------------------------------
# House style, copied from `sandbox_executor._docker_argv`: the isolation flags
# are produced by a PURE method so they can be asserted with no daemon
# present. `tests/test_verifier_checks_offline.py` parametrizes over every flag
# so deleting one fails loudly.
def container_argv(
    check_type: str,
    work_dir: str,
    *,
    docker_binary: str = "docker",
    targets: Optional[list[str]] = None,
) -> list[str]:
    """argv for a containerised verifier. Pure: no filesystem, no daemon."""
    spec = pinned_check_type(check_type)
    if spec["kind"] != "container":
        raise CheckSpecError(f"{check_type} is not containerised")
    argv = [
        docker_binary, "run", "--rm",
        "--network", "none",
        "--read-only",
        "--memory", SANDBOX_MEMORY,
        "--cpus", SANDBOX_CPUS,
        "--pids-limit", str(SANDBOX_PIDS_LIMIT),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", SANDBOX_USER,
        "-v", f"{work_dir}:/work",
        "-w", "/work",
        "--entrypoint", str(spec["entrypoint"]),
        str(spec["image"]),
    ]
    argv.extend(str(f) for f in spec["flags"])
    if targets:
        argv.extend(targets)
    else:
        argv.append(".")
    return argv


def local_argv(check_type: str, binary: str, *, targets: Optional[list[str]] = None) -> list[str]:
    """argv for a locally-executed pinned verifier. Pure."""
    spec = pinned_check_type(check_type)
    if spec["kind"] != "local":
        raise CheckSpecError(f"{check_type} is not executed locally")
    argv = [binary, *[str(f) for f in spec["flags"]]]
    if targets:
        argv.extend(targets)
    return argv


def scrubbed_env(extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """A minimal environment for a LOCAL verifier: no credentials survive."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if any(key.upper().startswith(p) for p in _SCRUBBED_ENV_PREFIXES):
            continue
        if key.endswith("_API_KEY") or key.endswith("_TOKEN"):
            continue
        env[key] = value
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Parsers -- each one carries its own liveness assertion
# ---------------------------------------------------------------------------
def parse_actionlint(exit_code: int, stdout: str, stderr: str) -> tuple[str, tuple[CheckFinding, ...], str]:
    """actionlint exit codes (official table, verified locally):
    0 ran/no problem · 1 ran/problem found · 2 invalid option · 3 fatal error.

    `2` and `3` are `error`, never `fail`: a typo'd flag and a missing input
    file are both "we did not check anything", and locally a nonexistent file
    gives exactly 3."""
    if exit_code in (2, 3):
        return "error", (), (stderr or stdout).strip()[:300] or f"actionlint exit {exit_code}"
    body = (stdout or "").strip()
    if not body.startswith(_ACTIONLINT_JSON_PREFIX):
        return "error", (), "actionlint produced no JSON report; refusing to read silence as pass"
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        return "error", (), f"actionlint JSON unparseable: {exc}"[:300]
    if not isinstance(raw, list):
        return "error", (), "actionlint JSON was not a list of errors"
    findings = tuple(
        CheckFinding(
            code=str(item.get("kind") or "actionlint"),
            message=str(item.get("message") or ""),
            path=item.get("filepath"),
            line=int(item["line"]) if isinstance(item.get("line"), int) else None,
        )
        for item in raw
        if isinstance(item, dict)
    )
    if exit_code == 0:
        # Liveness: 0 with findings is a contradiction, and a 0 with an empty
        # array is the only true clean signal.
        if findings:
            return "error", findings, "actionlint exited 0 but reported findings"
        return "pass", (), ""
    if exit_code == 1:
        if not findings:
            return "error", (), "actionlint exited 1 but reported no findings"
        return "fail", findings, f"{len(findings)} finding(s)"
    return "error", (), f"actionlint exit {exit_code}"


def parse_zizmor(exit_code: int, stdout: str, stderr: str) -> tuple[str, tuple[CheckFinding, ...], str]:
    """zizmor exit codes: 0 no findings (OR sarif mode) · 1 audit error ·
    2 bad args · 3 no inputs collected · 11/12/13/14 findings at
    informational/low/medium/high.

    We use `--format=json` precisely so the findings array -- not the exit
    code -- decides the verdict."""
    if exit_code in (1, 2, 3):
        return ("no_input" if exit_code == 3 else "error"), (), \
            (stderr or stdout).strip()[:300] or f"zizmor exit {exit_code}"
    body = (stdout or "").strip()
    start = body.find("[")
    if start < 0:
        return "error", (), "zizmor produced no JSON report; refusing to read silence as pass"
    try:
        raw = json.loads(body[start:])
    except json.JSONDecodeError as exc:
        return "error", (), f"zizmor JSON unparseable: {exc}"[:300]
    if not isinstance(raw, list):
        return "error", (), "zizmor JSON was not a list of findings"
    findings: list[CheckFinding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        det = item.get("determinations") or {}
        loc = None
        locations = item.get("locations") or []
        if locations and isinstance(locations[0], dict):
            concrete = locations[0].get("concrete") or {}
            loc = concrete.get("filename")
            row = concrete.get("row")
            line = int(row) + 1 if isinstance(row, int) else None  # json-v1 is 0-based
        else:
            line = None
        findings.append(CheckFinding(
            code=str(item.get("ident") or "zizmor"),
            message=str(item.get("desc") or ""),
            severity=str(det.get("severity")) if det.get("severity") else None,
            path=loc,
            line=line,
        ))
    if exit_code == 0:
        if findings:
            return "error", tuple(findings), "zizmor exited 0 but reported findings"
        return "pass", (), ""
    if exit_code in (11, 12, 13, 14):
        if not findings:
            return "error", (), f"zizmor exit {exit_code} but reported no findings"
        return "fail", tuple(findings), f"{len(findings)} finding(s)"
    return "error", (), f"zizmor exit {exit_code}"


def parse_ast_grep_test(
    exit_code: int,
    stdout: str,
    stderr: str,
    *,
    expected_case_count: Optional[int] = None,
    expected_rule_id: Optional[str] = None,
) -> tuple[str, tuple[CheckFinding, ...], str]:
    """`ast-grep test` verdicts, measured locally:

    exit 0 + "N passed; M failed"  -> N>0 is the ONLY trustworthy pass
    exit 4                        -> a test case failed
    exit 8                        -> a rule failed to parse
    exit 0 + "0 passed; 0 failed" -> NO CASES RAN. This is the false-accept:
                                     ast-grep prints "Configuration not found!"
                                     and exits 0. Must never be a pass.

    UNIT, learned the hard way: `N passed` counts rule TEST FILES, not individual
    valid/invalid snippets -- a rule-test holding 1 valid and 1 invalid case reports
    "1 passed". Asserting against len(valid)+len(invalid) therefore FAILS a correct
    check, which is the liveness assertion doing its job on our own bug. Per-rule
    isolation stages exactly one test file, so the expected count is 1.
    """
    text = f"{stdout or ''}\n{stderr or ''}"
    if _AST_GREP_PARSE_ERROR_RE.search(text):
        # An upstream rule the pinned CLI cannot load. Worth naming precisely, because
        # the operator's next question is always "is this our bug or the corpus's?" and
        # at this commit it is demonstrably the corpus's.
        return "error", (), (
            "ast-grep cannot parse this rule (upstream rule-definition defect at the "
            "pinned CLI version, not a check failure)"
        )
    match = _AST_GREP_SUMMARY_RE.search(text)
    if match is None:
        return "error", (), "ast-grep test produced no result summary"
    passed_n, failed_n = int(match.group(1)), int(match.group(2))
    if exit_code == 0 and passed_n == 0 and failed_n == 0:
        return "no_input", (), "ast-grep ran zero cases (rule id not loaded); not a pass"
    if expected_case_count is not None and passed_n + failed_n != expected_case_count:
        return "error", (), (
            f"ast-grep ran {passed_n + failed_n} test group(s) but {expected_case_count} "
            "were expected; the test file and the staged rule disagree"
        )
    if failed_n:
        return "fail", (), f"{failed_n} test group(s) failed"
    if passed_n:
        if expected_rule_id and expected_rule_id not in text:
            # A pass that never names the rule it claims to have checked: the same
            # failure as the zero-case bug wearing a different hat.
            return "error", (), (
                f"ast-grep reported a pass without naming {expected_rule_id!r}"
            )
        return "pass", (), f"{passed_n} test group(s) passed"
    if exit_code == 0:
        return "no_input", (), "ast-grep reported no cases"
    return "error", (), f"ast-grep test exit {exit_code}"


_PARSERS = {
    "actionlint": lambda ec, out, err, **_: parse_actionlint(ec, out, err),
    "zizmor": lambda ec, out, err, **_: parse_zizmor(ec, out, err),
    "ast_grep": lambda ec, out, err, **kw: parse_ast_grep_test(ec, out, err, **kw),
}


def parse_result(
    check_type: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    *,
    expected_case_count: Optional[int] = None,
    expected_rule_id: Optional[str] = None,
) -> tuple[str, tuple[CheckFinding, ...], str]:
    if check_type not in _PARSERS:
        raise CheckSpecError(f"no parser for {check_type!r}")
    return _PARSERS[check_type](
        exit_code, stdout, stderr,
        expected_case_count=expected_case_count,
        expected_rule_id=expected_rule_id,
    )


# ---------------------------------------------------------------------------
# Layout validation -- fail before executing, not after
# ---------------------------------------------------------------------------
def validate_layout(check_type: str, files: Mapping[str, bytes]) -> None:
    """Refuse a layout the pinned tool cannot audit.

    zizmor only collects `.github/workflows/**` and action manifests; hand it
    anything else and it exits 3 with a cheerful "no findings"-shaped success.
    Better to say so before spending a container start."""
    if check_type != "zizmor":
        return
    if not any(k.startswith(_ZIZMOR_WORKFLOW_PREFIX) for k in files):
        raise CheckSpecError(
            f"a zizmor check needs at least one file under "
            f"{_ZIZMOR_WORKFLOW_PREFIX!r}; got {sorted(files)[:5]!r}"
        )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _run_sync(
    check_type: str,
    work_dir: Path,
    files: dict[str, bytes],
    timeout_seconds: float,
    *,
    docker_binary: str,
    ast_grep_binary: Optional[str],
) -> tuple[int, str, str, bool, Optional[str]]:
    """Returns `(exit_code, stdout, stderr, timed_out, refusal)`.

    `refusal` short-circuits the parser: it is set only when we declined to produce a
    verdict at all (no binary), and its message MUST reach `CheckResult.detail`. Without
    the short-circuit the parser would overwrite it with a generic "no result summary"
    and the operator would lose the one message that says what to fix.
    """
    spec = pinned_check_type(check_type)
    if spec["kind"] == "container":
        argv = container_argv(check_type, str(work_dir), docker_binary=docker_binary)
    else:
        binary = ast_grep_binary or os.environ.get(str(spec["binary_env"])) or shutil.which("ast-grep")
        if not binary:
            # Never degrade silently -- `ContainerSandboxExecutor` sets the precedent
            # with its "Refusing to fall back to an unisolated executor" message.
            return -1, "", "", False, (
                "ast-grep binary not found; refusing to report a verdict we did not "
                f"produce. Set {spec['binary_env']} to a pinned "
                f"{VERIFIER_VERSIONS['ast_grep']} binary."
            )
        argv = local_argv(check_type, str(binary))

    env = scrubbed_env({"HOME": str(work_dir)})
    try:
        proc = subprocess.run(  # noqa: S603 - argv is built from a closed registry, never caller text
            argv,
            cwd=str(work_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return -1, _as_text(exc.stdout), "killed after timeout", True, None
    except OSError as exc:
        return -1, "", "", False, f"could not execute verifier: {exc}"[:300]
    return proc.returncode, proc.stdout or "", proc.stderr or "", False, None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


async def run_check(
    check: VerifierCheck,
    files: Mapping[str, bytes],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    docker_binary: str = "docker",
    ast_grep_binary: Optional[str] = None,
) -> CheckResult:
    """Run one pinned verifier over `files` and return a five-valued verdict.

    `files` keys are paths RELATIVE to the check's own temp workspace. An
    absolute or `..`-carrying key raises `InputPathEscape` (reused from
    `sandbox_executor`, so the escape guard cannot drift between the two
    runners). The workspace is always removed, including on timeout.
    """
    if not isinstance(files, Mapping) or not files:
        raise CheckSpecError("a check needs at least one input file")
    validate_layout(check.check_type, files)
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise CheckSpecError(
            f"timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS}], got {timeout_seconds}"
        )

    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix=f"slcheck_{check.check_type}_"))
    try:
        stage_input_files(tmp, dict(files))  # raises InputPathEscape
        code, out, err, timed_out, refusal = await run_blocking(
            _run_sync,
            check.check_type,
            tmp,
            dict(files),
            float(timeout_seconds),
            docker_binary=docker_binary,
            ast_grep_binary=ast_grep_binary,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    if refusal is not None:
        # We declined to produce a verdict at all. Short-circuit the parser so the
        # operator sees WHY rather than a generic "no result summary".
        return CheckResult(
            verdict="error", check_type=check.check_type,
            tool_version=check.tool_version, exit_code=code,
            duration_ms=duration_ms, detail=refusal,
        )
    if timed_out:
        return CheckResult(
            verdict="error", check_type=check.check_type,
            tool_version=check.tool_version, exit_code=code,
            timed_out=True, duration_ms=duration_ms,
            detail="verifier exceeded its timeout; no verdict",
        )
    verdict, findings, detail = parse_result(
        check.check_type, code, out, err,
        expected_case_count=check.expected_case_count,
        expected_rule_id=(check.config or {}).get("rule_id"),
    )
    return CheckResult(
        verdict=verdict, check_type=check.check_type,
        tool_version=check.tool_version, findings=findings, exit_code=code,
        duration_ms=duration_ms, detail=detail,
    )


__all__ = [
    "VERIFIER_CHECK_TYPES", "VERDICTS", "VERIFIER_VERSIONS", "VERIFIER_REGISTRY",
    "CONTAINER_CHECK_TYPES", "LOCAL_CHECK_TYPES", "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS", "CheckSpecError", "VerifierCheck", "CheckFinding",
    "CheckResult", "container_argv", "local_argv", "scrubbed_env",
    "parse_actionlint", "parse_zizmor", "parse_ast_grep_test", "parse_result",
    "validate_layout", "run_check", "pinned_check_type",
]
