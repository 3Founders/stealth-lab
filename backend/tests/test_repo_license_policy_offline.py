"""DB-free coverage for app/services/repo_license_policy.py -- the pure SPDX
allowlist verdict used by repo ingestion.

Seven focused behaviours, each one a decision the ingester actually has to
get right: an allowlisted permissive id, an id nobody ruled on, a
copyleft id the configuration cannot talk its way out of, a subfolder
LICENSE beating the repository-level id, the default fallback when
nothing nearer exists, deterministic tie-breaking when one directory
carries more than one license file, and the two pieces a caller needs to
build that index at all -- which blobs can govern anything, and what each
blob's own text identifies. No network, no DB, no fixtures
beyond in-memory trees.
"""
from __future__ import annotations

from app.services.repo_license_policy import (
    ALLOWLIST_VERSION,
    DEFAULT_ALLOWLIST,
    LicenseVerdict,
    classify_spdx,
    decide_repo_license,
    identify_spdx_from_text,
    license_paths,
    resolve_license_file,
)


def _tree(*paths: str) -> list[dict]:
    return [{"type": "blob", "path": p, "size": 1024} for p in paths]


# ---------------------------------------------------------------------
# 1. allow
# ---------------------------------------------------------------------


def test_disclosed_permissive_ids_allow():
    for spdx in sorted(DEFAULT_ALLOWLIST):
        verdict = classify_spdx(spdx)
        assert verdict.decision == "ALLOW", spdx
        assert verdict.spdx_id == spdx
        assert verdict.allowlist_version == ALLOWLIST_VERSION
        assert "disclosed permissive allowlist" in verdict.reason


def test_allowlist_is_case_and_whitespace_insensitive():
    assert classify_spdx("  mit ").decision == "ALLOW"
    assert classify_spdx("apache-2.0").spdx_id == "apache-2.0"


def test_mpl20_quarantined_by_default_and_allowed_only_on_configuration():
    default = classify_spdx("MPL-2.0")
    assert default.decision == "QUARANTINE"
    assert "not on the disclosed permissive allowlist" in default.reason
    assert "MPL-2.0" not in DEFAULT_ALLOWLIST

    configured = classify_spdx("MPL-2.0", allow=DEFAULT_ALLOWLIST | {"MPL-2.0"})
    assert configured.decision == "ALLOW"
    assert "caller-configured" in configured.reason
    assert configured.allowlist_version == f"{ALLOWLIST_VERSION}+caller"


# ---------------------------------------------------------------------
# 2. unknown -> quarantine
# ---------------------------------------------------------------------


def test_unknown_license_quarantines():
    verdict = classify_spdx("LicenseRef-Proprietary-Internal")
    assert verdict.decision == "QUARANTINE"
    assert "not on the disclosed permissive allowlist" in verdict.reason


def test_noassertion_and_missing_quarantine_with_distinct_reasons():
    unidentified = classify_spdx("NOASSERTION")
    assert unidentified.decision == "QUARANTINE"
    assert "NOASSERTION" in unidentified.reason

    for empty in (None, "", "   "):
        verdict = classify_spdx(empty)
        assert verdict.decision == "QUARANTINE", empty
        assert verdict.spdx_id is None
        assert "no license id available" in verdict.reason

    declared_none = classify_spdx("NONE")
    assert declared_none.decision == "QUARANTINE"
    assert declared_none.spdx_id == "NONE"


def test_share_alike_and_source_available_ids_quarantine():
    for spdx in ("BUSL-1.1", "Elastic-2.0", "CC-BY-SA-4.0", "OSL-3.0"):
        assert classify_spdx(spdx).decision == "QUARANTINE", spdx


# ---------------------------------------------------------------------
# 3. copyleft -> reject, and the floor is not configurable
# ---------------------------------------------------------------------


def test_copyleft_and_non_commercial_families_reject():
    for spdx in (
        "GPL-2.0", "GPL-3.0-only", "GPL-3.0-or-later",
        "AGPL-3.0", "LGPL-2.1", "LGPL-3.0-or-later",
        "SSPL-1.0", "CC-BY-NC-4.0", "CC-BY-ND-4.0",
    ):
        verdict = classify_spdx(spdx)
        assert verdict.decision == "REJECT", spdx
        assert "reject floor" in verdict.reason


