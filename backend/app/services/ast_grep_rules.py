"""Ingest ast-grep-essentials rules as Procedures whose check is the rule's OWN test cases.

WHY THIS SOURCE IS WORTH THE TROUBLE
    The product direction is "routing with a check". ast-grep-essentials is one of the few
    corpora where the check ships WITH the knowledge: every rule in
    `coderabbitai/ast-grep-essentials` is accompanied by a `tests/<lang>/<rule>-test.yml`
    holding `valid` cases (must NOT match) and `invalid` cases (must match). So the rule's
    correctness is a property we can EXECUTE, not assert. That is the whole point: a
    Procedure becomes eligible only because `ast-grep test` passes on its own fixtures.

    Two properties make these unusually good substrate material:
      * `id` is unique across the whole package (not per language) -- a stable identity for
        `source_key` dedup.
      * `note` carries the CWE id and OWASP reference -- real provenance text, not a
        reworded title.

    184 rules across 15 languages at the pinned commit. Apache-2.0, which is on
    `repo_license_policy.DEFAULT_ALLOWLIST`, so the license gate is an ALLOW -- but it is
    still RUN per item, because "license per item, never per compilation" is the rule and
    the allowlist version is recorded in the verdict.

WHY THE CHECK IS PER-RULE, NOT WHOLE-SUITE
    ast-grep loads every rule before running any test, so ONE unparseable rule aborts the
    entire suite (exit 8). Measured at the pinned commit: 30 of 184 rules use a prelude
    `utils:` form (`PATTERN_1(identifier)`) that ast-grep 0.43.0, 0.44.1 and 0.45.3 all
    reject as a reserved-character utility id, so `ast-grep test` over the whole repo exits
    8 and reports nothing. Running each rule in its own miniature project isolates the
    failure to the rule that caused it -- which is the only way to both keep the 154 good
    rules AND report the 30 bad ones honestly instead of dropping them silently.

    Those 30 become NO Procedures. "Only verified outcomes become Procedures": a rule whose
    own test cannot be executed has no verified outcome.

SCOPE LIMITS, STATED NOT HIDDEN
    - Snapshot tests are NOT run (`--skip-snapshot-tests`). We assert fire/no-fire, which is
      what a Procedure's check means; the byte-exact message/span snapshots are upstream's
      own regression hygiene, not a property of the knowledge we store. Recorded in the
      stored check config so it is not a silent omission.
    - `severity: off` rules are skipped by ast-grep unless `--include-off` is passed, so they
      can never produce a check here; they are counted and reported, not ingested.
    - No LLM call is made in this path. Cost is zero, which is unusual and worth stating.
    - No agent traces are involved, so `trace_redaction` is not on this path. Explicitly
      absent, not silently skipped.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from app.services.check_runner import (
    VERIFIER_VERSIONS,
    CheckResult,
    CheckSpecError,
    VerifierCheck,
    run_check,
)
from app.services.repo_license_policy import classify_spdx

# The pinned corpus. NO GitHub releases exist for this repo ("There aren't any releases
# here"), so a commit SHA is the only immutable reference available -- a third party
# (pi-lens) vendors the same SHA, which is corroboration that pinning is the norm.
CORPUS_REPO = "coderabbitai/ast-grep-essentials"
CORPUS_COMMIT = "73120109bf45c284d0cd8a37bdd7082e80e92e87"
CORPUS_LICENSE_SPDX = "Apache-2.0"
CORPUS_URI = f"https://github.com/{CORPUS_REPO}"
# package.json declares "license": "ISC"; the root LICENSE (which governs the rule files)
# is Apache-2.0. We gate on the root LICENSE because that is what covers the content we
# ingest, and the discrepancy is recorded rather than resolved by guessing.
CORPUS_PACKAGE_JSON_SPDX = "ISC"

SOURCE_ID = f"ast-grep-essentials@{CORPUS_COMMIT[:7]}"
CREATED_BY = "ast_grep_rules_ingestion"

# The miniature project each check runs in. Deliberately NOT upstream's layout: upstream's
# `sgconfig.yml` declares `utilDirs: - utils` for a directory that holds only a `.gitkeep`,
# and the `utils:` blocks that actually break live inside individual rule files, so no
# config change fixes it. One rule + one test file + a minimal config is the smallest thing
# that can pass or fail for a reason attributable to that rule.
_SGCONFIG = "---\nruleDirs:\n  - rules\ntestConfigs:\n  - testDir: tests\n"

# `ast-grep test` cannot be asked about severity from the CLI, so rules declaring
# `severity: off` are filtered here, where we can count them.
_OFF_SEVERITY = "off"


@dataclass(frozen=True)
class AstGrepRule:
    """One rule plus its own test file, as found on disk."""

    rule_id: str
    language: str
    severity: str
    message: str
    note: str
    rule_path: Path
    test_path: Optional[Path]
    valid_count: int = 0
    invalid_count: int = 0
    license_spdx: str = CORPUS_LICENSE_SPDX
    source_path: str = ""

    @property
    def expected_case_count(self) -> int:
        """The liveness assertion, in AST-GREP'S UNIT.

        `ast-grep test` reports "N passed" where N counts rule TEST FILES, not individual
        valid/invalid snippets -- a test with 1 valid and 1 invalid case reports "1 passed"
        (measured). Per-rule isolation stages exactly one test file, so this is 1. Getting
        this wrong fails a CORRECT check, which is the assertion working: the first pilot
        asserted len(valid)+len(invalid) and correctly rejected a passing rule.
        """
        return 1 if self.test_path is not None else 0

    @property
    def snippet_count(self) -> int:
        """Individual valid+invalid snippets, for reporting only -- never for a verdict."""
        return self.valid_count + self.invalid_count

    @property
    def source_key(self) -> str:
        """Stable across re-ingest, so a re-run creates nothing new (ON CONFLICT DO NOTHING)."""
        return f"ast-grep:{self.rule_id}"

    @property
    def source_ref(self) -> str:
        return f"{SOURCE_ID}:{self.source_path}"


@dataclass
class IngestCounters:
    """Reason counts. Every non-accepting item lands in exactly one bucket, so
    accepted + sum(rejected/quarantined) == discovered. That identity is asserted in the
    proving tests, because a counter set that does not add up is how items get lost."""

    discovered: int = 0
    accepted: int = 0
    unchanged: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    quarantined_by_reason: dict[str, int] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)
    tools_missing: int = 0

    def reject(self, reason: str) -> None:
        self.rejected_by_reason[reason] = self.rejected_by_reason.get(reason, 0) + 1

    def quarantine(self, reason: str) -> None:
        self.quarantined_by_reason[reason] = self.quarantined_by_reason.get(reason, 0) + 1

    def verdict(self, verdict: str) -> None:
        self.verdicts[verdict] = self.verdicts.get(verdict, 0) + 1

    def accounted(self) -> int:
        return (self.accepted + self.unchanged
                + sum(self.rejected_by_reason.values())
                + sum(self.quarantined_by_reason.values()))

    def to_json(self) -> dict[str, Any]:
        return {
            "discovered": self.discovered,
            "accepted": self.accepted,
            "unchanged": self.unchanged,
            "accounted": self.accounted(),
            "rejected_by_reason": dict(sorted(self.rejected_by_reason.items())),
            "quarantined_by_reason": dict(sorted(self.quarantined_by_reason.items())),
            "verdicts": dict(sorted(self.verdicts.items())),
            "tools_missing": self.tools_missing,
        }


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _index_tests(tests_dir: Path) -> dict[str, Path]:
    """Map `rule_id -> test file` by the test's DECLARED `id`, never by its filename.

    The `<rule_id>-test.yml` convention holds for most of the corpus and fails for the
    rest, measured at the pinned commit:
      * `missing-nul-cpp-string-memcpy-copy-cpp`  -> `missing-nul-cpp-string-memcpy-cpp-test.yml` (no `copy-`)
      * `sizeof-this-cpp`                          -> `size-of-this-test.yml` (different words)
      * `networkcredential-hardcoded-secret-csharp`-> `networkcredential-hardcoded-secret-python-test.yml`
      * `jwt-simple-noverify-typescript`           -> `jwt-simple-noverify-typecript-test.yml` (typo)
      * `detect-angular-sce-disabled-typescript`    -> `detect-angular-sce-disabled-typescript.yml` (no suffix)
    Trusting filenames would have wrongly quarantined 5 usable rules. `id` is the same
    key ast-grep itself matches on, and it is unique across the package by the corpus's
    own contract -- so it is the only safe join.
    """
    index: dict[str, Path] = {}
    if not tests_dir.is_dir():
        return index
    for candidate in sorted(tests_dir.rglob("*.yml")):
        if "__snapshots__" in candidate.parts:
            continue
        data = _load_yaml(candidate)
        if isinstance(data, dict) and isinstance(data.get("id"), str) and data["id"]:
            index.setdefault(data["id"], candidate)
    return index


def discover_rules(root: Path) -> tuple[list[AstGrepRule], list[tuple[Path, str]]]:
    """Walk the checkout. Returns `(rules, unreadable)`.

    `unreadable` carries `(path, reason)` so a malformed rule, an id-mismatched test, or a
    test with no rule at all is REPORTED, never silently skipped -- the same discipline the
    corpus literature demands of us."""
    rules: list[AstGrepRule] = []
    unreadable: list[tuple[Path, str]] = []
    rules_dir = root / "rules"
    tests_dir = root / "tests"
    if not rules_dir.is_dir():
        return rules, [(rules_dir, "rules_dir_missing")]

    test_index = _index_tests(tests_dir)
    matched_test_ids: set[str] = set()

    for rule_file in sorted(rules_dir.rglob("*.yml")):
        data = _load_yaml(rule_file)
        if not isinstance(data, dict):
            unreadable.append((rule_file, "rule_unparseable"))
            continue
        rule_id = data.get("id")
        language = data.get("language")
        if not isinstance(rule_id, str) or not rule_id:
            unreadable.append((rule_file, "rule_missing_id"))
            continue
        if not isinstance(language, str) or not language:
            unreadable.append((rule_file, "rule_missing_language"))
            continue
        test_path: Optional[Path] = test_index.get(rule_id)
        valid_n = invalid_n = 0
        if test_path is not None:
            matched_test_ids.add(rule_id)
            test_data = _load_yaml(test_path)
            if not isinstance(test_data, dict):
                unreadable.append((test_path, "test_unparseable"))
                test_path = None
            elif test_data.get("id") != rule_id:
                # Should be unreachable -- the index is keyed on this very id -- but a
                # duplicate id in the corpus would surface here rather than silently.
                unreadable.append((test_path, "test_id_mismatch"))
                test_path = None
            else:
                valid_n = len(test_data.get("valid") or [])
                invalid_n = len(test_data.get("invalid") or [])
        rel = rule_file.relative_to(root).as_posix()
        rules.append(AstGrepRule(
            rule_id=rule_id,
            language=language,
            severity=str(data.get("severity") or "unspecified"),
            message=str(data.get("message") or "").strip(),
            note=str(data.get("note") or "").strip(),
            rule_path=rule_file,
            test_path=test_path,
            valid_count=valid_n,
            invalid_count=invalid_n,
            source_path=rel,
        ))
    # Tests whose id matches no rule. The corpus ships one of these (tests/java has 37
    # test files for 36 java rules) and it is exactly the shape that makes `ast-grep
    # test` exit 0 having run nothing, so it is counted rather than ignored.
    for test_id, test_path in test_index.items():
        if test_id not in matched_test_ids:
            unreadable.append((test_path, "test_without_rule"))
    return rules, unreadable


def check_files(rule: AstGrepRule) -> dict[str, bytes]:
    """The miniature project `ast-grep test` runs for this one rule."""
    if rule.test_path is None:
        raise CheckSpecError(
            f"rule {rule.rule_id!r} has no usable test file; it has no checkable outcome"
        )
    return {
        "sgconfig.yml": _SGCONFIG.encode("utf-8"),
        f"rules/{rule.rule_id}.yml": rule.rule_path.read_bytes(),
        f"tests/{rule.rule_id}-test.yml": rule.test_path.read_bytes(),
    }


def verifier_check_for(rule: AstGrepRule) -> VerifierCheck:
    return VerifierCheck(
        check_type="ast_grep",
        tool_version=VERIFIER_VERSIONS["ast_grep"],
        source=rule.source_ref,
        config={
            # Every knob that changes the verdict, recorded so the verdict is auditable.
            "snapshot_tests": False,
            "config": "./sgconfig.yml",
            "rule_id": rule.rule_id,
            "language": rule.language,
        },
        expected_case_count=rule.expected_case_count,
    )


def classify_license() -> dict[str, Any]:
    """Run the real allowlist gate per item and return the verdict plus the discrepancy."""
    verdict = classify_spdx(
        CORPUS_LICENSE_SPDX,
        source_path=f"{CORPUS_REPO}/LICENSE@{CORPUS_COMMIT}",
    )
    return {
        "verdict": verdict,
        "package_json_spdx": CORPUS_PACKAGE_JSON_SPDX,
    }


async def check_rule(
    rule: AstGrepRule,
    *,
    timeout_seconds: float = 60.0,
    ast_grep_binary: Optional[str] = None,
) -> CheckResult:
    """Run this rule's own tests through the sandboxed runner."""
    return await run_check(
        verifier_check_for(rule),
        check_files(rule),
        timeout_seconds=timeout_seconds,
        ast_grep_binary=ast_grep_binary,
    )


