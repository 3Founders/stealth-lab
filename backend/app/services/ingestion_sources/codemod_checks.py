"""
Deterministic check runner for codemod fixtures -- and the gate that keeps
a self-consistency check from being read as a correctness claim.

WHAT THIS IS FOR
    `nodejs/userland-migrations` ships each recipe with before/after
    fixtures. Running them is a real, executable, hermetic check: the
    transform is applied to each `input` and must reproduce the committed
    `expected`. That is Gate A. Gate B is independent and strictly
    stronger: parse every committed `expected` file and require it to be
    syntactically well formed. Gate B is what would have caught
    nodejs/userland-migrations#249, where the fixture test passes and the
    committed output throws `ERR_INVALID_URL` at runtime.

WHAT NEITHER GATE PROVES, STATED IN CODE NOT IN A COMMENT
    A fixture is maintained by the same party as the transform, so a
    passing fixture is evidence of *self-consistency*, not of
    correctness. Every payload built here carries
    `check_semantics="self-consistency"`, and the claim string is bounded
    to "this transform deterministically maps these N input shapes to
    these N output shapes". Nothing in this module may emit
    correctness language, and `build_check_payload` has a test that
    fails if it does.

    Gate B proves SYNTAX, not runtime semantics. A `new URL('/path?x')`
    is perfectly well formed and still throws. We deliberately do not
    execute committed outputs: that would mean running untrusted code
    against a real Node runtime, which is a different (and much larger)
    risk decision than parsing it.

WHY THIS RUNNER IS A SEPARATE MODULE
    The shared check runner is a later step's deliverable. This one owns
    the minimum surface its replacement will need to sit behind, and
    deliberately imports nothing from the screening path (a test pins
    that) so the runner stays a pure, offline, dependency-free function
    of a directory tree plus a subprocess.

THE VULNERABILITY THIS MODULE IS BUILT AGAINST
    A check runner that reports `passed=True` it did not actually read
    is worse than no runner: it launders a broken environment into a
    trust signal. So every failure path here -- missing binary, non-zero
    exit, timeout, unparseable stdout, JSON of an unrecognised shape --
    returns `passed=False` with the reason recorded. There is no code
    path that produces a pass without having read a result.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# Hard ceiling on one fixture-suite execution. The tool documents a 30s
# per-test default and suites here run 1-36 cases, so 120s is generous
# while still bounding a hung subprocess. A timeout is a FAILURE, never
# a pass: "we stopped waiting" is not "the transform is right".
DEFAULT_CHECK_TIMEOUT_S = 120

# `node --check` is a parse, not a program run, so it is cheap; this is
# only here to stop a pathological file from wedging the ingest.
DEFAULT_PARSE_TIMEOUT_S = 20

# Raw tool output is copied into `detail` for the audit trail, so it is
# bounded. The full stdout/stderr is not kept anywhere.
MAX_CAPTURED_OUTPUT_CHARS = 2000

# Pinned in every payload so a stored check is attributable to the code
# that produced it. Bump when the gate set or the claim wording changes.
RUNNER_VERSION = "codemod-jssg-check@v1"

# The only semantics a fixture suite can support. Not a constant for
# aesthetics: it is the single most important string this module emits,
# because it is what stops a downstream surface from upgrading
# "reproduces its own fixtures" into "correct".
CHECK_SEMANTICS = "self-consistency"

CHECK_TIERS: tuple[str, ...] = ("executable", "static")

# The exact set of environment variables a check subprocess inherits.
# Anything a caller has set that is not on this list -- an
# `npx_config_*` registry override, an `http_proxy`, an
# `AWS_*` credential, a `NODE_OPTIONS=--require ...` injection -- is
# dropped. A check must depend on the checkout and the tool, not on
# ambient configuration of whoever ran the ingest.
_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
)

# Extensions Gate B can actually decide on, and the verdict each one is
# allowed to produce. `.ts` / `.tsx` are deliberately absent: there is no
# TypeScript parser available offline in this process, and reporting a
# file we could not parse as well formed is exactly the failure mode
# this gate exists to prevent.
_JSON_EXTS: frozenset[str] = frozenset({".json"})
_CJS_EXTS: frozenset[str] = frozenset({".js", ".cjs"})
_ESM_EXTS: frozenset[str] = frozenset({".mjs"})

# Ordered weakest-support-first. `duplicate_declaration` sits just above
# `wellformed` and below `malformed`: the file is not a loadable module, but
# neither the transform nor the fixture is at fault, so it must not be
# reported as a broken fixture NOR rounded up to a clean parse.
WELLFORMNESS_VERDICTS: tuple[str, ...] = (
    "wellformed",
    "duplicate_declaration",
    "inherited_unparseable",
    "no_parser",
    "malformed",
)

# Claim wording. Bounded on purpose, and covered by a test that fails if
# "verified correct" ever appears in a payload.
_CLAIM_EXECUTABLE = (
    "this transform deterministically maps these {cases} input shape(s) to these "
    "{cases} output shape(s); the check establishes self-consistency against the "
    "transform's own committed fixtures, not that the output is correct"
)
_CLAIM_STATIC = (
    "this recipe's declaration parses and its module attribution is "
    "Apache-2.0 and non-archived; no transformation was executed, so the "
    "claim is about the declaration only"
)


class UnreadableToolOutput(Exception):
    """The check subprocess produced something this module could not read
    as a pass/fail result. Callers must turn this into `passed=False`."""


@dataclass(frozen=True)
class CheckOutcome:
    """One check's verdict, in the shape a Procedure's evidence row needs.

    `failures` holds one short line per failing case, not a traceback:
    this is a value an auditor reads, not a log. `detail` is the
    unstructured remainder (raw output, gate B's per-file verdicts, the
    resolved command) and is explicitly allowed to contain text that was
    never verified -- callers must not treat `detail` as evidence, only
    `passed`/`gates` as evidence.
    """

    passed: bool
    tier: str
    semantics: str
    case_count: int
    negative_case_count: int
    failures: tuple[str, ...] = ()
    gates: dict[str, str] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "tier": self.tier,
            "semantics": self.semantics,
            "case_count": self.case_count,
            "negative_case_count": self.negative_case_count,
            "failures": list(self.failures),
            "gates": dict(self.gates),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class FixtureInventory:
    """What a recipe's fixture tree actually contains, counted.

    `negative_case_count` is the number of cases whose `input` and
    `expected` bytes are identical -- a no-op fixture, the OpenRewrite
    single-argument `java(...)` equivalent. A transform with zero of
    them has a check that cannot distinguish "does the right thing" from
    "does nothing", so the count is carried into every payload rather
    than being a detail the caller has to remember to look for.

    `dangling_pairs` counts cases with an `input` and no matching
    `expected` (or the reverse). They are counted as cases but never as
    negative ones, and a non-zero value is the signature of a truncated
    or hand-assembled fixture set.
    """

    layout: str
    case_count: int
    negative_case_count: int
    input_paths: tuple[str, ...] = ()
    expected_paths: tuple[str, ...] = ()
    dangling_pairs: int = 0


def _scrubbed_env(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a check subprocess is allowed to see.

    Windows environment variable names are case-insensitive but
    `os.environ` preserves whatever casing the parent process used, so
    the lookup is case-folded and the canonical name is emitted. `CI=1`
    is added rather than inherited: it is the flag that keeps a tool from
    opening an interactive pager or prompting.
    """
    source = os.environ if parent is None else parent
    folded = {str(key).upper(): str(value) for key, value in source.items()}
    env = {
        name: folded[name.upper()]
        for name in _ENV_ALLOWLIST
        if folded.get(name.upper()) is not None
    }
    env["CI"] = "1"
    return env