def test_reject_floor_survives_a_caller_allowlist():
    verdict = classify_spdx(
        "GPL-3.0-only", allow={"MIT", "GPL-3.0-only", "MPL-2.0"},
    )
    assert verdict.decision == "REJECT"
    assert "configuration cannot allow" in verdict.reason
    assert classify_spdx("MIT", allow={"MIT", "GPL-3.0-only"}).decision == "ALLOW"


# ---------------------------------------------------------------------
# 4. subfolder LICENSE overrides the repository SPDX id
# ---------------------------------------------------------------------


def test_subfolder_license_overrides_repo_spdx():
    tree = _tree("LICENSE", "vendor/thing/LICENSE", "vendor/thing/mod.py")
    verdict = decide_repo_license(
        path="vendor/thing/mod.py",
        tree=tree,
        repo_spdx="MIT",
        license_spdx_by_path={"vendor/thing/LICENSE": "GPL-3.0-only"},
    )
    assert verdict.decision == "REJECT"
    assert verdict.spdx_id == "GPL-3.0-only"
    assert verdict.source_path == "vendor/thing/LICENSE"


def test_deeper_license_wins_and_unrelated_branch_is_ignored():
    tree = _tree("LICENSE", "pkg/LICENSE", "pkg/deep/COPYING", "pkg/deep/mod.py", "other/LICENSE")
    resolved = resolve_license_file("pkg/deep/mod.py", tree)
    assert resolved == "pkg/deep/COPYING"

    verdict = decide_repo_license(
        path="pkg/mod.py",
        tree=tree,
        repo_spdx="MIT",
        license_spdx_by_path={"pkg/LICENSE": "BUSL-1.1", "other/LICENSE": "AGPL-3.0"},
    )
    assert verdict.decision == "QUARANTINE"
    assert verdict.spdx_id == "BUSL-1.1"
    assert verdict.source_path == "pkg/LICENSE"


def test_unidentified_subfolder_license_does_not_fall_back_to_repo_spdx():
    tree = _tree("LICENSE", "vendor/thing/LICENSE", "vendor/thing/mod.py")
    verdict = decide_repo_license(
        path="vendor/thing/mod.py", tree=tree, repo_spdx="MIT",
        license_spdx_by_path={"LICENSE": "MIT"},
    )
    assert verdict.decision == "QUARANTINE"
    assert verdict.spdx_id is None
    assert verdict.source_path == "vendor/thing/LICENSE"
    assert "no license id available from vendor/thing/LICENSE" in verdict.reason


# ---------------------------------------------------------------------
# 5. default fallback
# ---------------------------------------------------------------------


def test_default_fallback_to_repo_spdx_when_no_nearer_license():
    tree = _tree("README.md", "src/app.py", "src/nested/util.py")
    for path in ("src/app.py", "src/nested/util.py", None):
        verdict = decide_repo_license(path=path, tree=tree, repo_spdx="Apache-2.0")
        assert verdict.decision == "ALLOW", path
        assert verdict.spdx_id == "Apache-2.0"
        assert verdict.source_path is None


def test_default_fallback_quarantines_a_repo_with_no_license_at_all():
    tree = _tree("README.md", "src/app.py")
    verdict = decide_repo_license(path="src/app.py", tree=tree, repo_spdx=None)
    assert verdict.decision == "QUARANTINE"
    assert verdict.source_path is None
    assert "no license id available" in verdict.reason


def test_repo_license_file_resolves_a_top_level_license_path():
    tree = _tree("LICENSE", "src/app.py")
    assert resolve_license_file("LICENSE", tree) == "LICENSE"
    assert resolve_license_file("src/app.py", tree) == "LICENSE"


# ---------------------------------------------------------------------
# 6. deterministic ordering
# ---------------------------------------------------------------------


def test_ties_in_one_directory_break_deterministically():
    tree = _tree("COPYING", "LICENSE-MIT", "LICENSE.txt", "LICENSE", "src/app.py")
    expected = "LICENSE"
    for _ in range(5):
        assert resolve_license_file("src/app.py", tree) == expected
    for _ in range(5):
        assert resolve_license_file("src/app.py", list(reversed(tree))) == expected
    assert resolve_license_file("src/app.py", _tree("COPYING", "LICENSE-MIT", "src/app.py")) == "LICENSE-MIT"
    assert resolve_license_file("src/app.py", _tree("COPYING", "src/app.py")) == "COPYING"


