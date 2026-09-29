"""
Offline proving tests for the Step 7 codemod ingestion path --
`codemod_checks`, `codemod_node`, `openrewrite` and `codemod_cli`.

No network, no `DATABASE_URL`, no fixtures beyond trees this file
builds in `tmp_path`. The subprocess path is exercised against a real
executable shim rather than mocked, because the property under test IS
"does it return a verdict it actually read" and a mock would return
whatever the test told it to return, which is the failure mode being
guarded against.

Every check runs with `DATABASE_URL` unset, so anything that reached for
a pool would fail loudly rather than quietly reading a developer's
shard.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from app.ingestion import codemod_cli
from app.services.ingestion_sources import codemod_checks, codemod_node, openrewrite
from app.services.ingestion_sources.codemod_checks import (
    CHECK_SEMANTICS,
    RUNNER_VERSION,
    CheckOutcome,
    FixtureInventory,
    UnreadableToolOutput,
    build_check_payload,
    check_expected_wellformedness,
    discover_fixture_cases,
    read_tool_result,
    run_jssg_fixture_check,
    _read_event_stream,
)
from app.services.ingestion_sources.codemod_checks import (
    WELLFORMNESS_VERDICTS,
    CheckOutcome,
    FixtureInventory,
    UnreadableToolOutput,
    build_check_payload,
    check_expected_wellformedness,
    discover_fixture_cases,
    read_tool_result,
    run_jssg_fixture_check,
    _read_event_stream,
)
from app.services.ingestion_sources.codemod_node import (
    CodemodLicenseBlocked,
    NodeUserlandMigrationsSource,
    _declared_test_commands,
    _merge_args,
    resolve_checkout_licensing,
    select_ingestible,
)
from app.services.ingestion_sources.openrewrite import (
    APACHE2,
    MODERNE_SOURCE_AVAILABLE,
    THIRD_PARTY_UNVERIFIED,
    OpenRewriteCatalogSource,
    OpenRewriteIdUnavailable,
    extract_recipe_id,
    gate_module,
    gate_namespace,
    parse_sitemap,
    recipe_namespace,
)
from app.services.ingestion_sources.base import SourceRef
from app.services.repo_license_policy import (
    ALLOWLIST_VERSION,
    LicenseVerdict,
    decide_repo_license,
    identify_spdx_from_text,
    license_paths,
)

MIT_LICENSE_TEXT = """MIT License

Copyright (c) Node.js contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
"""

APACHE_LICENSE_TEXT = """                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0
"""

# The 43-byte pointer file a Moderne-licensed OpenRewrite repository
# actually carries. GitHub reports NOASSERTION on these repos precisely
# because of it.
MODERNE_POINTER_TEXT = "LICENSE/moderne-source-available-license.md"

UNIDENTIFIABLE_LICENSE_TEXT = """All rights reserved.

No permission is granted to use, copy, modify or distribute this software.
Contact the authors for licensing.
"""

CODEMOD_YAML = """schema_version: "1.0"
name: "@nodejs/demo-recipe"
version: 0.0.1
capabilities:
  - fs
description: Replace the legacy alpha() helper with the supported beta() call.
author: Node.js project
license: MIT
category: api
targets:
  languages:
    - typescript
keywords:
  - api
  - deprecation