def _truncate(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) <= MAX_CAPTURED_OUTPUT_CHARS:
        return text
    return text[:MAX_CAPTURED_OUTPUT_CHARS] + f"... [truncated at {MAX_CAPTURED_OUTPUT_CHARS} chars]"


def _as_case_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _failure_line(entry: Mapping[str, Any], index: int) -> str:
    for key in ("name", "case", "id", "path", "file", "title"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return f"case {index} ({value})"
    return f"case {index}"


def _entries(payload: Any) -> list[Mapping[str, Any]] | None:
    """The per-case list, if the payload is a list of case objects."""
    if isinstance(payload, Mapping):
        for key in ("results", "tests", "cases", "case_results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        return None
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    return None


def _summary_pair(payload: Mapping[str, Any]) -> tuple[int, int] | None:
    """(case_count, failure_count) if the payload carries explicit totals.

    Only explicit integer totals are accepted. A payload that merely
    exists is not a verdict: inferring "no failures" from the absence of
    a failures key is how a runner invents a pass.
    """
    scopes: list[Mapping[str, Any]] = [payload]
    nested = payload.get("summary") or payload.get("totals")
    if isinstance(nested, Mapping):
        scopes.insert(0, nested)

    total: int | None = None
    failed: int | None = None
    for scope in scopes:
        for key in ("total", "cases", "count", "passed", "failed", "failures", "errors"):
            if key in scope and total is None and key in ("total", "cases", "count"):
                total = _as_case_count(scope[key])
        for key in ("failed", "failures", "errors", "failure_count"):
            if key in scope and failed is None:
                raw = scope[key]
                failed = _as_case_count(raw)
                if failed is None and isinstance(raw, list):
                    failed = len(raw)
    if total is None or failed is None or failed > total:
        return None
    return total, failed


def read_tool_result(payload: Any) -> tuple[int, int, tuple[str, ...]]:
    """(case_count, failure_count, failure_messages) from a parsed result.

    Raises `UnreadableToolOutput` for any shape this module does not
    recognise. There is deliberately no lenient default: an unknown
    schema must surface as a failure to read, not as a pass.
    """
    totals = _summary_pair(payload) if isinstance(payload, Mapping) else None
    if totals is not None:
        total, failed = totals
        if failed == 0:
            return total, 0, ()
        # Explicit totals with a non-zero failure count ARE a verdict. The
        # per-case list is not needed to know the run failed, and falling
        # through to `_entries` here raised UnreadableToolOutput on a
        # summary-only failure report -- reporting a red suite as a result
        # this reader could not understand, which is both wrong and, since
        # the caller treats unreadable as a failure, indistinguishable from
        # a crash.
        messages = [f"the tool reported {failed} failing case(s) of {total}"]
        named = _entries(payload)
        if named:
            messages.extend(_failure_line(entry, index) for index, entry in enumerate(named))
        return total, failed, tuple(messages)

    entries = _entries(payload)
    if entries is None:
        raise UnreadableToolOutput(
            "result payload carries neither explicit totals nor a recognisable case list"
        )
    if not entries:
        raise UnreadableToolOutput("result payload contains an empty case list")

    messages: list[str] = []
    for index, entry in enumerate(entries):
        status = entry.get("status") or entry.get("outcome") or entry.get("result")
        ok = entry.get("passed")
        if isinstance(ok, bool):
            failed = not ok
        elif isinstance(status, str):
            failed = status.strip().lower() not in ("pass", "passed", "ok", "success")
        elif "error" in entry or "failure" in entry:
            failed = True
        else:
            raise UnreadableToolOutput(
                f"case {index} has no readable pass/fail field"
            )
        if failed:
            messages.append(_failure_line(entry, index))
    return len(entries), len(messages), tuple(messages)


def run_jssg_fixture_check(
    *,
    recipe_dir: str | Path,
    transform: str,
    language: str,
    test_dir: str,
    node_bin: str,
    timeout_s: int = DEFAULT_CHECK_TIMEOUT_S,
    extra_args: Sequence[str] = (),
) -> CheckOutcome:
    """Gate A: run the transform's own fixture suite and read the result.

    `node_bin` is the executable to invoke. For this catalog that is the
    `codemod` CLI (Apache-2.0, the Rust binary published as `codemod`),
    NOT `node` -- the name is historical and is kept because the
    signature is the one a shared runner will replace. Pass an explicit
    path: resolving it from PATH at call time means a stray shim earlier
    on PATH decides what a trust check actually runs.

    `cwd` is pinned to `recipe_dir` because the tool resolves its test
    directory and its transform path relative to the recipe, and a
    process that can be moved to a different working directory is a
    process whose result no longer means what it says.

    Every non-happy path returns `passed=False` with the reason in
    `detail`. See the module docstring: this function has no path that
    reports a pass it did not read.
    """
    root = Path(recipe_dir)
    base_detail: dict[str, Any] = {
        "runner_version": RUNNER_VERSION,
        "recipe_dir": str(root),
        "transform": transform,
        "language": language,
        "test_dir": test_dir,
        "node_bin": node_bin,
        "extra_args": list(extra_args),
    }

    if not root.is_dir():
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(f"recipe_dir does not exist: {root}",),
            gates={"jssg_fixture_suite": "not_run"},
            detail={**base_detail, "reason": "recipe_dir_missing"},
        )

    suite = root / test_dir
    if not suite.is_dir():
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(f"fixture suite directory does not exist: {test_dir}",),
            gates={"jssg_fixture_suite": "not_run", "case_coverage": "vacuous"},
            detail={**base_detail, "reason": "fixture_suite_missing"},
        )

    command = [
        node_bin,
        "jssg",
        "test",
        "-l",
        language,
        transform,
        test_dir,
        # The flag is `--reporter json`, NOT `--output-format json`.
        # `codemod jssg test --output-format json` is rejected by clap as an
        # unexpected argument and exits 2, so a runner that guessed the
        # wrong name sees "every fixture failed" on a catalog that is
        # entirely green. Verified against codemod 1.18.3 on 2026-09-28.
        "--reporter",
        "json",
        *[str(arg) for arg in extra_args],
    ]
    env = _scrubbed_env()

    try:
        completed = subprocess.run(  # noqa: S603 -- argv list, no shell
            command,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(f"check tool not found: {node_bin}",),
            gates={"jssg_fixture_suite": "not_run", "case_coverage": "vacuous"},
            detail={**base_detail, "reason": "tool_not_found", "command": command},
        )
    except subprocess.TimeoutExpired:
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(f"check exceeded {timeout_s}s and was killed",),
            gates={"jssg_fixture_suite": "timeout", "case_coverage": "unknown"},
            detail={**base_detail, "reason": "timeout", "command": command, "timeout_s": timeout_s},
        )
    except OSError as exc:
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(f"check could not be started: {exc}",),
            gates={"jssg_fixture_suite": "not_run", "case_coverage": "unknown"},
            detail={**base_detail, "reason": "spawn_failed", "command": command},
        )

    detail = {
        **base_detail,
        "command": command,
        "returncode": completed.returncode,
        "stdout": _truncate(completed.stdout),
        "stderr": _truncate(completed.stderr),
    }

    stdout = (completed.stdout or "").strip()
    # `--reporter json` emits NEWLINE-DELIMITED JSON, one object per event,
    # not a single JSON document: a `suite started` line, a `test` line per
    # case, then a `suite ok`/`failed` summary. `json.loads` on the whole
    # stream raises, which would have reported every recipe on this catalog
    # as an unreadable result. Each line is decoded and the terminal suite
    # event is the authority; a stream that yields no suite event at all is
    # unreadable rather than green.
    payload, saw_json = _read_event_stream(stdout)
    if payload is None:
        # Two different failures, and the difference is diagnostic: no JSON
        # at all means the tool never ran or printed a human message, while
        # JSON-but-no-summary means it ran and emitted a shape this reader
        # does not understand. Collapsing them hides a version mismatch
        # behind what looks like a missing binary.
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(
                ("check tool produced no JSON event on stdout",)
                if not saw_json
                else ("check tool emitted JSON but no terminal suite event",)
            ),
            gates={"jssg_fixture_suite": "unreadable", "case_coverage": "unknown"},
            detail={
                **detail,
                "reason": "unreadable_result" if saw_json else "empty_result",
                "stdout_tail": _truncate(stdout),
            },
        )
    try:
        case_count, failure_count, messages = read_tool_result(payload)
    except UnreadableToolOutput as exc:
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(f"check result could not be read: {exc}",),
            gates={"jssg_fixture_suite": "unreadable", "case_coverage": "unknown"},
            detail={**detail, "reason": "unreadable_result", "raw_result": _truncate(stdout)},
        )

    # A non-zero exit with a readable all-green result is contradictory
    # and is treated as a failure, not silently believed.
    if completed.returncode != 0:
        return CheckOutcome(
            passed=False,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=case_count,
            negative_case_count=0,
            failures=(
                f"check tool exited {completed.returncode} despite reporting "
                f"{case_count} case(s) and {failure_count} failure(s)",
            ),
            gates={"jssg_fixture_suite": "failed", "case_coverage": "reported_by_tool"},
            detail={**detail, "reason": "nonzero_exit"},
        )

    return CheckOutcome(
        passed=failure_count == 0,
        tier="executable",
        semantics=CHECK_SEMANTICS,
        case_count=case_count,
        negative_case_count=0,
        failures=messages,
        gates={
            "jssg_fixture_suite": "passed" if failure_count == 0 else "failed",
            "case_coverage": "reported_by_tool",
        },
        detail={**detail, "reason": "read_result", "reported_failure_count": failure_count},
    )