def procedure_payload(rule: AstGrepRule, check: VerifierCheck) -> dict[str, Any]:
    """Everything `capture_procedure` needs, derived only from VERIFIED inputs.

    The body is built from the rule's own `message` and `note` (its CWE id and OWASP
    reference) -- not from an LLM's paraphrase -- so what we store is what the corpus
    asserts. `postconditions` stays statement-strings because
    `execution/verification.py:89-100` only understands that shape; the RUNNABLE check
    lives in `verifier_check` (migration 125)."""
    body_bits = [b for b in (rule.message, rule.note) if b]
    body = "\n\n".join(body_bits) or rule.rule_id
    locator = {
        "source_id": SOURCE_ID,
        "uri": CORPUS_URI,
        "path": rule.source_path,
        "commit": CORPUS_COMMIT,
        "content_hash": hashlib.sha256(rule.rule_path.read_bytes()).hexdigest(),
        "granularity": "document",
    }
    return {
        "name": f"ast-grep rule: {rule.rule_id}",
        "goal": (
            f"Flag {rule.language} code matching the upstream rule {rule.rule_id!r} "
            "so the defect class is caught statically rather than in review."
        ),
        # No `domain=`: capture_procedure passes `scope_entity_id or domain` into
        # No `domain=`: capture_procedure passes `scope_entity_id or domain` into
        # v0_gate.validate_scope, and a `global` scope may not carry an entity_id
        # (V0Violation). The language is kept in `domain_payload` instead, which is
        # where a per-item attribute belongs anyway.
        "steps": [
            {
                "order": 1,
                "text": (
                    f"Run the pinned ast-grep ({VERIFIER_VERSIONS['ast_grep']}) rule "
                    f"{rule.rule_id!r} over the files under test; it reports a match for "
                    "each occurrence of the pattern this rule encodes."
                ),
                "verification": (
                    "ast-grep test over the rule's own valid/invalid cases: every valid "
                    f"case must produce no match and every invalid case must produce one "
                    f"({rule.snippet_count} snippets in 1 test group)."
                ),
                # `kind: "binary"` with `sandbox_policy: "isolated_network"` is the
                # binding vocabulary's own shape (source_locators.BINDING_KINDS), and
                # `verifier` is an allowed key on it -- so the executable step and its
                # check are stored where a consumer already looks.
                "binding": {
                    "kind": "binary",
                    "binary": "ast-grep",
                    # A `binary` binding needs a `path` (where it lives) as well as the
                    # name -- see source_locators.validate_binding. The resolved path is
                    # environment-specific, so the stable entrypoint name is stored and the
                    # exact version is pinned in `verifier` below.
                    "path": "ast-grep",
                    "args": ["test", "--skip-snapshot-tests"],
                    "sandbox_policy": "isolated_network",
                    "verifier": check.to_json(),
                },
            }
        ],
        "postconditions": [
            f"ast-grep rule {rule.rule_id} matches none of its {rule.valid_count} valid "
            f"cases and all {rule.invalid_count} of its invalid cases."
        ],
        "provenance": "system_pending_review",
        "scope_type": "global",
        "visibility": "public",
        "source_key": rule.source_key,
        "source_locator": locator,
        "verifier_check": check.to_json(),
        "domain_payload": {
            "language": rule.language,
            "rule_id": rule.rule_id,
            "severity": rule.severity,
            "cwe_note": rule.note,
            "license_spdx": rule.license_spdx,
        },
        "created_by": CREATED_BY,
    }