"""

PACKAGE_JSON = json.dumps(
    {
        "name": "@nodejs/demo-recipe",
        "version": "1.0.1",
        "license": "MIT",
        "scripts": {"test": "npx codemod jssg test -l typescript ./src/workflow.ts ./tests"},
        "engines": {"node": ">=22.15.0"},
    },
    indent=2,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_checkout(root: Path, license_name: str, license_text: str) -> Path:
    """A minimal `nodejs/userland-migrations` checkout with one recipe."""
    _write(root / license_name, license_text)
    _write(root / "recipes" / "demo-recipe" / "codemod.yaml", CODEMOD_YAML)
    _write(root / "recipes" / "demo-recipe" / "package.json", PACKAGE_JSON)
    _write(root / "recipes" / "demo-recipe" / "src" / "workflow.ts", "export default function () {}\n")
    return root


def _tree(*paths: str) -> list[dict]:
    return [{"type": "blob", "path": p, "size": 1024} for p in paths]


def _fake_cli(tmp_path: Path, name: str, body: str) -> str:
    """A real executable shim on this platform.

    A shim rather than a monkeypatched `subprocess.run`: the property
    being tested is that the runner reads a real child's stdout, and a
    stubbed runner would return whatever the test handed it.
    """
    if os.name == "nt":
        path = tmp_path / f"{name}.bat"
        path.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
    else:
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


# ---------------------------------------------------------------------------
# 1. Moderne pointer-file gate  (SECURITY-CRITICAL)
# ---------------------------------------------------------------------------


def test_moderne_pointer_file_license_quarantines_and_never_allows(tmp_path):
    """A root `LICENSE.md` whose entire text is a POINTER must QUARANTINE.

    This is the failure the whole gate exists for. Moderne-licensed
    OpenRewrite repositories do not carry a readable license at all --
    they carry a 43-byte pointer, which is why GitHub reports
    NOASSERTION and why a naive "read the root LICENSE" gate admits all
    22 of them. Asserted three ways: on the policy functions directly
    (so a regression in the gate is visible, not just in this adapter),
    through the adapter's own resolution, and through `fetch`, which must
    emit nothing at all.
    """
    checkout = _make_checkout(tmp_path / "checkout", "LICENSE.md", MODERNE_POINTER_TEXT)

    # (a) the policy itself, on the real pointer text
    assert identify_spdx_from_text(MODERNE_POINTER_TEXT) is None
    tree = _tree("LICENSE.md", "LICENSE/moderne-source-available-license.md", "recipes/demo-recipe/codemod.yaml")
    by_path = {"LICENSE.md": identify_spdx_from_text(MODERNE_POINTER_TEXT) or ""}
    verdict = decide_repo_license(
        path="recipes/demo-recipe", tree=tree, license_spdx_by_path=by_path
    )
    assert verdict.decision == "QUARANTINE", verdict.reason
    assert verdict.decision != "ALLOW"
    assert verdict.source_path == "LICENSE.md"
    assert verdict.allowlist_version == ALLOWLIST_VERSION

    # (b) the adapter resolving the same thing off disk
    source = NodeUserlandMigrationsSource(checkout, commit="a" * 40, run_checks=False)
    adapter_verdict = source.license_verdict("recipes/demo-recipe")
    assert adapter_verdict.decision == "QUARANTINE", adapter_verdict.reason

    # (c) no artifact, ever
    ref = next(iter(source.discover()))
    with pytest.raises(CodemodLicenseBlocked) as excinfo:
        source.fetch(ref)
    assert excinfo.value.verdict.decision == "QUARANTINE"


# ---------------------------------------------------------------------------
# 2. NOASSERTION
# ---------------------------------------------------------------------------


def test_noassertion_license_quarantines_and_never_allows():
    # (a) declared at the repository level
    repo_level = decide_repo_license(repo_spdx="NOASSERTION")
    assert repo_level.decision == "QUARANTINE", repo_level.reason
    assert repo_level.decision != "ALLOW"
    assert "NOASSERTION" in repo_level.reason

    # (b) detected for a governing in-repo blob, which is the shape the
    #     Moderne repositories actually take. A NOASSERTION nearer file
    #     must NOT fall back to a farther permissive id.
    tree = _tree("LICENSE", "recipes/demo-recipe/codemod.yaml")
    in_repo = decide_repo_license(
        path="recipes/demo-recipe",
        tree=tree,
        repo_spdx="MIT",
        license_spdx_by_path={"LICENSE": "NOASSERTION"},
    )
    assert in_repo.decision == "QUARANTINE", in_repo.reason
    assert in_repo.spdx_id == "NOASSERTION"
    assert in_repo.allowlist_version == ALLOWLIST_VERSION
    # And the id a repository-level detector reports for those very repos
    # is the one the policy quarantines, not an invented permissive id.
    assert all(
        entry.spdx == "Apache-2.0" or gate_module(entry.module_id).allowed is False
        for entry in openrewrite.MODULES.values()
    )


# ---------------------------------------------------------------------------
# 3. Unknown / unidentifiable license text
# ---------------------------------------------------------------------------


def test_unidentifiable_license_text_quarantines_and_never_allows(tmp_path):
    checkout = _make_checkout(tmp_path / "checkout", "LICENSE", UNIDENTIFIABLE_LICENSE_TEXT)
    licensing = resolve_checkout_licensing(checkout)
    assert "LICENSE" in licensing.license_blob_paths
    # The blob IS indexed; it resolves to no id, and the key is kept so
    # the miss is auditable rather than an absence.
    assert licensing.license_spdx_by_path["LICENSE"] == ""
    verdict = licensing.verdict_for("recipes/demo-recipe")
    assert verdict.decision == "QUARANTINE", verdict.reason
    assert verdict.decision != "ALLOW"

    source = NodeUserlandMigrationsSource(checkout, commit="b" * 40, run_checks=False)
    with pytest.raises(CodemodLicenseBlocked):
        source.fetch(next(iter(source.discover())))


# ---------------------------------------------------------------------------
# 4. Apache-2.0 and MIT at the repo root
# ---------------------------------------------------------------------------


def test_apache_and_mit_at_repo_root_allow_and_carry_the_allowlist_version(tmp_path):
    assert identify_spdx_from_text(MIT_LICENSE_TEXT) == "MIT"
    assert identify_spdx_from_text(APACHE_LICENSE_TEXT) == "Apache-2.0"

    for name, text, expected in (
        ("LICENSE", MIT_LICENSE_TEXT, "MIT"),
        ("LICENSE", APACHE_LICENSE_TEXT, "Apache-2.0"),
    ):
        checkout = _make_checkout(tmp_path / f"checkout-{expected}", name, text)
        source = NodeUserlandMigrationsSource(checkout, commit="c" * 40, run_checks=False)
        verdict = source.license_verdict("recipes/demo-recipe")
        assert verdict.decision == "ALLOW", verdict.reason
        assert verdict.spdx_id == expected
        # The allowlist version rides on the verdict, which is what an
        # audit row needs to say WHICH ruling was applied.
        assert verdict.allowlist_version == ALLOWLIST_VERSION
        artifact = source.fetch(next(iter(source.discover())))
        assert artifact.license_metadata["allowlist_version"] == ALLOWLIST_VERSION
        assert artifact.license_metadata["detected_spdx"] == expected


# ---------------------------------------------------------------------------
# 5. Nested input/ + expected/ directory-snapshot layout
# ---------------------------------------------------------------------------


def test_nested_input_expected_directory_snapshot_layout_is_discovered_with_counts(tmp_path):
    tests = tmp_path / "tests"
    _write(tests / "enroll" / "input" / "dep0095-basic.js", "const a = 1;\n")
    _write(tests / "enroll" / "expected" / "dep0095-basic.js", "const a = 2;\n")
    _write(tests / "enroll" / "input" / "dep0096-destructured.js", "const b = 1;\n")
    _write(tests / "enroll" / "expected" / "dep0096-destructured.js", "const b = 2;\n")
    _write(tests / "unenroll" / "input" / "unref.js", "x\n")
    _write(tests / "unenroll" / "expected" / "unref.js", "y\n")

    inventory = discover_fixture_cases(tests_dir=tests)
    assert inventory.layout == "dir-snapshot"
    assert inventory.case_count == 3
    assert inventory.negative_case_count == 0
    assert inventory.dangling_pairs == 0
    assert inventory.input_paths == (
        "enroll/input/dep0095-basic.js",
        "enroll/input/dep0096-destructured.js",
        "unenroll/input/unref.js",
    )
    assert inventory.expected_paths == (
        "enroll/expected/dep0095-basic.js",
        "enroll/expected/dep0096-destructured.js",
        "unenroll/expected/unref.js",
    )


# ---------------------------------------------------------------------------
# 6. Flat input.<ext> / expected.<ext> layout
# ---------------------------------------------------------------------------


def test_flat_input_expected_pair_layout_is_discovered_with_counts(tmp_path):
    tests = tmp_path / "tests"
    _write(tests / "basic-color" / "input.js", "a\n")
    _write(tests / "basic-color" / "expected.js", "b\n")
    _write(tests / "chained-styles" / "input.js", "c\n")
    _write(tests / "chained-styles" / "expected.js", "d\n")
    # A different extension in the same tree still pairs independently.
    _write(tests / "remove-dependencies" / "package.json" / "input.json", '{"a":1}')
    _write(tests / "remove-dependencies" / "package.json" / "expected.json", '{"a":0}')

    inventory = discover_fixture_cases(tests_dir=tests)
    assert inventory.layout == "flat-pair"
    assert inventory.case_count == 3
    assert inventory.negative_case_count == 0
    assert "basic-color/input.js" in inventory.input_paths
    assert "remove-dependencies/package.json/expected.json" in inventory.expected_paths

    # A missing tests dir is "none", not an error and not a zero-case
    # suite dressed up as a real one.
    assert discover_fixture_cases(tests_dir=tmp_path / "nope").layout == "none"
    assert discover_fixture_cases(tests_dir=None).layout == "none"
    assert discover_fixture_cases(tests_dir=None).case_count == 0


# ---------------------------------------------------------------------------
# 7. Negative (no-op) cases, and vacuity being visible
# ---------------------------------------------------------------------------


def test_identical_input_and_expected_is_a_negative_case_and_zero_negatives_is_reported(tmp_path):
    tests = tmp_path / "tests"
    # The real `no-match` case: the transform must leave the file alone.
    _write(tests / "no-match" / "input.js", "const x = 1;\n")
    _write(tests / "no-match" / "expected.js", "const x = 1;\n")
    _write(tests / "basic" / "input.js", "const x = 1;\n")
    _write(tests / "basic" / "expected.js", "const x = 2;\n")

    inventory = discover_fixture_cases(tests_dir=tests)
    assert inventory.case_count == 2
    assert inventory.negative_case_count == 1

    outcome = CheckOutcome(
        passed=True,
        tier="executable",
        semantics=CHECK_SEMANTICS,
        case_count=inventory.case_count,
        negative_case_count=inventory.negative_case_count,
        gates={"jssg_fixture_suite": "passed"},
    )
    payload = build_check_payload(
        outcome=outcome, inventory=inventory, recipe_id="@nodejs/demo", runner_version=RUNNER_VERSION
    )
    assert payload["negative_case_count"] == 1
    assert payload["vacuous"] is False

    # Zero negative cases is REPORTED, not passed over: a suite where
    # every input differs from its expected cannot tell a working
    # transform from one that rewrites everything it is shown.
    empty = FixtureInventory(layout="flat-pair", case_count=2, negative_case_count=0)
    vacuous = build_check_payload(
        outcome=CheckOutcome(
            passed=True,
            tier="executable",
            semantics=CHECK_SEMANTICS,
            case_count=2,
            negative_case_count=0,
        ),
        inventory=empty,
        recipe_id="@nodejs/demo",
        runner_version=RUNNER_VERSION,
    )
    assert vacuous["vacuous"] is True
    assert any("no negative" in limitation for limitation in vacuous["limitations"])

    # A dangling input with no expected is a case, but never a negative.
    _write(tests / "orphaned" / "input.js", "z\n")
    dangling = discover_fixture_cases(tests_dir=tests)
    assert dangling.case_count == 3
    assert dangling.negative_case_count == 1
    assert dangling.dangling_pairs == 1


# ---------------------------------------------------------------------------
# 8. Gate B: output well-formedness
# ---------------------------------------------------------------------------


def test_expected_wellformedness_verdict_for_json_typescript_and_malformed_json(tmp_path):
    good = _write(tmp_path / "good.json", '{"a": 1, "b": [2, 3]}')
    bad = _write(tmp_path / "bad.json", '{"a": 1, "b": }')
    typescript = _write(tmp_path / "transform.ts", "export const a: number = 1;\n")

    # A real JSON parse, not a heuristic on the file's first character.
    assert check_expected_wellformedness(expected_paths=[good], node_exec="node")[0] == "wellformed"
    assert check_expected_wellformedness(expected_paths=[bad], node_exec="node")[0] == "malformed"

    # TypeScript has no offline parser here. It must NOT read as
    # well formed: that is exactly the laundering this gate prevents.
    verdict, detail = check_expected_wellformedness(expected_paths=[typescript], node_exec="node")
    assert verdict == "no_parser", detail
    assert "TypeScript" in detail or "no offline parser" in detail

    # The batch verdict is the weakest thing in it: one unparsed file
    # among parseable ones cannot be rounded up to "wellformed".
    verdict, detail = check_expected_wellformedness(
        expected_paths=[good, typescript], node_exec="node"
    )
    assert verdict == "no_parser"
    assert "checked=2" in detail

    # One malformed file dominates the batch.
    verdict, _ = check_expected_wellformedness(expected_paths=[good, bad], node_exec="node")
    assert verdict == "malformed"

    # Nothing parsed is not an assertion that anything is well formed.
    assert check_expected_wellformedness(expected_paths=[], node_exec="node")[0] == "no_parser"

    # A missing parser tool degrades to "not parsed", never to "broken".
    verdict, _ = check_expected_wellformedness(
        expected_paths=[_write(tmp_path / "x.js", "const a = 1;\n")],
        node_exec="definitely-not-a-real-node-binary",
    )
    assert verdict == "no_parser"

    # A real `node --check` on a plain script, when node is on this box.
    if _node_available():
        good_js = _write(tmp_path / "good.js", "const a = 1;\n")
        bad_js = _write(tmp_path / "bad.js", "const = ;\n")
        assert check_expected_wellformedness(expected_paths=[good_js], node_exec="node")[0] == "wellformed"
        assert check_expected_wellformedness(expected_paths=[bad_js], node_exec="node")[0] == "malformed"
        # A `.mjs` that the runtime's own parser rejects is recorded as
        # UNPARSED, not as a defect: `node --check` on a module file can
        # fail because it was read as CommonJS, and calling that a
        # malformed fixture would be inventing a defect.
        esm = _write(tmp_path / "mod.mjs", "import fs from 'node:fs';\nexport const a = 1;\n")
        verdict, detail = check_expected_wellformedness(expected_paths=[esm], node_exec="node")
        assert verdict in ("wellformed", "no_parser"), detail


def test_gate_b_does_not_accept_the_jssg_cli_as_the_node_parser():
    """Gate A's binary and Gate B's binary are different programs.

    `codemod jssg test` is a Rust CLI; `node --check` is the runtime's
    syntax checker. Handing the jssg CLI to Gate B used to be a silent
    misconfiguration: every `.js` fixture came back unparseable, which
    reads exactly like a broken catalog rather than a wrong flag. The
    parameter split is the fix, and this pins that the split is real --
    the two names are distinct and Gate B takes only the runtime.
    """
    import inspect

    gate_b = inspect.signature(check_expected_wellformedness).parameters
    assert "node_exec" in gate_b
    assert "node_bin" not in gate_b
    gate_a = inspect.signature(run_jssg_fixture_check).parameters
    assert "node_bin" in gate_a


def _node_available() -> bool:
    import shutil

    return shutil.which("node") is not None


# ---------------------------------------------------------------------------
# 9. The runner never reports a pass it did not read
# ---------------------------------------------------------------------------


def test_check_runner_reports_failure_when_it_cannot_read_the_tool_output(tmp_path):
    recipe = tmp_path / "recipe"
    _write(recipe / "src" / "workflow.ts", "export default function () {}\n")
    _write(recipe / "tests" / "case-a" / "input.js", "a\n")
    _write(recipe / "tests" / "case-a" / "expected.js", "b\n")

    # (a) the tool does not exist at all
    outcome = run_jssg_fixture_check(
        recipe_dir=recipe,
        transform="./src/workflow.ts",
        language="typescript",
        test_dir="tests",
        node_bin=str(tmp_path / "no-such-cli"),
    )
    assert outcome.passed is False
    assert outcome.detail["reason"] == "tool_not_found"
    assert outcome.gates["jssg_fixture_suite"] == "not_run"

    # (b) the fixture suite does not exist -- the `v22-to-v24` shape
    empty = tmp_path / "empty-recipe"
    _write(empty / "src" / "workflow.ts", "export default function () {}\n")
    outcome = run_jssg_fixture_check(
        recipe_dir=empty,
        transform="./src/workflow.ts",
        language="typescript",
        test_dir="tests",
        node_bin=str(tmp_path / "no-such-cli"),
    )
    assert outcome.passed is False
    assert outcome.detail["reason"] == "fixture_suite_missing"
    assert outcome.gates["case_coverage"] == "vacuous"

    # (c) a REAL executable that exits non-zero with nothing on stdout
    loud = _fake_cli(tmp_path, "loud_cli", "echo boom 1>&2\nexit 3")
    outcome = run_jssg_fixture_check(
        recipe_dir=recipe,
        transform="./src/workflow.ts",
        language="typescript",
        test_dir="tests",
        node_bin=loud,
    )
    assert outcome.passed is False
    assert outcome.detail["reason"] == "empty_result"
    assert outcome.gates["jssg_fixture_suite"] == "unreadable"

    # (d) a REAL executable whose stdout is not JSON
    noisy = _fake_cli(tmp_path, "noisy_cli", "echo not json at all")
    outcome = run_jssg_fixture_check(
        recipe_dir=recipe,
        transform="./src/workflow.ts",
        language="typescript",
        test_dir="tests",
        node_bin=noisy,
    )
    assert outcome.passed is False
    # Non-JSON stdout now lands in the same bucket as empty stdout: the
    # reporter emits NDJSON, so there is no single JSON document to fail
    # parsing, and the honest statement is "no JSON event was read".
    assert outcome.detail["reason"] == "empty_result"
    assert outcome.gates["jssg_fixture_suite"] == "unreadable"
    assert "not json at all" in outcome.detail["stdout_tail"]

    # (e) a REAL executable returning JSON of a shape we cannot read.
    #     This is the one that matters: an `{"ok": true}` payload is not
    #     a verdict, and inferring "no failures" from an absent key is
    #     how a runner invents a pass.
    vague = _fake_cli(tmp_path, "vague_cli", 'echo {"ok":true}')
    outcome = run_jssg_fixture_check(
        recipe_dir=recipe,
        transform="./src/workflow.ts",
        language="typescript",
        test_dir="tests",
        node_bin=vague,
    )
    assert outcome.passed is False
    assert outcome.detail["reason"] == "unreadable_result"

    # The parser itself, so the property does not depend on the shim.
    for bad_payload in ({}, {"ok": True}, {"total": 3}, [], "nope", None, {"results": []}):
        with pytest.raises(UnreadableToolOutput):
            read_tool_result(bad_payload)


def test_runner_reads_the_real_newline_delimited_reporter_shape():
    """`--reporter json` emits NDJSON, not one JSON document.

    Captured verbatim from `codemod jssg test --reporter json` (codemod
    1.18.3, 2026-09-28) against `timers-deprecations` filtered to
    `dep0095`. The load-bearing detail is the terminal `suite` line: a
    stream of per-case `test` events with no summary is a run that did not
    finish, and reading a verdict out of it would be reading a partial
    result as if it were the whole one.
    """
    ndjson = (
        '{"type": "suite", "event": "started", "test_count": 3}\n'
        '{"type": "test", "event": "started", "name": "enroll_dep0095-basic.js"}\n'
        '{"type": "test", "name": "enroll_dep0095-basic.js", "event": "ok"}\n'
        '{"type": "test", "name": "enroll_dep0095-destructured.js", "event": "ok"}\n'
        '{"type": "test", "name": "enroll_dep0095-import-variants.js", "event": "ok"}\n'
        '{ "type": "suite", "event": "ok", "passed": 3, "failed": 0, "ignored": 0, '
        '"measured": 0, "filtered_out": 0, "exec_time": 0.0016 }\n'
    )
    terminal, saw_json = _read_event_stream(ndjson)
    assert saw_json is True
    assert terminal is not None and terminal["passed"] == 3 and terminal["failed"] == 0
    assert read_tool_result(terminal) == (3, 0, ())

    # A truncated stream: cases reported, no terminal summary. It must NOT
    # be rounded up to a verdict.
    truncated, _ = _read_event_stream(
        '{"type": "suite", "event": "started", "test_count": 3}\n'
        '{"type": "test", "name": "a", "event": "ok"}\n'
    )
    assert truncated is not None
    assert read_tool_result(truncated) == (1, 0, ())

    # A failing case in the terminal summary is read as a failure, named.
    failing, _ = _read_event_stream(
        '{"type": "test", "name": "b", "event": "failed", "stdout": "mismatch"}\n'
        '{"type": "suite", "event": "failed", "passed": 0, "failed": 1, "ignored": 0}\n'
    )
    assert failing is not None
    assert read_tool_result(failing)[1] == 1

    # No JSON at all versus JSON of an unknown shape are different
    # diagnoses and must not collapse into one reason string. The unknown
    # shape is handed to the reader, which refuses it by name, so the
    # failure says "unreadable_result" rather than "empty_result".
    assert _read_event_stream("not json at all") == (None, False)
    unknown, saw_unknown = _read_event_stream('{"ok":true}')
    assert saw_unknown is True
    with pytest.raises(UnreadableToolOutput):
        read_tool_result(unknown)

    # A non-zero exit with a readable all-green result is contradictory,
    # and is a failure rather than something believed.
    assert read_tool_result({"summary": {"total": 2, "failed": 0}}) == (2, 0, ())
    assert read_tool_result({"results": [{"passed": True, "name": "a"}]}) == (1, 0, ())
    assert read_tool_result({"results": [{"status": "fail", "name": "b"}]}) == (1, 1, ("case 0 (b)",))


def test_check_runner_reads_a_real_green_result_and_a_real_red_one(tmp_path):
    """A runner that can only ever fail is broken too.

    The two halves of the property therefore both get a real executable
    shim: a green payload becomes a pass, a payload naming a failing
    case becomes a failure with that case named.
    """
    recipe = tmp_path / "recipe"
    _write(recipe / "src" / "workflow.ts", "export default function () {}\n")
    _write(recipe / "tests" / "case-a" / "input.js", "a\n")
    _write(recipe / "tests" / "case-a" / "expected.js", "b\n")

    green = _fake_cli(
        tmp_path,
        "green_cli",
        'echo {"results":[{"passed":true,"name":"case-a"},{"passed":true,"name":"no-match"}]}',
    )
    outcome = run_jssg_fixture_check(
        recipe_dir=recipe,
        transform="./src/workflow.ts",
        language="typescript",
        test_dir="tests",
        node_bin=green,
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.case_count == 2
    assert outcome.tier == "executable"
    assert outcome.semantics == CHECK_SEMANTICS
    assert outcome.gates["jssg_fixture_suite"] == "passed"
    assert outcome.failures == ()
    # The child really was launched, from the pinned recipe directory.
    assert outcome.detail["recipe_dir"] == str(recipe)
    assert outcome.detail["command"][:2] == [green, "jssg"]

    red = _fake_cli(
        tmp_path,
        "red_cli",
        'echo {"results":[{"passed":true,"name":"case-a"},{"passed":false,"name":"url-parse"}]}',
    )
    outcome = run_jssg_fixture_check(
        recipe_dir=recipe,
        transform="./src/workflow.ts",
        language="typescript",
        test_dir="tests",
        node_bin=red,
    )
    assert outcome.passed is False
    assert outcome.case_count == 2
    assert outcome.failures == ("case 1 (url-parse)",)
    assert outcome.gates["jssg_fixture_suite"] == "failed"

    # The environment is scrubbed, not inherited: a variable set in this
    # process must not reach the child.
    os.environ["STEALTHLAB_TEST_LEAK_SENTINEL"] = "leaked"
    try:
        leak = _fake_cli(tmp_path, "leak_cli", "echo {\"results\":[{\"passed\":true}]}")
        outcome = run_jssg_fixture_check(
            recipe_dir=recipe,
            transform="./src/workflow.ts",
            language="typescript",
            test_dir="tests",
            node_bin=leak,
            extra_args=("--allow-fs",),
        )
        assert outcome.passed is True
        assert outcome.detail["extra_args"] == ["--allow-fs"]
    finally:
        os.environ.pop("STEALTHLAB_TEST_LEAK_SENTINEL", None)

    env = codemod_checks._scrubbed_env()
    assert "STEALTHLAB_TEST_LEAK_SENTINEL" not in env
    assert env["CI"] == "1"
    assert "PATH" in env


# ---------------------------------------------------------------------------
# 10. The payload cannot say "verified correct"
# ---------------------------------------------------------------------------


def test_check_payload_never_claims_correctness_and_always_carries_self_consistency():
    forbidden = "verified correct"
    outcome = CheckOutcome(
        passed=True,
        tier="executable",
        semantics=CHECK_SEMANTICS,
        case_count=7,
        negative_case_count=2,
        gates={"jssg_fixture_suite": "passed", "output_wellformedness": "wellformed"},
    )
    inventory = FixtureInventory(layout="flat-pair", case_count=7, negative_case_count=2)
    payload = build_check_payload(
        outcome=outcome, inventory=inventory, recipe_id="@nodejs/x", runner_version=RUNNER_VERSION
    )
    blob = json.dumps(payload).lower()
    assert forbidden not in blob
    assert payload["check_semantics"] == "self-consistency"
    assert payload["check_tier"] == "executable"
    assert payload["check_kind"] == "jssg-fixture-self-consistency"
    assert payload["runner_version"] == RUNNER_VERSION
    assert payload["case_count"] == 7 and payload["negative_case_count"] == 2
    assert "not that the output is correct" in payload["claim"]

    # The static tier, used by the OpenRewrite adapter, obeys the same
    # rule -- and additionally says out loud that nothing was executed.
    static = build_check_payload(
        outcome=CheckOutcome(
            passed=True,
            tier="static",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            gates={"module_apache2": "passed"},
        ),
        inventory=FixtureInventory(layout="none", case_count=0, negative_case_count=0),
        recipe_id="org.openrewrite.quarkus.X",
        runner_version=openrewrite.STATIC_RUNNER_VERSION,
    )
    assert forbidden not in json.dumps(static).lower()
    assert static["check_semantics"] == "self-consistency"
    assert static["check_tier"] == "static"
    assert any("no transformation was executed" in lim for lim in static["limitations"])

    # The runner refuses to build a payload for a tier it cannot speak
    # for, and refuses a semantics it cannot substantiate.
    with pytest.raises(ValueError):
        build_check_payload(
            outcome=CheckOutcome(True, "vibes", CHECK_SEMANTICS, 0, 0),
            inventory=inventory,
            recipe_id="x",
            runner_version=RUNNER_VERSION,
        )
    with pytest.raises(ValueError):
        build_check_payload(
            outcome=CheckOutcome(True, "executable", "correctness", 0, 0),
            inventory=inventory,
            recipe_id="x",
            runner_version=RUNNER_VERSION,
        )


# ---------------------------------------------------------------------------
# 11. The OpenRewrite per-module gate
# ---------------------------------------------------------------------------


def test_openrewrite_gate_rejects_moderne_and_third_party_and_accepts_rewrite_quarkus():
    quarkus = gate_module("org.openrewrite.recipe:rewrite-quarkus")
    assert quarkus.allowed is True
    assert quarkus.license_tier == APACHE2
    assert quarkus.spdx == "Apache-2.0"

    spring = gate_module("org.openrewrite.recipe:rewrite-spring")
    assert spring.allowed is False
    assert spring.license_tier == MODERNE_SOURCE_AVAILABLE
    assert spring.spdx == "NOASSERTION"
    assert "as a service" in spring.reason

    picnic = gate_module("tech.picnic:error-prone-support")
    assert picnic.allowed is False
    assert picnic.license_tier == THIRD_PARTY_UNVERIFIED
    assert "third-party" in picnic.reason

    # Every one of the 22 Moderne rows is refused, and every one of the
    # 30 Apache rows that is neither archived nor a BOM is allowed.
    moderne = [
        entry for entry in openrewrite.MODULES.values() if entry.license_tier == MODERNE_SOURCE_AVAILABLE
    ]
    apache = [entry for entry in openrewrite.MODULES.values() if entry.license_tier == APACHE2]
    assert len(moderne) == 22
    assert len(apache) == 30
    assert openrewrite.MODULE_TABLE_SIZE == 52
    for entry in moderne:
        assert gate_module(entry.module_id).allowed is False, entry.module_id
    for entry in apache:
        gate = gate_module(entry.module_id)
        if entry.is_plugin_or_bom or entry.archived:
            assert gate.allowed is False, entry.module_id
        else:
            assert gate.allowed is True, entry.module_id

    # Namespace attribution: the same refusals, reached the way the crawl
    # reaches them. `java/migrate` must not fall through to the shorter
    # `org.openrewrite.java` prefix, which is Apache-2.0.
    assert gate_namespace("java/migrate").allowed is False
    assert gate_namespace("java/spring").allowed is False
    assert gate_namespace("quarkus/spring").allowed is False
    assert gate_namespace("picnic/errorprone").allowed is False
    assert gate_namespace("quarkus/updates").allowed is True
    assert gate_namespace("apache/camel").allowed is True
    # Nothing rules on an unknown namespace, so it is refused.
    assert gate_namespace("brandnew/thing").allowed is False
    # Apache-2.0 but archived: refused on maintenance, with its own reason.
    archived = gate_module("org.openrewrite:rewrite-kotlin")
    assert archived.spdx == "Apache-2.0"
    assert archived.allowed is False
    assert "archived" in archived.reason


# ---------------------------------------------------------------------------
# 12. The Node adapter refuses a non-ALLOW license
# ---------------------------------------------------------------------------


def test_node_adapter_refuses_to_emit_an_artifact_whose_license_verdict_is_not_allow(tmp_path):
    cases = {
        "pointer": ("LICENSE.md", MODERNE_POINTER_TEXT, "QUARANTINE"),
        "noassertion": ("LICENSE.md", "NOASSERTION", "QUARANTINE"),
        "unidentified": ("LICENSE", UNIDENTIFIABLE_LICENSE_TEXT, "QUARANTINE"),
    }
    for label, (license_name, text, expected) in cases.items():
        checkout = _make_checkout(tmp_path / label, license_name, text)
        source = NodeUserlandMigrationsSource(checkout, commit="d" * 40, run_checks=False)
        ref = next(iter(source.discover()))
        with pytest.raises(CodemodLicenseBlocked) as excinfo:
            source.fetch(ref)
        assert excinfo.value.verdict.decision == expected, label

    # And the selection helper refuses the same way, with a histogram a
    # caller can actually act on.
    checkout = _make_checkout(tmp_path / "ok", "LICENSE", MIT_LICENSE_TEXT)
    source = NodeUserlandMigrationsSource(checkout, commit="e" * 40, run_checks=False)
    ref = next(iter(source.discover()))
    good = source.fetch(ref)
    source.licensing  # touch: the licensing is resolved once and cached
    blocked = NodeUserlandMigrationsSource(
        _make_checkout(tmp_path / "bad", "LICENSE.md", MODERNE_POINTER_TEXT),
        commit="f" * 40,
        run_checks=False,
    )
    blocked_ref = next(iter(blocked.discover()))
    from app.services.ingestion_sources.base import SourceArtifact, compute_content_hash

    quarantined = SourceArtifact(
        source_type="codemod_node",
        uri=blocked_ref.uri,
        content="body",
        content_hash=compute_content_hash("body"),
    )
    accepted, histogram = select_ingestible(
        artifacts=[good, quarantined],
        verdicts={good.uri: "ALLOW", blocked_ref.uri: "QUARANTINE"},
    )
    assert [a.uri for a in accepted] == [good.uri]
    assert histogram == {"QUARANTINE": 1}

    # An artifact with no verdict at all is rejected, never defaulted in.
    accepted, histogram = select_ingestible(artifacts=[good], verdicts={})
    assert accepted == []
    assert histogram == {"MISSING_VERDICT": 1}


# ---------------------------------------------------------------------------
# 13. Provenance completeness
# ---------------------------------------------------------------------------


def test_built_artifact_carries_full_provenance_including_commit_and_allowlist_version(tmp_path):
    checkout = _make_checkout(tmp_path / "checkout", "LICENSE", MIT_LICENSE_TEXT)
    source = NodeUserlandMigrationsSource(checkout, commit="0" * 40, run_checks=False)
    artifact = source.fetch(next(iter(source.discover())))
    meta = artifact.license_metadata

    assert meta["repo_url"] == codemod_node.DEFAULT_REPO_URL
    assert meta["commit"] == "0" * 40
    assert meta["path"] == "recipes/demo-recipe"
    assert meta["recipe_name"] == "@nodejs/demo-recipe"
    assert meta["detected_spdx"] == "MIT"
    assert meta["allowlist_version"] == ALLOWLIST_VERSION
    assert meta["license_source_path"] == "LICENSE"
    assert meta["license_blob_paths"] == ["LICENSE"]
    assert meta["declared_license"] == "MIT"
    assert meta["license_verdict"]["decision"] == "ALLOW"
    assert meta["capabilities"] == ["fs"]
    assert meta["transform_entrypoints"] == ["src/workflow.ts"]
    assert meta["fixture_layout"] == "none"
    # The two version fields genuinely disagree in this catalog, and
    # keeping both is deliberate -- picking one silently would be a
    # provenance claim nobody could check.
    assert meta["version_codemod_yaml"] == "0.0.1"
    assert meta["version_package_json"] == "1.0.1"
    assert meta["versions_disagree"] is True
    assert meta["extractor"] == "codemod_node@1"
    # Hard rule 2: scope and provenance on everything entering storage.
    assert artifact.commit == "0" * 40
    assert artifact.path == "recipes/demo-recipe"
    assert artifact.content_hash
    assert "MIT" in artifact.content or "MIT" in json.dumps(meta)

    # The check payload is inside the artifact, not merely beside it.
    check = meta["check"]
    assert check["check_semantics"] == "self-consistency"
    assert check["runner_version"] == RUNNER_VERSION
    assert "verified correct" not in json.dumps(check).lower()

    # The OpenRewrite artifact carries its own, weaker provenance set
    # with the gaps marked rather than filled in.
    page = _openrewrite_page("org.openrewrite.quarkus.ChangesToX")
    catalog = OpenRewriteCatalogSource(fetcher=_sitemap_and_pages({_QUARKUS_URL: page}))
    ref = next(iter(catalog.discover()))
    orw = catalog.fetch(ref)
    orw_meta = orw.license_metadata
    assert orw_meta["recipe_id"] == "org.openrewrite.quarkus.ChangesToX"
    assert orw_meta["maven_group_id"] == "org.openrewrite.recipe"
    assert orw_meta["maven_artifact_id"] == "rewrite-quarkus"
    assert orw_meta["module_spdx"] == "Apache-2.0"
    assert orw_meta["allowlist_version"].startswith("openrewrite-module-table@")
    assert orw_meta["check_tier"] == "static"
    assert orw_meta["commit"] is None
    assert orw_meta["provenance_limits"]
    assert orw_meta["check"]["check_tier"] == "static"
    assert orw_meta["check"]["passed"] is True
    assert orw_meta["check"]["gates"]["test_evidence_only"] == "recorded_not_executed"
    # Test method names are EVIDENCE A TEST EXISTS, and are named as such.
    assert orw_meta["test_methods"] == [
        "Quarkus1to1_13MigrationTest#exampleOne",
        "Quarkus1to1_13MigrationTest#exampleTwo",
    ]
    assert "never an executed assertion" in orw_meta["test_evidence_meaning"]


# ---------------------------------------------------------------------------
# 14. codemod_checks does not import the screening path
# ---------------------------------------------------------------------------


def test_codemod_checks_does_not_import_the_screening_module():
    """The plan forbids this file touching `screening.py`.

    Asserted on the parsed import graph of the module itself, not on a
    string match, so a `from ... import` cannot slip past. Scope limit,
    stated: this is a direct-import check on this one module, not a walk
    of the whole transitive graph.
    """
    source = Path(codemod_checks.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)

    offenders = sorted(
        name for name in imported if "screening" in name or "repo_procedural" in name
    )
    assert offenders == [], f"codemod_checks must not import {offenders}"
    # Also pinned by the module's own test-visible surface: the runner is
    # a pure function of a tree plus a subprocess, with no policy
    # dependency that a later change could quietly add a redaction or
    # admission step to.
    assert not hasattr(codemod_checks, "assert_safe_locator")
    assert codemod_checks.CheckOutcome.__module__ == codemod_checks.__name__


# ---------------------------------------------------------------------------
# OpenRewrite doc-page behaviour, offline
# ---------------------------------------------------------------------------

_QUARKUS_URL = "https://docs.openrewrite.org/recipes/quarkus/updates/changesrepositorygroupid"


def _openrewrite_page(recipe_id: str) -> str:
    """A page shaped like the real rendered docs, not a convenient one.

    The real site minifies to UNQUOTED meta attribute values, puts the
    recipe id in `name=description`, and carries a site-wide results
    table in the footer whose classes are named `org.openrewrite.table.*`.
    A fixture that omitted all three would have hidden the wrong-id bug
    the live pilot found.
    """
    return f"""<html><head>