def _node_check(path: Path, node_exec: str) -> tuple[bool, str]:
    """`node --check <file>`: rc 0 means the file parses as a script."""
    try:
        completed = subprocess.run(  # noqa: S603 -- argv list, no shell
            [node_exec, "--check", str(path)],
            cwd=str(path.parent),
            env=_scrubbed_env(),
            capture_output=True,
            text=True,
            timeout=DEFAULT_PARSE_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return False, f"{node_exec} not found"
    except subprocess.TimeoutExpired:
        return False, f"parse exceeded {DEFAULT_PARSE_TIMEOUT_S}s"
    except OSError as exc:
        return False, f"parse could not be started: {exc}"
    return completed.returncode == 0, _truncate(completed.stderr)


import re

_DUPLICATE_DECLARATION_RE = re.compile(
    r"has already been declared|SyntaxError: Identifier '[^']+' has already been declared",
    re.IGNORECASE,
)

def _input_sibling(expected: Path) -> Path | None:
    """The `input.*` fixture paired with an `expected.*`, or None.

    Both documented layouts name the pair the same way -- a flat
    `<case>/input.js` beside `<case>/expected.js`, or a nested
    `<case>/input/<name>.js` beside `<case>/expected/<name>.js` -- so
    substituting the stem is the pairing the catalog itself uses.
    """
    if expected.stem != "expected":
        return None
    candidate = expected.with_name("input" + expected.suffix)
    return candidate if candidate.is_file() else None


def _is_inherited_unparseable(expected: Path, node_exec: str) -> tuple[bool, str]:
    """Does the paired INPUT also fail to parse?

    `import-assertions-to-attributes/tests/file-edge-case/input.js`
    contains `import { fileURLToPath } from 'node:url' invalid { };` -- a
    deliberately malformed line the catalog ships so the transform has
    something to leave alone. Its `expected.js` inherits the same line, so
    it cannot parse either, while the transform is behaving exactly as
    specified and its own suite passes.

    Deciding this by comparing the pair is more honest than pattern-
    matching the parser's error text: it asks whether the transform
    INTRODUCED the defect, which is the only question Gate B exists to
    answer. An unparseable expected file whose input parses is still
    `malformed`; one whose input is equally unparseable is `inherited`.
    """
    sibling = _input_sibling(expected)
    if sibling is None:
        return False, "no paired input fixture"
    ok, detail = _node_check(sibling, node_exec)
    if ok:
        return False, "the paired input parses, so the output's defect is the transform's"
    return True, f"the paired input does not parse either ({_truncate(detail)})"


def _read_event_stream(stdout: str) -> tuple[dict[str, Any] | None, bool]:
    """The terminal `suite` event from a newline-delimited JSON stream.

    `codemod jssg test --reporter json` writes one JSON object per line:
    a `suite started` line, one `test` line per case, then a `suite ok` or
    `suite failed` summary. The summary line is the only one carrying
    totals, so it is what this returns; a stream whose last `suite` event is
    a `started` line means the run never finished, which is why `started`
    lines are skipped rather than accepted.

    Returns `(terminal_event, saw_any_json)`. A `None` event with
    `saw_any_json=True` means the tool ran and printed JSON in a shape this
    reader does not know -- a different diagnosis from printing no JSON at
    all -- so the two are reported separately rather than merged.
    """
    # A whole-document payload is accepted first, so a reporter that emits
    # one JSON object with `summary`/`results` is read by the same code
    # path. The NDJSON form is the fallback, not the only shape.
    text = (stdout or "").strip()
    if text.startswith("{") and "\n" not in text:
        try:
            single = json.loads(text)
        except ValueError:
            single = None
        if isinstance(single, Mapping) and "type" not in single:
            return dict(single), True

    terminal: dict[str, Any] | None = None
    saw_json = False
    declared_total: int | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, Mapping):
            continue
        saw_json = True
        if event.get("type") == "suite" and event.get("event") == "started":
            if isinstance(event.get("test_count"), int):
                declared_total = event["test_count"]
        if event.get("type") == "suite" and event.get("event") in ("ok", "failed"):
            terminal = dict(event)
            if isinstance(declared_total, int):
                terminal["test_count"] = declared_total
    if terminal is not None:
        # Normalized into the shape `read_tool_result` reads. The suite
        # event's own `passed` key is a COUNT here, not a boolean, and
        # leaving it raw makes a suite summary look like a case-less
        # payload to the reader. `test_count` from the matching `started`
        # line is preferred for the total because `passed` excludes ignored
        # and filtered-out cases.
        passed_n = terminal.get("passed") if isinstance(terminal.get("passed"), int) else 0
        failed_n = terminal.get("failed") if isinstance(terminal.get("failed"), int) else 0
        total_n = declared_total if isinstance(declared_total, int) else passed_n + failed_n
        return (
            {
                "type": "suite",
                "event": terminal.get("event"),
                "total": total_n,
                "passed": passed_n,
                "failed": failed_n,
                "ignored": terminal.get("ignored"),
                "measured": terminal.get("measured"),
                "filtered_out": terminal.get("filtered_out"),
            },
            saw_json,
        )
    # NDJSON with per-case events but no suite summary: rebuild a document
    # `read_tool_result` understands from the case lines, so a truncated
    # stream still yields the cases it did report rather than nothing.
    cases = [
        event
        for line in text.splitlines()
        if line.strip().startswith("{")
        for event in (_maybe_json(line.strip()),)
        if isinstance(event, Mapping) and event.get("type") == "test"
    ]
    if cases:
        return {"results": [{"passed": event.get("event") == "ok", "name": event.get("name", "")} for event in cases]}, True
    return None, saw_json