def test_license_bearing_tree_entries_are_ignored():
    tree = [
        {"type": "tree", "path": "LICENSE", "sha": "abc"},
        {"type": "blob", "path": "src/LICENSE", "size": 10},
        {"type": "blob", "path": "src/app.py", "size": 10},
        "not-an-entry",
        {"type": "blob", "path": "", "size": 1},
    ]
    assert resolve_license_file("src/app.py", tree) == "src/LICENSE"


def test_path_normalization_handles_leading_and_backslash_separators():
    tree = _tree("vendor/thing/LICENSE", "vendor/thing/mod.py")
    for path in ("./vendor/thing/mod.py", "/vendor/thing/mod.py", "vendor\\thing\\mod.py"):
        assert resolve_license_file(path, tree) == "vendor/thing/LICENSE", path


def test_verdict_as_dict_carries_the_full_audit_payload():
    verdict = decide_repo_license(
        path="pkg/mod.py", tree=_tree("pkg/LICENSE"), repo_spdx="MIT",
        license_spdx_by_path={"pkg/LICENSE": "ISC"},
    )
    assert isinstance(verdict, LicenseVerdict)
    assert verdict.as_dict() == {
        "decision": "ALLOW",
        "reason": verdict.reason,
        "spdx_id": "ISC",
        "source_path": "pkg/LICENSE",
        "allowlist_version": ALLOWLIST_VERSION,
    }


def test_empty_tree_and_empty_input_are_quarantine_not_allow():
    assert decide_repo_license(path="src/app.py", tree=[], repo_spdx=None).decision == "QUARANTINE"
    assert decide_repo_license().decision == "QUARANTINE"
    assert resolve_license_file("src/app.py", None) is None
    assert resolve_license_file(None, _tree("LICENSE")) is None


# ---------------------------------------------------------------------
# 7. the caller's index: license_paths + identify_spdx_from_text
# ---------------------------------------------------------------------


def test_license_paths_is_the_exact_resolvable_set_and_is_deterministic():
    tree = _tree("COPYING", "LICENSE-MIT", "LICENSE.txt", "LICENSE", "src/app.py",
                 "src/COPYING", "vendor/thing/LICENSE.md", "docs/x.LICENSE")
    expected = ["COPYING", "LICENSE", "LICENSE-MIT", "LICENSE.txt", "src/COPYING",
                "vendor/thing/LICENSE.md"]
    assert license_paths(tree) == expected
    for _ in range(5):
        assert license_paths(list(reversed(tree))) == expected
    assert license_paths([{"type": "tree", "path": "LICENSE"}, "junk", {"path": ""}]) == []
    assert license_paths(None) == []


def test_a_blob_named_license_only_as_a_suffix_is_not_a_license_blob():
    """`docs/x.LICENSE` is a filename the stem rule (the text before the first dot) does
    not recognise, so the index and the resolver agree to ignore it rather than one of them
    seeing a governing blob the other cannot resolve."""
    tree = _tree("docs/x.LICENSE", "docs/app.py", "LICENSE")
    assert license_paths(tree) == ["LICENSE"]
    assert resolve_license_file("docs/app.py", tree) == "LICENSE"


def test_every_blob_the_resolver_can_return_is_a_key_the_index_will_have():
    tree = _tree("LICENSE", "pkg/LICENSE", "pkg/deep/COPYING", "src/app.py", "src/nested/util.py")
    for path in tree:
        assert license_paths(tree) == sorted(set(license_paths(tree)))
    for target in ("LICENSE", "pkg/LICENSE", "pkg/deep/COPYING", "src/app.py", "src/nested/util.py"):
        resolved = resolve_license_file(target, tree)
        assert resolved is None or resolved in license_paths(tree)