async def ingest_ast_grep_rules(
    pool: Any,
    root: Path,
    *,
    limit: Optional[int] = None,
    owner_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    timeout_seconds: float = 60.0,
    ast_grep_binary: Optional[str] = None,
    run_checks: bool = True,
    counters: Optional[IngestCounters] = None,
) -> IngestCounters:
    """Discover, license-gate, VERIFY, then capture. A rule that does not pass its own
    test is never captured -- there is no "capture then quarantine" path here on purpose."""
    from app.services.procedures import capture_procedure  # local: avoids an import cycle

    counters = counters or IngestCounters()
    license_info = classify_license()
    license_verdict = license_info["verdict"]
    if license_verdict.decision != "ALLOW":
        counters.reject(f"license_{license_verdict.decision.lower()}")
        counters.discovered = 0
        return counters

    rules, unreadable = discover_rules(root)
    for _path, reason in unreadable:
        counters.quarantine(reason)
    if limit is not None:
        rules = rules[:limit]
    counters.discovered = len(rules)

    for rule in rules:
        if rule.severity == _OFF_SEVERITY:
            counters.quarantine("severity_off")
            continue
        if rule.test_path is None:
            counters.quarantine("no_test_file")
            continue
        if run_checks:
            result = await check_rule(
                rule, timeout_seconds=timeout_seconds, ast_grep_binary=ast_grep_binary
            )
            counters.verdict(result.verdict)
            if not result.passed:
                # `error` here is usually the upstream corpus's own defect (the 30 rules
                # with the prelude `utils:` form), not ours -- but either way the rule has
                # no verified outcome, so it becomes no Procedure.
                counters.quarantine(f"check_{result.verdict}")
                continue
        check = verifier_check_for(rule)
        row = await capture_procedure(
            pool,
            owner_id=owner_id,
            tenant_id=tenant_id,
            **procedure_payload(rule, check),
        )
        # `capture_procedure` returns `{"id", "procedure_id", "duplicate": True}` when the
        # unique index on source_key already held a live row -- it does NOT return None.
        # Counting on `row is None` would report every re-ingest as a fresh acceptance.
        if isinstance(row, dict) and row.get("duplicate"):
            counters.unchanged += 1
        else:
            counters.accepted += 1
    return counters


__all__ = [
    "CORPUS_REPO", "CORPUS_COMMIT", "CORPUS_LICENSE_SPDX", "CORPUS_URI",
    "CORPUS_PACKAGE_JSON_SPDX", "SOURCE_ID", "CREATED_BY",
    "AstGrepRule", "IngestCounters", "discover_rules", "check_files",
    "verifier_check_for", "classify_license", "check_rule", "procedure_payload",
    "ingest_ast_grep_rules",
]