def _maybe_json(line: str) -> Any:
    try:
        return json.loads(line)
    except ValueError:
        return None


def _is_duplicate_declaration(detail: str) -> bool:
    """Does a `node --check` failure mean 'this binding is declared twice'?

    Matched on the phrase rather than the exit code because the code is the
    same for every syntax error, and misreading a duplicate declaration as
    a malformed fixture would be a false accusation against a catalog whose
    own suite is green.
    """
    return bool(_DUPLICATE_DECLARATION_RE.search(detail or ""))


def _classify_expected(path: Path, node_exec: str) -> tuple[str, str]:
    """One `expected` file -> (per-file verdict, why)."""
    suffix = path.suffix.lower()
    if suffix in _JSON_EXTS:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return "malformed", f"json parse failed: {exc}"
        return "wellformed", "json parse succeeded"
    if suffix in _CJS_EXTS:
        ok, detail = _node_check(path, node_exec)
        if ok:
            return "wellformed", "node --check succeeded"
        if "not found" in detail:
            return "no_parser", detail
        if _is_duplicate_declaration(detail):
            # A committed `expected` fixture that redeclares one binding
            # twice parses in a real ESM module as a runtime-level
            # SyntaxError, but `node --check` evaluates the file as a SCRIPT,
            # where a repeated `const` at top level is a syntax error for a
            # different reason. `ansi-colors-to-styletext` ships such a
            # fixture deliberately -- it pins the transform's output for
            # input that imported the same module twice. Calling that
            # "malformed" would report a codemod defect where the fixture is
            # correct, so a duplicate-declaration diagnostic is recorded as
            # a distinct, honest verdict: the file is not a valid module
            # even though it is a valid fixture.
            return (
                "duplicate_declaration",
                "the committed output declares the same binding twice, so it is not a "
                "loadable module; the catalog's own fixture suite still passes, so this "
                "is recorded as its own verdict rather than as a malformed fixture: "
                f"{detail}",
            )
        inherited, why = _is_inherited_unparseable(path, node_exec)
        if inherited:
            return (
                "inherited_unparseable",
                f"node --check rejected the committed output, and {why}; the defect "
                "predates the transform, so it is recorded as inherited rather than as "
                f"a broken fixture: {detail}",
            )
        # The paired-input comparison is reported in the `malformed` reason
        # too, because "the transform introduced this" is the claim being
        # made and a reader deserves to see what it rests on.
        return (
            "malformed",
            f"node --check failed and {why}, so the defect is the transform's: {detail}",
        )
    if suffix in _ESM_EXTS:
        ok, detail = _node_check(path, node_exec)
        if ok:
            return "wellformed", "node --check succeeded"
        # A `.mjs` that `node --check` rejects is usually rejected because
        # the runtime parsed it as CommonJS, not because the file is
        # broken. Reporting that as `malformed` would be a fabricated
        # defect, so it is recorded as unparsed with the stderr kept.
        return (
            "no_parser",
            "node --check rejected the .mjs file; commonly a CommonJS parse of ESM "
            f"syntax rather than a real defect, so this is recorded as unparsed, not "
            f"malformed: {detail}",
        )
    return (
        "no_parser",
        f"no offline parser for {path.suffix or 'an extensionless file'}; "
        "TypeScript is deliberately not faked here",
    )