def test_disclosed_and_copyleft_headers_identify_to_their_own_ids():
    assert identify_spdx_from_text("MIT License\n\nPermission is hereby granted, free of charge") == "MIT"
    assert identify_spdx_from_text("Expat License. Permission is hereby granted, free of charge") == "MIT"
    assert identify_spdx_from_text("Apache License\nVersion 2.0, January 2004") == "Apache-2.0"
    assert identify_spdx_from_text("Mozilla Public License Version 2.0") == "MPL-2.0"
    assert identify_spdx_from_text("This is free and unencumbered software released into the public domain") == "Unlicense"
    assert identify_spdx_from_text("Permission to use, copy, modify, and/or distribute this software") == "ISC"
    assert identify_spdx_from_text("Server Side Public License, Version 1") == "SSPL-1.0"
    assert identify_spdx_from_text("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007") == "GPL-3.0-only"
    assert identify_spdx_from_text("GNU LESSER GENERAL PUBLIC LICENSE\nVersion 2.1, February 1999") == "LGPL-2.1-only"
    assert identify_spdx_from_text("GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3") == "AGPL-3.0-only"


def test_gnu_families_fall_back_to_a_family_token_when_the_version_is_unreadable():
    assert identify_spdx_from_text("GNU GENERAL PUBLIC LICENSE") == "GPL"
    assert identify_spdx_from_text("gnu lesser general public license version 9") == "LGPL"
    for family, header in (("GPL", "GNU GENERAL PUBLIC LICENSE"),
                           ("LGPL", "GNU LESSER GENERAL PUBLIC LICENSE"),
                           ("AGPL", "GNU AFFERO GENERAL PUBLIC LICENSE")):
        verdict = classify_spdx(identify_spdx_from_text(header))
        assert verdict.decision == "REJECT" and family in verdict.reason, family


def test_a_share_alike_creative_commons_text_reports_at_the_family_it_also_names():
    share_alike = ("Creative Commons Attribution-ShareAlike 4.0 International Public License. "
                   "The licensor grants a worldwide, royalty-free, non-sublicensable license "
                   "under the Attribution-NonCommercial 4.0 terms described in the appendix.")
    assert identify_spdx_from_text(share_alike) == "CC-BY-NC"
    assert classify_spdx(identify_spdx_from_text(share_alike)).decision == "REJECT"


def test_creative_commons_shapes_land_on_the_non_commercial_reject_floor():
    assert identify_spdx_from_text("Creative Commons Attribution-NonCommercial 4.0") == "CC-BY-NC"
    assert identify_spdx_from_text("Creative Commons Attribution-NonCommercial-ShareAlike 4.0") == "CC-BY-NC"
    assert identify_spdx_from_text("Creative Commons Attribution-NonCommercial-NoDerivatives 4.0") == "CC-BY-NC-ND"
    assert identify_spdx_from_text("Creative Commons Attribution-NoDerivatives 4.0") == "CC-BY-ND"
    assert classify_spdx(identify_spdx_from_text("Creative Commons Attribution-NonCommercial 4.0")).decision == "REJECT"
    assert identify_spdx_from_text("Creative Commons Zero v1.0 Universal") == "CC0-1.0"
    assert classify_spdx("CC0-1.0").decision == "ALLOW"


def test_identified_text_never_admits_a_license_outside_the_policy():
    for text in ("Apache License\nVersion 1.1", "Mozilla Public License\nVersion 1.1",
                 "redistribution and use in source and binary forms", "do not distribute",
                 "Copyright (c) 2024 Acme. All rights reserved."):
        assert identify_spdx_from_text(text) is None, text
    for empty in (None, "", "   \n\t "):
        assert identify_spdx_from_text(empty) is None


def test_bsd_clause_count_is_read_from_the_text_not_guessed():
    two = "Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met: 1. Redistributions of source code must retain the above copyright notice."
    three = two + " 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software."
    assert identify_spdx_from_text(two) == "BSD-2-Clause"
    assert identify_spdx_from_text(three) == "BSD-3-Clause"
    assert classify_spdx(identify_spdx_from_text(two)).decision == "ALLOW"
    assert classify_spdx(identify_spdx_from_text(three)).decision == "ALLOW"


def test_identification_is_a_pure_function_of_the_text():
    for text in ("MIT License\n\nPermission is hereby granted, free of charge",
                 "GNU GENERAL PUBLIC LICENSE\nVersion 3", "nonsense"):
        assert identify_spdx_from_text(text) == identify_spdx_from_text(text)
    assert identify_spdx_from_text("  \n MIT   License \n\n Permission is hereby granted, free of charge ") == "MIT"