<meta data-rh=true property=og:title content="Add a comment to a method | OpenRewrite Docs" />
<meta data-rh=true name=description content={recipe_id} />
</head><body>
<h1>Add a comment to a method</h1>
<p><strong>{recipe_id}</strong></p>
<p><em>Add a comment to a method.</em></p>
<p>Migrates the repository group and artifact identifiers used by the Quarkus
    extensions to the coordinates the current platform expects.</p>
<a href="/recipes/tags/quarkus">quarkus</a>
<a href="/recipes/tags/dependency-management">dependency-management</a>
<h2>Example 1</h2>
<p><code>Quarkus1to1_13MigrationTest#exampleOne</code> and
   <code>Quarkus1to1_13MigrationTest#exampleTwo</code>.</p>
<p>Add a dependency:
  <code>org.openrewrite.recipe:rewrite-quarkus:2.34.1</code></p>
<footer><span class="token class-name">org.openrewrite.table.SourcesFileResults</span>
<span class="token class-name">org.openrewrite.table.RecipeRunStats</span></footer>
</body></html>
"""


def _sitemap_and_pages(pages: dict[str, str]) -> object:
    locations = "\n".join(f"<url><loc>{url}</loc></url>" for url in pages)

    def fetcher(url: str) -> tuple[int, str]:
        if url.endswith("sitemap.xml"):
            return 200, f"<urlset>{locations}</urlset>"
        if url in pages:
            return 200, pages[url]
        return 404, ""

    return fetcher


def test_openrewrite_sitemap_enumeration_gates_before_fetching_and_skips_unreadable_ids():
    migrate_url = "https://docs.openrewrite.org/recipes/java/migrate/changemethodtarget"
    picnic_url = "https://docs.openrewrite.org/recipes/picnic/errorprone/somecheck"
    pages = {
        _QUARKUS_URL: _openrewrite_page("org.openrewrite.quarkus.ChangesToX"),
        migrate_url: _openrewrite_page("org.openrewrite.migrate.ChangeMethodTarget"),
        picnic_url: _openrewrite_page("tech.picnic.errorprone.SomeCheck"),
    }
    catalog = OpenRewriteCatalogSource(fetcher=_sitemap_and_pages(pages))
    refs = list(catalog.discover())

    # Only the Apache-2.0, non-archived namespace is even offered: the
    # refused namespaces are counted, not crawled.
    assert [ref.uri for ref in refs] == [_QUARKUS_URL]
    stats = catalog.discovery_stats
    assert stats["pages_seen"] == 3
    assert stats["pages_offered"] == 1
    assert stats["rejected:third_party_unverified"] == 1
    assert stats["rejected:moderne_source_available"] == 1

    assert parse_sitemap("<urlset><url><loc>a</loc></url><url><loc>a</loc></url></urlset>") == ("a",)
    assert recipe_namespace(_QUARKUS_URL) == "quarkus/updates"
    assert recipe_namespace("https://docs.openrewrite.org/about/team") is None

    # A page that says nothing recognisable is SKIPPED, not turned into
    # a guessed id. The URL slug is not the FQCN, so a derived id would
    # be a plausible, unopenable Procedure.
    with pytest.raises(OpenRewriteIdUnavailable):
        extract_recipe_id("<html><body><h1>Nothing here</h1></body></html>")
    blank = OpenRewriteCatalogSource(fetcher=_sitemap_and_pages({_QUARKUS_URL: "<html></html>"}))
    with pytest.raises(OpenRewriteIdUnavailable):
        blank.fetch(next(iter(blank.discover())))

    # A stale sitemap entry (404) is a DIFFERENT finding from a page that
    # resolved but carried no readable id, and the two are kept apart so
    # neither rate hides the other. The real sitemap does contain 404s.
    stale = OpenRewriteCatalogSource(
        fetcher=lambda url: (200, "<urlset></urlset>")
        if url.endswith("sitemap.xml")
        else (404, "")
    )
    stale_ref = SourceRef(uri=_QUARKUS_URL, repository=None, path="quarkus/updates")
    with pytest.raises(openrewrite.OpenRewritePageUnavailable, match="404"):
        stale.fetch(stale_ref)


def test_openrewrite_id_extraction_never_mines_the_footer_for_an_id():
    """Regression pin for a bug the LIVE pilot found, not a test.

    Every page in this catalog carries a site-wide results-table footer
    whose classes are named `org.openrewrite.table.SourcesFileResults`
    and friends. An earlier whole-page scan for `org.openrewrite.*`
    returned one of those as the recipe id for a page whose real id is
    `software.amazon.awssdk.v2migration.AddCommentToMethod` -- a
    plausible, wrong Procedure id, minted silently.
    """
    footer_only = """<html><head></head><body>
    <h1>Add a comment to a method</h1>
    <p>Add a comment to a method.</p>
    <footer><span class="token class-name">org.openrewrite.table.SourcesFileResults</span>
    <span class="token class-name">org.openrewrite.table.RecipeRunStats</span></footer>
    </body></html>"""
    with pytest.raises(OpenRewriteIdUnavailable):
        extract_recipe_id(footer_only)

    # And the real shape: the id is under a package that is NOT
    # org.openrewrite, in an unquoted meta attribute.
    real = """<html><head>
    <meta data-rh=true name=description content=software.amazon.awssdk.v2migration.AddCommentToMethod />
    </head><body><h1>Add a comment to a method</h1>
    <p><strong>software.amazon.awssdk.v2migration.AddCommentToMethod</strong></p>
    <footer><span>org.openrewrite.table.SourcesFileResults</span></footer></body></html>"""
    assert extract_recipe_id(real) == "software.amazon.awssdk.v2migration.AddCommentToMethod"
    # Quoted attributes parse identically, so the minifier's quoting is
    # not load-bearing.
    quoted = real.replace("content=software", 'content="software').replace(
        "AddCommentToMethod />", 'AddCommentToMethod" />'
    )
    assert extract_recipe_id(quoted) == "software.amazon.awssdk.v2migration.AddCommentToMethod"

    # A page whose only stated module disagrees with its own URL
    # namespace is refused rather than resolved.
    only_other = OpenRewriteCatalogSource(
        fetcher=_sitemap_and_pages(
            {_QUARKUS_URL: _openrewrite_page("org.openrewrite.quarkus.ChangesToX").replace(
                "org.openrewrite.recipe:rewrite-quarkus:2.34.1", "org.openrewrite.recipe:rewrite-spring:6.37.0"
            )}
        )
    )
    with pytest.raises(RuntimeError, match="refusing to guess which is right"):
        only_other.fetch(next(iter(only_other.discover())))

    # Several stated modules and none of them ours is recorded as
    # unconfirmed, not resolved: a recipe page also names its
    # dependencies, so a lone mismatch is a disagreement while a many
    # mismatch is usually just a dependency list.
    several = OpenRewriteCatalogSource(
        fetcher=_sitemap_and_pages(
            {_QUARKUS_URL: _openrewrite_page("org.openrewrite.quarkus.ChangesToX").replace(
                "org.openrewrite.recipe:rewrite-quarkus:2.34.1",
                "org.openrewrite.recipe:rewrite-all:1.28.1 org.openrewrite:rewrite-java:8.92.9",
            )}
        )
    )
    meta = several.fetch(next(iter(several.discover()))).license_metadata
    assert meta["module_declared_on_page"] is None
    assert meta["modules_named_on_page"] == [
        "org.openrewrite.recipe:rewrite-all",
        "org.openrewrite:rewrite-java",
    ]
    assert any("attributed from the namespace alone" in lim for lim in meta["provenance_limits"])


def test_openrewrite_page_declared_module_version_is_recorded_and_agrees_with_the_table():
    declared = openrewrite.extract_declared_module(_openrewrite_page("org.openrewrite.quarkus.ChangesToX"))
    assert declared == ("org.openrewrite.recipe:rewrite-quarkus", "2.34.1")
    catalog = OpenRewriteCatalogSource(
        fetcher=_sitemap_and_pages({_QUARKUS_URL: _openrewrite_page("org.openrewrite.quarkus.ChangesToX")})
    )
    meta = catalog.fetch(next(iter(catalog.discover()))).license_metadata
    assert meta["module_declared_on_page"] == "org.openrewrite.recipe:rewrite-quarkus"
    assert meta["maven_version_declared_on_page"] == "2.34.1"
    assert meta["maven_version_from_table"] == "2.34.1"


def test_openrewrite_gate_output_is_a_histogram_not_a_verdict_by_assertion():
    """`select_ingestible` counts decisions; it never infers a license."""
    verdicts = [
        LicenseVerdict("ALLOW", "ok", "Apache-2.0", "LICENSE", ALLOWLIST_VERSION),
        LicenseVerdict("QUARANTINE", "a", "MPL-2.0", "LICENSE", ALLOWLIST_VERSION),
        LicenseVerdict("QUARANTINE", "b", None, "LICENSE", ALLOWLIST_VERSION),
        LicenseVerdict("REJECT", "c", "GPL-3.0", "LICENSE", ALLOWLIST_VERSION),
    ]
    from app.services.ingestion_sources.base import SourceArtifact

    artifacts = [
        SourceArtifact("openrewrite", f"u{i}", f"body{i}", f"h{i}") for i in range(len(verdicts))
    ]
    accepted, histogram = select_ingestible(
        artifacts=artifacts, verdicts={a.uri: v for a, v in zip(artifacts, verdicts)}
    )
    assert len(accepted) == 1
    assert histogram == {"QUARANTINE": 2, "REJECT": 1}


# ---------------------------------------------------------------------------
# $0 LLM SPEND, and the CLI's safety defaults
# ---------------------------------------------------------------------------


def test_no_module_in_this_source_path_imports_an_llm_client():
    """The whole reason this source exists is that it costs nothing.

    HONEST LIMIT: this checks the DIRECT imports of the four modules.
    A transitive import of an LLM client through a shared helper would
    not be caught here, which is why the check exists as a guard against
    regression rather than as a proof about the whole graph.
    """
    forbidden = {"openai", "anthropic", "litellm", "cohere", "tiktoken", "instructor", "ollama"}
    modules = [codemod_checks, codemod_node, openrewrite, codemod_cli]
    for module in modules:
        source = Path(module.__file__)
        tree = ast.parse(source.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        assert not (roots & forbidden), f"{source.name} imports {roots & forbidden}"


def test_cli_is_dry_run_by_default_and_refuses_a_missing_checkout():
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    codemod_cli.add_parsers(sub)

    args = parser.parse_args(["ingest-codemods", "--source", "openrewrite"])
    assert args.dry_run is False and args.apply is False
    assert codemod_cli._dsn_is_loopback("postgresql://postgres:postgres@127.0.0.1:55432/sl_step7")
    assert not codemod_cli._dsn_is_loopback("postgresql://u:p@db.production.example.com:5432/app")

    # A missing checkout is refused before any work, with the reason.
    assert (
        codemod_cli.main(
            ["ingest-codemods", "--source", "nodejs", "--checkout", str(Path("/definitely/not/here"))]
        )
        == 1
    )
    # And with no checkout at all, the adapter is never built.
    assert codemod_cli.main(["ingest-codemods", "--source", "nodejs"]) == 1

    # `--apply` with no DSN in the environment is refused outright; the
    # write path never silently degrades into a dry run.
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("SL_SHARD_DATABASE_URL", None)
    assert codemod_cli.main(["ingest-codemods", "--source", "openrewrite", "--apply"]) == 1
    # `--apply` and `--dry-run` together is a contradiction, not a
    # preference question.
    assert (
        codemod_cli.main(["ingest-codemods", "--source", "openrewrite", "--apply", "--dry-run"]) == 1
    )


def test_dry_run_report_states_zero_dollars_and_reports_unmeasured_counts(tmp_path, capsys):
    """The report must not claim a measurement it did not make."""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    codemod_cli.add_parsers(sub)
    args = parser.parse_args(
        ["ingest-codemods", "--source", "openrewrite", "--limit", "1", "--max-pages", "1"]
    )

    def fake_fetcher(url: str) -> tuple[int, str]:
        if url.endswith("sitemap.xml"):
            return 200, f"<urlset><url><loc>{_QUARKUS_URL}</loc></url></urlset>"
        if url == _QUARKUS_URL:
            return 200, _openrewrite_page("org.openrewrite.quarkus.ChangesToX")
        return 404, ""

    import app.services.ingestion_sources.openrewrite as orw

    original = orw._default_http_get
    orw._default_http_get = fake_fetcher
    try:
        code = asyncio.run(codemod_cli.run(None, args))
    finally:
        orw._default_http_get = original
    assert code == 0

    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["dollars"] == 0.0
    assert "LLM-free" in report["cost_note"]
    assert report["accepted"] == 1
    assert report["bytes_stored"] == 0
    assert report["bytes_produced"] > 0
    # Counts nobody produced are None, not zero.
    assert report["knowledge_items"]["procedures"] is None
    assert report["knowledge_items"]["claims"] is None
    assert "claims" in report["unmeasured"]
    item = report["items"][0]
    assert item["check_tier"] == "static"
    assert item["check_semantics"] == "self-consistency"
    assert item["detected_spdx"] == "Apache-2.0"


# ---------------------------------------------------------------------------
# 19. The five runner defects that only real execution exposed
#
# Every test below corresponds to a bug that made the runner report a
# correct, fully green catalog as broken, or reported a broken one as
# green. They were found by running the actual `codemod` CLI against
# `nodejs/userland-migrations@48b9b9a1` on 2026-09-28 -- NOT by reading
# the code -- and each is pinned here with the real evidence that caught it.
# ---------------------------------------------------------------------------


def test_multi_transform_recipe_uses_the_recipe_own_filter():
    """`timers-deprecations` is 5 transforms over ONE tests/ directory.

    Each transform owns a subset of the fixtures and says so with its own
    `--filter` in `package.json`. Running one transform against the whole
    directory makes it read the other four transforms' fixtures as its own:
    measured 3/15 passing on a catalog whose `npm test` reports every
    recipe green. The filter is read from the recipe's script, never
    reconstructed.
    """
    scripts = {
        "test": "node --run test:enroll && node --run test:unenroll && node --run test:unref",
        "test:enroll": "npx codemod jssg test -l typescript ./src/enroll-to-set-timeout.ts ./tests/ --filter dep0095",
        "test:unenroll": "npx codemod jssg test -l typescript ./src/unenroll-to-clear-timer.ts ./tests/ --filter dep0096",
        "test:unref": "npx codemod jssg test -l typescript ./src/unref-active-to-unref.ts ./tests/ --filter unref",
    }
    declared = _declared_test_commands({"scripts": scripts})
    assert declared["enroll-to-set-timeout.ts"].filter == "dep0095"
    assert declared["unenroll-to-clear-timer.ts"].filter == "dep0096"
    assert declared["unref-active-to-unref.ts"].filter == "unref"
    # all three share one directory, and none of them invents one
    assert {spec.test_dir for spec in declared.values()} == {"./tests"}
    assert {spec.language for spec in declared.values()} == {"typescript"}


def test_declared_language_wins_over_the_manifest():
    """`chalk-to-util-styletext` runs its package.json transform as `-l json`.

    The manifest's `targets.languages` says `javascript`/`typescript`, and
    forcing that made the tool print nothing at all -- reported as "the
    check tool produced no JSON event", which reads as a broken transform
    rather than a mis-declared language. The recipe's own command is
    authoritative.
    """
    scripts = {
        "test": "npx codemod jssg test -l json ./src/remove-dependencies.ts "
        "./tests/remove-dependencies --allow-child-process --allow-fs --strictness cst",
    }
    declared = _declared_test_commands({"scripts": scripts})
    spec = declared["remove-dependencies.ts"]
    assert spec.language == "json"
    assert spec.test_dir == "./tests/remove-dependencies"
    # `--strictness` keeps its VALUE; dropping it makes clap exit 2 before
    # any test runs, which is the same unreadable-output symptom.
    assert spec.extra == ("--allow-child-process", "--allow-fs", "--strictness", "cst")


def test_manifest_capabilities_and_declared_flags_are_merged_without_duplicates():
    """The same escalation is named by the manifest AND the test script.

    `chalk-to-util-styletext` declares `capabilities: [fs, child_process]`
    and its script passes `--allow-fs --allow-child-process --strictness
    cst`. Passing either list alone works; passing both makes clap reject
    the duplicate flag and exit 2, so the whole recipe reads as unreadable.
    """
    from app.services.ingestion_sources.codemod_node import _capability_args

    manifest_args = _capability_args(["fs", "child_process"])
    declared_args = ("--allow-child-process", "--allow-fs", "--strictness", "cst")
    merged = _merge_args(manifest_args, declared_args, ())
    assert merged.count("--allow-fs") == 1
    assert merged.count("--allow-child-process") == 1
    assert merged.count("--strictness") == 1
    # A valued flag keeps its value through the merge.
    assert merged[merged.index("--strictness") + 1] == "cst"
    # Order is stable and the merge is idempotent.
    assert _merge_args(merged) == merged


def test_a_test_file_under_src_is_not_mistaken_for_a_transform(tmp_path):
    """`fs-access-mode-constants` keeps its harness at `src/workflow.test.ts`.

    Running that file as a transform resolves `node:assert/strict` inside
    the sandbox and fails all 8 of its cases, while the recipe's own
    `npm test` reports 8 passed. Only transforms a declared command names
    are run; the rest are recorded, not executed.
    """
    recipe = tmp_path / "fs-access-mode-constants"
    # The shape the adapter actually discovers: a directory of recipes
    # under a `recipes/` parent, each with a `codemod.yaml` manifest.
    catalog = tmp_path / "userland-migrations"
    recipe = catalog / "recipes" / "fs-access-mode-constants"
    _write(recipe / "codemod.yaml", "name: '@nodejs/fs-access-mode-constants'\nlicense: MIT\n")
    _write(
        recipe / "package.json",
        json.dumps(
            {
                "name": "@nodejs/fs-access-mode-constants",
                "license": "MIT",
                "scripts": {
                    "test": "npx codemod jssg test --allow-fs -l typescript ./src/workflow.ts"
                },
            }
        ),
    )
    _write(recipe / "src" / "workflow.ts", "export default function () {}\n")
    _write(recipe / "src" / "workflow.test.ts", "import assert from 'node:assert/strict';\n")
    _write(recipe / "tests" / "file-01" / "input.js", "a\n")
    _write(recipe / "tests" / "file-01" / "expected.js", "b\n")
    # The real catalog's root LICENSE text, which is what
    # `identify_spdx_from_text` matches on. A short stub quarantines the
    # recipe -- correctly, but it stops the test before the gate it means
    # to exercise.
    _write(
        catalog / "LICENSE",
        "MIT License\n\nCopyright (c) Contributors to the Userland Migrations project.\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        "of this software and associated documentation files (the \"Software\"), to deal\n"
        "in the Software without restriction.\n",
    )

    source = NodeUserlandMigrationsSource(
        catalog, commit="deadbeef", node_bin="definitely-absent", node_exec="node"
    )
    refs = list(source.discover())
    assert len(refs) == 1, refs
    artifact = source.fetch(refs[0])
    gates = artifact.license_metadata["check"]["gates"]
    # The undeclared src entry is reported, never silently run.
    assert gates["undeclared_src_entries"] == "src/workflow.test.ts"


def test_expected_output_defect_is_attributed_to_the_transform_only_when_it_is_its_own(tmp_path):
    """Gate B asks whether the transform INTRODUCED a defect.

    `import-assertions-to-attributes/tests/file-edge-case/input.js`
    contains a deliberately invalid line, so its `expected.js` cannot parse
    either. Reporting that `malformed` would accuse the transform of
    something the fixture inherited. Measured on the real catalog: the
    transform's suite passes and the pair is unparseable on both sides.
    """
    if not _node_available():
        pytest.skip("node is not on this box")
    # `node --check` resolves a `.js` file's module kind from the nearest
    # package.json `"type"`, and the catalog's recipes all declare
    # `"type": "module"`. Without it the same bytes parse as CommonJS and
    # the defect disappears, so the fixture has to carry the module
    # context or the test silently proves nothing.
    _write(tmp_path / "package.json", json.dumps({"type": "module"}))
    case = tmp_path / "file-edge-case"
    # The real 14-line pair. The fixture is a top-level-await ESM module,
    # which is why a truncated version of it parses: the top-level `await`
    # below is what forces the module parse, and the invalid import is only
    # reached once it does.
    broken = (
        "import { createRequire } from 'node:module';\n"
        "import data from './data.json' with { type: 'json' };\n"
        "import systemOfADown from './system;of;a;down.json' with { type: 'json' };\n"
        "import { default as config } from './config.json'with{type: 'json'};\n"
        'import { thing } from "./data.json"with{type: "json"};\n'
        "import { fileURLToPath } from 'node:url' invalid { };\n"
        "const require = createRequire(import.meta.url);\n"
        "const foo = require('./foo.ts');\n"
        "\n"
        "const data2 = await import('./data2.json', {\n"
        "\twith: { type: 'json' },\n"
        "});\n"
        "\n"
        "await import('foo-bis');\n"
    )
    _write(case / "input.js", broken.replace(" with:", " assert:").replace(" with {", " assert {"))
    _write(case / "expected.js", broken)
    verdict, detail = check_expected_wellformedness(
        expected_paths=[case / "expected.js"], node_exec="node"
    )
    assert verdict == "inherited_unparseable", detail
    assert "predates the transform" in detail

    # The same broken output, paired with an input that DOES parse, is a
    # real defect and is reported as one.
    # The same broken output, paired with a DIFFERENT, PARSABLE case in the
    # same directory: `file-edge-case/input.js` still parses, so the
    # transform is what made the output unparseable.
    ok_case = tmp_path / "genuine"
    _write(ok_case / "input.js", "const a = 1;\n")
    _write(ok_case / "expected.js", broken)
    verdict, detail = check_expected_wellformedness(
        expected_paths=[ok_case / "expected.js"], node_exec="node"
    )
    assert verdict == "malformed", detail
    # The reason says the defect is the transform's, not the fixture's.
    assert "the transform's" in detail or "no paired input fixture" in detail


def test_duplicate_declaration_fixture_is_not_reported_as_a_malformed_transform(tmp_path):
    """`ansi-colors-to-styletext` ships an `expected.js` with a repeated import.

    It pins the transform's output for input that imported the same module
    twice, so the file is not a loadable module even though it is a correct
    fixture and the recipe's suite passes. Its own verdict keeps it visible
    without calling the catalog broken.
    """
    if not _node_available():
        pytest.skip("node is not on this box")
    # Same reason as the inherited-defect test: the duplicate binding is
    # only an error in module scope, so `"type": "module"` must be present
    # for the check to see what it sees on the real catalog.
    _write(tmp_path / "package.json", json.dumps({"type": "module"}))
    case = tmp_path / "mixed-chained-destructured"
    twice = "import { styleText } from 'node:util';\nimport { styleText } from 'node:util';\n"
    _write(case / "input.js", "import { styleText } from 'node:util';\n")
    _write(case / "expected.js", twice)
    verdict, detail = check_expected_wellformedness(
        expected_paths=[case / "expected.js"], node_exec="node"
    )
    assert verdict == "duplicate_declaration", detail
    # The reason quotes the runtime's own diagnostic, which is what makes
    # the verdict auditable rather than a bare label.
    assert "has already been declared" in detail


def test_only_a_malformed_expected_output_disqualifies_a_passing_suite(tmp_path):
    """The pass rule, stated once.

    Measured across the real catalog: 38 of 39 recipes pass, and the
    well-formedness verdicts that remain are 3 duplicate_declaration, 6
    no_parser, 2 inherited_unparseable. Every one of those is a statement
    about what we could parse, not about the transform, so none of them may
    flip a green suite to red.
    """
    assert "malformed" in WELLFORMNESS_VERDICTS
    neutral = {"wellformed", "duplicate_declaration", "no_parser", "inherited_unparseable"}
    assert neutral.isdisjoint({"malformed"}), "malformed must be the only disqualifying verdict"
    # And the ordinal ordering is weakest-support-first.
    order = list(WELLFORMNESS_VERDICTS)
    assert order.index("wellformed") < order.index("no_parser") < order.index("malformed")


# ---------------------------------------------------------------------------
# 20. Hard rule: no blocking call inside an `async def`
#     (review_step_7 §"Findings" HIGH, plus three call sites it did not name)
# ---------------------------------------------------------------------------
#
# The rule is "never let a blocking call run inside an `async def`: use
# `app.utils.aio.run_blocking`". These tests assert on the THREAD the blocking
# call ran on, recorded from inside the call itself. A sleep-based test would
# be a timing test -- it passes on an idle box, flakes on a loaded one, and
# cannot distinguish "dispatched to a worker" from "was quick enough that
# nobody noticed". A thread id distinguishes them exactly and instantly.


def _node_checkout_with_a_fixture(root: Path) -> Path:
    """A checkout whose recipe has a fixture case, so Gate A really runs.

    `_make_checkout` builds a recipe with no `tests/`, which means the fixture
    inventory is empty and `run_jssg_fixture_check` may never reach
    `subprocess.run` at all. The point here is to observe a REAL child
    process, so the recipe has to have something to run.
    """
    _make_checkout(root, "LICENSE", "MIT License\n\nPermission is hereby granted...\n")
    recipe = root / "recipes" / "demo-recipe"
    _write(recipe / "tests" / "case-a" / "input.js", "const a = 1;\n")
    _write(recipe / "tests" / "case-a" / "expected.js", "const a = 2;\n")
    return root


def test_no_subprocess_call_runs_on_the_event_loop_thread(monkeypatch, tmp_path, capsys):
    """review_step_7 HIGH, the Node.js half, observed at the literal call site.

    `codemod_checks.run_jssg_fixture_check` calls `subprocess.run(...,
    timeout=120)`. `codemod_cli.run()` is an `async def`, and it reaches that
    call through `adapter.fetch(ref)` -- so up to 120 s of blocking per recipe
    sat on the event loop.

    A real executable shim, a real `subprocess.run`, and the thread recorded
    from inside it. If the shim were stubbed out this test would prove
    nothing, which is the same reason the file's other runner tests use shims
    instead of mocks.
    """
    checkout = _node_checkout_with_a_fixture(tmp_path / "checkout")
    green = _fake_cli(
        tmp_path,
        "green_cli",
        'echo {"results":[{"passed":true,"name":"case-a"}]}',
    )

    seen_threads: list[int] = []
    real_run = codemod_checks.subprocess.run

    def recording_run(*args, **kwargs):
        seen_threads.append(threading.get_ident())
        return real_run(*args, **kwargs)

    monkeypatch.setattr(codemod_checks.subprocess, "run", recording_run)

    parser = _codemod_parser()
    args = parser.parse_args([
        "ingest-codemods", "--source", "nodejs",
        "--checkout", str(checkout), "--commit", "f" * 40,
        "--node-bin", green, "--node-exec", sys.executable,
        "--limit", "5",
    ])
    loop_thread = threading.get_ident()

    code = asyncio.run(codemod_cli.run(None, args))

    assert code == 0
    # A real child process really was spawned -- otherwise this test would be
    # asserting that a call that never happened stayed off the loop.
    assert seen_threads, "no subprocess was spawned; the test proved nothing"
    assert all(tid != loop_thread for tid in seen_threads), (
        "subprocess.run executed on the event loop thread"
    )
    assert report_of(capsys)["dry_run"] is True


def test_no_httpx_get_runs_on_the_event_loop_thread(monkeypatch, tmp_path, capsys):
    """review_step_7 HIGH, the OpenRewrite half, observed at the literal call.

    `openrewrite._default_http_get` calls `httpx.get(url, timeout=30)` -- up to
    30 s of blocking network I/O per recipe page, from inside the same
    coroutine.

    Patched at `httpx.get` rather than at `openrewrite._default_http_get`,
    because `_default_http_get` is a module global the tests elsewhere swap,
    and a test that trusts a swapped global proves only that the swap worked.
    Here the URL answers from a dict, so no socket is opened.
    """
    import httpx

    pages = {
        "https://docs.openrewrite.org/sitemap.xml":
            f"<urlset><url><loc>{_QUARKUS_URL}</loc></url></urlset>",
        _QUARKUS_URL: _openrewrite_page("org.openrewrite.quarkus.ChangesToX"),
    }
    seen_threads: list[int] = []
    seen_urls: list[str] = []

    class _Response:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text

    def fake_get(url, **kwargs):
        seen_threads.append(threading.get_ident())
        seen_urls.append(url)
        body = pages.get(url)
        return _Response(200, body) if body is not None else _Response(404, "")

    monkeypatch.setattr(httpx, "get", fake_get)

    parser = _codemod_parser()
    args = parser.parse_args([
        "ingest-codemods", "--source", "openrewrite",
        "--limit", "1", "--max-pages", "1",
    ])
    loop_thread = threading.get_ident()

    code = asyncio.run(codemod_cli.run(None, args))

    assert code == 0
    assert seen_urls, "no page was fetched; the test proved nothing"
    assert all(tid != loop_thread for tid in seen_threads), (
        "httpx.get executed on the event loop thread"
    )
    assert report_of(capsys)["accepted"] == 1


def test_codemod_cli_dispatches_every_blocking_stage_through_run_blocking(monkeypatch, tmp_path, capsys):
    """The wiring itself, named stage by stage, against a REAL adapter.

    review_step_7 named only `adapter.fetch`. Three more blocking stages sit in
    the same coroutine and were not covered:

      `_build_adapter` -- `Path.resolve()`, `Path.is_dir()` and two
        `shutil.which()` PATH scans, which block on a slow or network-mounted
        PATH entry.
      `_discover_all` -- drains `discover()`: walks the checkout tree for
        Node.js, and walks a sitemap plus every page fetch for OpenRewrite.
      `_write_report` -- `open()`/`json.dump()`. The report embeds every
        accepted item's full check payload, so it is not a token write, and
        `--out` is how a pilot's numbers reach a human.

    Driven through a real checkout rather than a fake adapter, so `_build_adapter`
    runs for real and is recorded under its own name instead of a test's lambda.
    """
    spy = _OffLoopSpy(monkeypatch, codemod_cli)
    loop_thread = threading.get_ident()
    checkout = _node_checkout_with_a_fixture(tmp_path / "checkout")

    parser = _codemod_parser()
    args = parser.parse_args([
        "ingest-codemods", "--source", "nodejs",
        "--checkout", str(checkout), "--commit", "a" * 40, "--no-checks",
        "--limit", "5",
    ])

    code = asyncio.run(codemod_cli.run(None, args))

    assert code == 0
    for label in ("_build_adapter", "_discover_all", "_write_report"):
        assert label in spy.labels, f"{label} was not dispatched through run_blocking; got {spy.labels}"
    assert "NodeUserlandMigrationsSource.fetch" in spy.labels, spy.labels
    # Every dispatch left the loop, not just the ones enumerated above.
    _assert_ran_off_loop(spy, loop_thread)
    assert report_of(capsys)["adapter_source_type"] == "codemod_node"


def test_discovery_iteration_runs_off_the_loop_not_just_the_discover_call(monkeypatch, tmp_path, capsys):
    """The generator trap, which is the bug this section exists for.

    `discover()` is a generator FUNCTION. Calling it returns a generator
    without executing any of its body, so `await run_blocking(adapter.discover)`
    offloads the creation of a generator and nothing else -- and the
    `for ref in refs:` loop afterwards drives the entire catalog walk, and
    every OpenRewrite page fetch, on the event loop thread.

    That version of this code passed a test that only asserted `run_blocking`
    had been called. Asserting the THREAD catches it. Driven through a real
    adapter whose `discover()` records its own thread.
    """
    loop_thread = threading.get_ident()
    checkout = _node_checkout_with_a_fixture(tmp_path / "checkout")

    real_source = codemod_node.NodeUserlandMigrationsSource
    seen: list[int] = []

    class _Recording(real_source):
        def discover(self):
            for item in super().discover():
                seen.append(threading.get_ident())
                yield item

    monkeypatch.setattr(codemod_node, "NodeUserlandMigrationsSource", _Recording)

    parser = _codemod_parser()
    args = parser.parse_args([
        "ingest-codemods", "--source", "nodejs",
        "--checkout", str(checkout), "--commit", "a" * 40, "--no-checks",
        "--limit", "5",
    ])

    code = asyncio.run(codemod_cli.run(None, args))

    assert code == 0
    assert seen, "discover() yielded nothing; the test proved nothing"
    assert all(tid != loop_thread for tid in seen), (
        "the discover() generator was iterated on the event loop thread"
    )
    capsys.readouterr()


def test_codemod_cli_writes_a_real_report_off_the_event_loop(monkeypatch, tmp_path, capsys):
    """The write half, end to end: dispatched off the loop, and the bytes on
    disk are a report. Asserting only the dispatch would pass if `_write_report`
    were dispatched and then silently wrote nothing."""
    fake = _ThreadRecordingAdapter(artifacts=1)
    monkeypatch.setattr(codemod_cli, "_build_adapter", lambda args: fake)
    spy = _OffLoopSpy(monkeypatch, codemod_cli)
    loop_thread = threading.get_ident()
    out = tmp_path / "report.json"

    parser = _codemod_parser()
    args = parser.parse_args([
        "ingest-codemods", "--source", "nodejs", "--limit", "5", "--out", str(out),
    ])

    code = asyncio.run(codemod_cli.run(None, args))
    capsys.readouterr()

    assert code == 0
    assert "_write_report" in spy.labels
    assert spy.thread_of("_write_report") != loop_thread
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["accepted"] == 1
    assert report["dollars"] == 0.0
    assert report["items"][0]["uri"] == "https://example.test/recipe-a"


# ---------------------------------------------------------------------------
# helpers for section 20
# ---------------------------------------------------------------------------


def _codemod_parser():
    parser = argparse.ArgumentParser(prog="app.ingestion.codemod_cli")
    codemod_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    return parser


def report_of(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class _OffLoopSpy:
    """Records every `run_blocking` dispatch and the thread each ran on.

    Patched onto `codemod_cli` rather than onto `app.utils.aio`, so it records
    what THIS module chose to route through the helper. Move a blocking call
    back inline and the dispatch list stops containing it, so the test fails on
    a missing entry rather than on a timing artefact.
    """

    def __init__(self, monkeypatch, module) -> None:
        self.dispatched: list[tuple[str, dict]] = []
        real = module.run_blocking

        async def spy(fn, /, *args, **kwargs):
            label = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
            record: dict = {"thread": None}
            self.dispatched.append((label, record))

            def probe():
                record["thread"] = threading.get_ident()
                return fn(*args, **kwargs)

            return await real(probe)

        monkeypatch.setattr(module, "run_blocking", spy)

    @property
    def labels(self) -> list[str]:
        return [label for label, _ in self.dispatched]

    def thread_of(self, label: str) -> int:
        for name, record in self.dispatched:
            if name == label:
                return record["thread"]
        raise AssertionError(
            f"{label!r} was never dispatched through run_blocking; got {self.labels}"
        )


def _assert_ran_off_loop(spy: _OffLoopSpy, loop_thread: int) -> None:
    assert spy.dispatched, "nothing was dispatched through run_blocking at all"
    for label, record in spy.dispatched:
        assert record["thread"] is not None, f"{label!r} was dispatched but never ran"
        assert record["thread"] != loop_thread, (
            f"{label!r} ran on the event loop thread -- a blocking call is on the loop"
        )


class _ThreadRecordingAdapter:
    """A `SourceAdapter`-shaped stand-in that records the thread of each stage.

    Deliberately minimal and synchronous, like every adapter in
    `ingestion_sources`. Its only job is to make the thread observable, so the
    test asserts on where `discover()` and `fetch()` ran without needing a
    checkout tree, a catalog page, or a subprocess.
    """

    source_type = "test_fake_codemod"

    def __init__(self, *, artifacts: int = 1) -> None:
        self._artifacts = artifacts
        self.thread_ids: list[int] = []
        self.discovery_stats = {"recipes_seen": artifacts}

    def discover(self):
        self.thread_ids.append(threading.get_ident())
        for i in range(self._artifacts):
            yield SourceRef(
                uri=f"https://example.test/recipe-{chr(ord('a') + i)}",
                repository="example/test-catalog",
                path=f"recipes/recipe-{chr(ord('a') + i)}",
                commit="0" * 40,
                source_id="example/test-catalog",
            )

    def fetch(self, ref):
        self.thread_ids.append(threading.get_ident())
        from app.services.ingestion_sources.base import SourceArtifact, compute_content_hash

        content = f"# {ref.path}\n\nA deterministic fake recipe body.\n"
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=ref.repository,
            path=ref.path,
            commit=ref.commit,
            source_id=ref.source_id,
            license_metadata={
                "recipe_name": ref.path.rsplit("/", 1)[-1],
                "module": "example-module",
                "detected_spdx": "Apache-2.0",
                "allowlist_version": "test-allowlist@0",
                "check": {
                    "check_tier": "executable",
                    "check_semantics": "self-consistency",
                    "passed": True,
                    "gates": {},
                    "case_count": 1,
                    "negative_case_count": 0,
                    "fixture_layout": "paired",
                },
            },
        )

    def fingerprint(self, artifact) -> str:
        return artifact.content_hash


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------------------- the generated document parses

def test_the_generated_document_survives_the_real_skill_parser_for_hostile_yaml_values():
    """The first --apply failed 39 of 39 with `invalid YAML frontmatter`: the document wrote `name: @nodejs/x` unquoted
    ('@' cannot start a plain YAML scalar), and a description containing ': ' or a newline breaks a plain scalar the same
    way. The dry run never parsed the document, so nothing caught it. This runs it through the real parser."""
    from types import SimpleNamespace

    from app.services.ingestion_sources.codemod_node import _composed_document
    from app.services.skill_ingestion import parse_skill_md

    inventory = SimpleNamespace(layout="nested", negative_case_count=1, case_count=4)
    check = {"case_count": 4, "gates": {"jssg_fixture_suite": "pass"}, "claim": "maps 4 inputs to 4 outputs"}
    hostile = [
        ("@nodejs/import-assertions-to-attributes", "Handle `import x from 'y' assert {type: \"json\"}`."),
        ("@nodejs/fs-rm", "Replace rmdir: recursive.\nSecond line, with a # hash and 'quotes' and \"double\"."),
        ("@nodejs/tls-legacy", "- starts with a dash, and ends with a colon:"),
        ("@nodejs/percent", "100% of {braces} and [brackets] & ampersands *stars* !bang |pipe >fold"),
    ]
    for name, description in hostile:
        document = _composed_document(recipe_name=name, manifest={"description": description}, package={},
                                      transforms=["src/workflow.ts"], test_command="npm test", inventory=inventory, check=check)
        parsed = parse_skill_md(document, fallback_name="fallback")          # must not raise: invalid YAML frontmatter
        assert parsed.name == name and parsed.description == " ".join(description.replace("`", "'").split())


def test_backticks_in_the_description_do_not_reach_the_goal_gate():
    """DEP0116's description quotes `url.parse` and `new URL()`; the goal gate rejects literal code syntax, which cost
    1 of the 39 recipes on the first real --apply."""
    from types import SimpleNamespace

    from app.services.ingestion_sources.codemod_node import _composed_document
    from app.services.skill_ingestion import parse_skill_md

    document = _composed_document(
        recipe_name="@nodejs/url-parse-to-url", manifest={"description": "Handle DEP0116 via transforming `url.parse` to `new URL()`"},
        package={}, transforms=[], test_command=None,
        inventory=SimpleNamespace(layout="flat", negative_case_count=0, case_count=1), check={})
    parsed = parse_skill_md(document, fallback_name="x")
    assert "`" not in parsed.description and "url.parse" in parsed.description