def check_expected_wellformedness(
    *,
    expected_paths: Sequence[str | Path],
    node_exec: str = "node",
) -> tuple[str, str]:
    """Gate B: is every committed `expected` file syntactically well formed?

    `node_exec` is the **Node.js runtime**, deliberately a different binary
    from the `node_bin` that runs the jssg transform. They are separate
    programs: `codemod jssg test` is a Rust CLI and `node --check` is the
    runtime's syntax checker, and passing the same path to both silently
    turns every `.js` fixture into a parse failure that looks like a broken
    fixture rather than a misconfigured gate. They are separate parameters
    so that mistake is a `TypeError` at the call site instead.

    Returns `(verdict, detail)`. The verdict is the WEAKEST thing the
    batch can support, in the order `malformed` < `no_parser` <
    `wellformed`, so a single unparseable TypeScript fixture among thirty
    parseable ones cannot be laundered into a clean "wellformed".

    An empty `expected_paths` is `no_parser`, not `wellformed`: nothing
    was parsed, and "wellformed" would read downstream as an assertion
    nobody made.
    """
    files = [Path(p) for p in expected_paths]
    if not files:
        return (
            "no_parser",
            "checked=0; no committed output was parsed, so this is not an assertion "
            "that the transform's output is well formed",
        )

    per_file: list[dict[str, str]] = []
    for path in sorted(files, key=lambda p: p.as_posix()):
        verdict, why = _classify_expected(path, node_exec)
        per_file.append({"path": path.name, "verdict": verdict, "reason": why})

    verdicts = {entry["verdict"] for entry in per_file}
    if "malformed" in verdicts:
        verdict = "malformed"
    elif "no_parser" in verdicts:
        verdict = "no_parser"
    elif "inherited_unparseable" in verdicts:
        verdict = "inherited_unparseable"
    elif "duplicate_declaration" in verdicts:
        verdict = "duplicate_declaration"
    else:
        verdict = "wellformed"

    counts = {
        name: sum(1 for entry in per_file if entry["verdict"] == name)
        for name in WELLFORMNESS_VERDICTS
    }
    detail = (
        f"checked={len(per_file)}; "
        + "; ".join(f"{name}={counts[name]}" for name in WELLFORMNESS_VERDICTS)
        + "; "
        + "; ".join(
            f"{entry['path']}={entry['verdict']} ({entry['reason']})" for entry in per_file
        )
    )
    return verdict, detail


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _pair_names(tests_dir: Path, kind: str, rel_dirs: Sequence[str]) -> dict[str, dict[str, str]]:
    """Map a case directory to {relative name: absolute path} for one side.

    Only `kind` subdirectories of a case are read. A `*.test.*` file
    sitting beside a case directory is not part of that case: mixing the
    two is how a "case" silently acquires an unrelated input.
    """
    found: dict[str, dict[str, str]] = {}
    for rel in rel_dirs:
        case_dir = tests_dir / rel
        side = case_dir / kind
        if not side.is_dir():
            continue
        bucket = found.setdefault(rel, {})
        for entry in sorted(side.rglob("*")):
            if not entry.is_file():
                continue
            bucket[entry.relative_to(side).as_posix()] = str(entry)
    return found


def discover_fixture_cases(*, tests_dir: str | Path | None) -> FixtureInventory:
    """Count a recipe's fixture cases, on either documented layout.

    Layout A (flat pair, the dominant shape): `<case>/input.<ext>` beside
    `<case>/expected.<ext>`.
    Layout B (directory snapshot, used by multi-transform recipes):
    `<case>/input/<name>` beside `<case>/expected/<name>`.
    Layout C (the one recipe still on a bespoke `node --test` harness):
    no fixtures at all, so the case count is the number of test files
    and the negative count is necessarily 0 -- recorded as zero rather
    than estimated.

    If both A and B are present, B wins: a directory snapshot is
    unambiguous about which `input` pairs with which `expected`, while a
    flat pair inside a snapshot tree could be a file that merely happens
    to be named `input.js`.

    Paths in the returned inventory are POSIX and relative to
    `tests_dir`, so the same tree yields the same inventory on Windows
    and on Linux.
    """
    if tests_dir is None:
        return FixtureInventory(layout="none", case_count=0, negative_case_count=0)
    root = Path(tests_dir)
    if not root.is_dir():
        return FixtureInventory(layout="none", case_count=0, negative_case_count=0)

    all_files = sorted(
        entry.relative_to(root).as_posix() for entry in root.rglob("*") if entry.is_file()
    )

    snapshot_inputs: dict[str, dict[str, str]] = {}
    snapshot_expected: dict[str, dict[str, str]] = {}
    for rel in all_files:
        parts = rel.split("/")
        for depth, part in enumerate(parts[:-1]):
            if part in ("input", "expected") and depth >= 1:
                case = "/".join(parts[:depth])
                side = snapshot_inputs if part == "input" else snapshot_expected
                case_dir = root / case / part
                member = case_dir / Path(*parts[depth + 1 :])
                side.setdefault(case, {})["/".join(parts[depth + 1 :])] = str(member)
                break

    if snapshot_inputs or snapshot_expected:
        cases = sorted(set(snapshot_inputs) | set(snapshot_expected))
        input_paths: list[str] = []
        expected_paths: list[str] = []
        negative = 0
        dangling = 0
        total = 0
        for case in cases:
            ins = snapshot_inputs.get(case, {})
            outs = snapshot_expected.get(case, {})
            for name in sorted(set(ins) | set(outs)):
                total += 1
                if name in ins:
                    input_paths.append(f"{case}/input/{name}")
                if name in outs:
                    expected_paths.append(f"{case}/expected/{name}")
                if name not in ins or name not in outs:
                    dangling += 1
                    continue
                in_bytes = _read_bytes(Path(ins[name]))
                out_bytes = _read_bytes(Path(outs[name]))
                if in_bytes is not None and in_bytes == out_bytes:
                    negative += 1
        return FixtureInventory(
            layout="dir-snapshot",
            case_count=total,
            negative_case_count=negative,
            input_paths=tuple(sorted(input_paths)),
            expected_paths=tuple(sorted(expected_paths)),
            dangling_pairs=dangling,
        )

    flat: dict[str, dict[str, str]] = {}
    for rel in all_files:
        stem, dot, ext = rel.rpartition(".")
        if not dot:
            continue
        base = stem.rsplit("/", 1)[-1]
        if base not in ("input", "expected"):
            continue
        case = stem.rsplit("/", 1)[0] if "/" in stem else ""
        side = flat.setdefault(case, {})
        if base == "input":
            side[f"input.{ext}"] = rel
        else:
            side[f"expected.{ext}"] = rel

    if flat:
        input_paths = []
        expected_paths = []
        negative = 0
        dangling = 0
        total = 0
        for case in sorted(flat):
            side = flat[case]
            for ext_key in sorted({k.split(".", 1)[1] for k in side}):
                in_key = f"input.{ext_key}"
                out_key = f"expected.{ext_key}"
                total += 1
                if in_key in side:
                    input_paths.append(side[in_key])
                if out_key in side:
                    expected_paths.append(side[out_key])
                if in_key not in side or out_key not in side:
                    dangling += 1
                    continue
                in_bytes = _read_bytes(root / side[in_key])
                out_bytes = _read_bytes(root / side[out_key])
                if in_bytes is not None and in_bytes == out_bytes:
                    negative += 1
        return FixtureInventory(
            layout="flat-pair",
            case_count=total,
            negative_case_count=negative,
            input_paths=tuple(sorted(input_paths)),
            expected_paths=tuple(sorted(expected_paths)),
            dangling_pairs=dangling,
        )

    test_files = [rel for rel in all_files if Path(rel).name.endswith((".test.js", ".test.ts", ".test.mjs", ".spec.js", ".spec.ts"))]
    if test_files:
        return FixtureInventory(
            layout="node-test",
            case_count=len(test_files),
            negative_case_count=0,
            input_paths=tuple(test_files),
        )

    return FixtureInventory(layout="none", case_count=0, negative_case_count=0)


def build_check_payload(
    *,
    outcome: CheckOutcome,
    inventory: FixtureInventory,
    recipe_id: str,
    runner_version: str,
) -> dict[str, Any]:
    """The JSON that becomes a Procedure's check.

    Everything a downstream surface would need to avoid over-claiming is
    in here rather than in a code comment: `check_semantics` is
    `self-consistency`, `claim` is bounded to what was actually
    established, and `limitations` names the two ways this check can be
    wrong (the fixture and the transform have the same author; well
    formedness is syntax, not behaviour).

    A transform with no negative case gets `vacuous: true` and a
    limitation saying so. That is the single most useful field here: a
    suite where every `input` differs from its `expected` cannot tell a
    working transform from one that rewrites every file it is shown.
    """
    if outcome.tier not in CHECK_TIERS:
        raise ValueError(f"unknown check tier {outcome.tier!r}; expected one of {CHECK_TIERS}")
    if outcome.semantics != CHECK_SEMANTICS:
        raise ValueError(
            f"check semantics {outcome.semantics!r} is not the only semantics this "
            f"runner can support ({CHECK_SEMANTICS!r})"
        )

    executable = outcome.tier == "executable"
    limitations = [
        "the fixture and the transform are maintained by the same party, so a passing "
        "suite can encode the transform's own bug",
        "output well-formedness is a syntax result, not a runtime-semantics result",
        "no LLM was consulted at any point: extraction and checking here are fully "
        "deterministic",
    ]
    if inventory.negative_case_count == 0 and executable:
        limitations.append(
            "no negative (no-op) fixture was found, so this check cannot distinguish a "
            "transform that does the right thing from one that does nothing"
        )
    if inventory.dangling_pairs:
        limitations.append(
            f"{inventory.dangling_pairs} fixture case(s) have an input with no matching "
            "expected output (or the reverse) and were counted but not paired"
        )
    if not executable:
        limitations.append(
            "no transformation was executed: this source cannot run its recipes without "
            "JDK 21, Gradle, network dependency resolution and a Code Genome token"
        )

    claim = _CLAIM_EXECUTABLE.format(cases=outcome.case_count) if executable else _CLAIM_STATIC
    return {
        "recipe_id": recipe_id,
        "check_kind": "jssg-fixture-self-consistency" if executable else "static-declaration",
        "check_tier": outcome.tier,
        "check_semantics": CHECK_SEMANTICS,
        "passed": outcome.passed,
        "case_count": outcome.case_count,
        "negative_case_count": outcome.negative_case_count,
        "vacuous": outcome.negative_case_count == 0,
        "fixture_layout": inventory.layout,
        "runner_version": runner_version,
        "gates": dict(outcome.gates),
        "failures": list(outcome.failures),
        "claim": claim,
        "limitations": limitations,
    }
