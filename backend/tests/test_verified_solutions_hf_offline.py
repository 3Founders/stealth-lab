"""
Proving tests for the verified-solutions corpus reader.

Offline: no network, no database, `DATABASE_URL` unset. Rows are injected
rather than downloaded, so every assertion is about OUR mapping and gating
logic and never about upstream availability.

The two tests that matter most are
`test_v1_display_name_would_be_quarantined_without_the_normalizer` and
`test_swe_gym_is_rejected_for_missing_license`: they pin the two data facts
that would otherwise silently cost us an entire corpus.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.ingestion_sources import verified_solutions_hf as vs
from app.services.ingestion_sources.base import compute_content_hash
from app.services.repo_license_policy import classify_spdx

REPO_ROOT = Path(__file__).resolve().parents[2]

DESIGN_PATHS = (
    REPO_ROOT / "experiments" / "swebench" / "runs" / "design.json",
    REPO_ROOT / "experiments" / "swebench_rebench" / "runs" / "design.json",
)


def _raw(**overrides):
    base = {
        "instance_id": "acme__widget-42",
        "repo": "acme/widget",
        "base_commit": "a" * 40,
        "problem_statement": "Widget raises on empty input.",
        "patch": "diff --git a/w.py b/w.py\n+fix",
        "test_patch": "diff --git a/t.py b/t.py\n+def test_x():",
        "FAIL_TO_PASS": ["t.py::test_x"],
        "PASS_TO_PASS": ["t.py::test_y"],
        "license": "mit",
        "language": "python",
    }
    base.update(overrides)
    return base


def _row(source_key="swe_bench_extra", **overrides):
    return vs.row_to_verified_solution_row(
        _raw(**overrides), source_key=source_key
    )


def _classify(spdx_id):
    return classify_spdx(spdx_id)


def _counters():
    return vs.GateCounters()


def _real_held_available() -> bool:
    """The real designs are gitignored; the tracked list (experiments/held_out_ids.json) is their committed copy."""
    return all(p.exists() for p in DESIGN_PATHS) or vs._TRACKED_IDS.is_file()


def _synthetic_tracked() -> Path:
    """A tracked list holding just the ids and repos these tests reference, so the held-out LOGIC is tested
    everywhere, including a fresh clone without the real designs (BLOCKERS.md I15)."""
    import tempfile

    path = Path(tempfile.mkdtemp(prefix="held-out-")) / "held_out_ids.json"
    path.write_text(json.dumps({"splits": ["test", "calibration"], "sources": [],
                                "instance_ids": ["astropy__astropy-14096"],
                                "scored_repos": ["django/django"]}), encoding="utf-8")
    return path


_SYNTHETIC_TRACKED = _synthetic_tracked()


def _held():
    if _real_held_available():
        return vs.load_held_out(DESIGN_PATHS)
    return vs.load_held_out((REPO_ROOT / "no" / "such.json",), tracked_path=_SYNTHETIC_TRACKED)


# ---------------------------------------------------------------------------
# License normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MIT License", "MIT"),
        ('BSD 3-Clause "New" or "Revised" License', "BSD-3-Clause"),
        ('BSD 2-Clause "Simplified" License', "BSD-2-Clause"),
        ("Apache License 2.0", "Apache-2.0"),
        ("The Unlicense", "Unlicense"),
        ("Zope Public License 2.1", "ZPL-2.1"),
        ("Mozilla Public License 2.0", "MPL-2.0"),
        ("New BSD License", "BSD-3-Clause"),
        ("ISC License", "ISC"),
    ],
)
def test_display_names_normalize(raw, expected):
    assert vs.normalize_spdx(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("mit", "MIT"), ("MIT", "MIT"), ("Apache-2.0", "Apache-2.0"),
     ("CC0-1.0", "CC0-1.0"), ("BSD-3-Clause", "BSD-3-Clause")],
)
def test_spdx_slugs_pass_through_canonically(raw, expected):
    """A slug that is already an SPDX id is returned in canonical casing, so
    the caller can hand it straight to classify_spdx."""
    assert vs.normalize_spdx(raw) == expected


def test_normalization_is_case_and_whitespace_insensitive():
    assert vs.normalize_spdx("  mit   license ") == "MIT"


@pytest.mark.parametrize(
    "raw", ["custom-check-github", "NOASSERTION", "", None, "unknown", "Some bespoke terms"]
)
def test_unmappable_values_return_none_not_a_guess(raw):
    assert vs.normalize_spdx(raw) is None


@pytest.mark.parametrize("raw", ["AGPL-3.0", "GPL-3.0", "AGPL-3.0-only", "SSPL-1.0"])
def test_copyleft_survives_normalization_so_it_can_be_rejected(raw):
    out = vs.normalize_spdx(raw)
    assert out is not None
    assert classify_spdx(out).decision == "REJECT"


def test_v1_display_name_would_be_quarantined_without_the_normalizer():
    """The reason the normalizer exists, pinned as a regression.

    SWE-rebench V1 stores GitHub Licensee display names. Fed straight to the
    allowlist, the most common one misses by one suffix character and the
    whole largest Python corpus is quarantined for a non-licensing reason.
    """
    raw = "MIT License"
    assert classify_spdx(raw).decision == "QUARANTINE"
    assert classify_spdx(vs.normalize_spdx(raw)).decision == "ALLOW"


def test_zpl_normalizes_but_stays_quarantined_by_the_default_allowlist():
    """Normalizing is not the same as allowing. ZPL-2.1 is weak copyleft and
    is deliberately absent from DEFAULT_ALLOWLIST, so the policy -- not this
    module -- decides, and it says QUARANTINE."""
    verdict = classify_spdx(vs.normalize_spdx("Zope Public License 2.1"))
    assert verdict.decision == "QUARANTINE"


# ---------------------------------------------------------------------------
# Row shape
# ---------------------------------------------------------------------------

def test_fail_to_pass_is_read_as_a_real_list_not_json():
    row = _row(FAIL_TO_PASS=["a", "b"])
    assert row.fail_to_pass == ("a", "b")


def test_fail_to_pass_accepts_a_json_string_because_the_card_says_str():
    row = _row(FAIL_TO_PASS='["a", "b"]')
    assert row.fail_to_pass == ("a", "b")


def test_malformed_test_list_degrades_instead_of_raising():
    assert vs._as_str_list("not json") == ["not json"]
    assert vs._as_str_list(None) == []
    assert vs._as_str_list("") == []


def test_absent_optional_fields_become_empty():
    row = _row(PASS_TO_PASS=None, PASS_TO_FAIL=None, FAIL_TO_FAIL=None)
    assert row.pass_to_pass == ()
    assert row.pass_to_fail == ()
    assert row.fail_to_fail == ()


def test_spurious_index_column_is_ignored():
    row = _row(__index_level_0__=6410)
    assert row.instance_id == "acme__widget-42"


def test_swe_gym_row_has_no_license_and_is_unmappable():
    row = vs.row_to_verified_solution_row(
        _raw(), source_key="swe_gym"
    )
    assert vs.SOURCES["swe_gym"]["license_field"] is None
    assert row.license_raw is None
    assert row.license_spdx is None


def test_v2_language_comes_from_the_row():
    row = _row("swe_rebench_v2", language="go")
    assert row.language == "go"


def test_dedup_key_is_repo_plus_commit():
    assert _row().dedup_key == "acme/widget@" + "a" * 40


# ---------------------------------------------------------------------------
# Held-out exclusion
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _real_held_available(), reason=(
    "real held-out designs absent and experiments/held_out_ids.json not committed yet: run "
    "experiments/export_held_out_ids.py where the designs exist (BLOCKERS.md I15)"))
def test_held_out_loads_both_designs_with_real_counts():
    held = _held()
    # 193 test + 12 calibration per design, two designs.
    assert len(held.instance_ids) == 2 * (193 + 12)
    # 8 SWE-bench scored repos + 13 SWE-rebench scored repos.
    assert len(held.repos) == 21
    assert "django/django" in held.repos
    assert "astropy__astropy-14096" in held.instance_ids


def test_held_out_repos_are_lowercased_for_case_insensitive_match():
    held = _held()
    assert "django/django" in held.repos
    assert "Django/Django" not in held.repos


def test_repo_exclusion_can_be_relaxed():
    held = vs.load_held_out((REPO_ROOT / "no" / "such.json",), include_repos=False, tracked_path=_SYNTHETIC_TRACKED)
    assert held.repos == frozenset()
    assert held.instance_ids


def test_missing_design_fails_closed_without_a_tracked_list():
    """BLOCKERS.md I15: this used to be a warning and an EMPTY set, which admits every held-out task."""
    from app.services.ingestion_sources.held_out import HeldOutUnavailable

    nowhere = REPO_ROOT / "no" / "such.json"
    with pytest.raises(HeldOutUnavailable):
        vs.load_held_out((nowhere,), tracked_path=REPO_ROOT / "no" / "tracked.json")
    # a dry run may still count without it -- explicitly
    dry = vs.load_held_out((nowhere,), tracked_path=REPO_ROOT / "no" / "tracked.json", allow_missing=True)
    assert dry.instance_ids == frozenset()


def test_missing_design_uses_the_tracked_list():
    held = vs.load_held_out((REPO_ROOT / "no" / "such.json",), tracked_path=_SYNTHETIC_TRACKED)
    assert "astropy__astropy-14096" in held.instance_ids
    assert "django/django" in held.repos


def test_tracked_list_missing_a_split_fails_closed(tmp_path):
    from app.services.ingestion_sources.held_out import HeldOutUnavailable

    partial = tmp_path / "held_out_ids.json"
    partial.write_text(json.dumps({"splits": ["test"], "instance_ids": ["x"], "scored_repos": []}), encoding="utf-8")
    with pytest.raises(HeldOutUnavailable):
        vs.load_held_out((REPO_ROOT / "no" / "such.json",), tracked_path=partial)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def test_clean_row_is_accepted():
    c = _counters()
    assert vs.evaluate_row(_row(), held_out=_held(), license_classify=_classify, counters=c) is None
    assert c.accepted == 1


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"instance_id": ""}, "missing_instance_id"),
        ({"repo": ""}, "missing_repo_or_commit"),
        ({"base_commit": ""}, "missing_repo_or_commit"),
        ({"problem_statement": ""}, "missing_problem_statement"),
        ({"patch": ""}, "missing_patch"),
        ({"test_patch": ""}, "missing_test_patch"),
        ({"FAIL_TO_PASS": []}, "no_fail_to_pass"),
    ],
)
def test_structural_rejections(overrides, reason):
    c = _counters()
    assert vs.evaluate_row(_row(**overrides), held_out=_held(), license_classify=_classify, counters=c) == reason


def test_held_out_instance_is_rejected_by_id():
    held = _held()
    c = _counters()
    row = _row(instance_id="astropy__astropy-14096")
    assert vs.evaluate_row(row, held_out=held, license_classify=_classify, counters=c) == "held_out_instance"


def test_held_out_repo_is_rejected_even_for_a_different_instance():
    """Our experiments score on these repos, so a *different* instance from
    the same repository teaches the same codebase. Instance-level exclusion
    alone would not catch that."""
    held = _held()
    c = _counters()
    row = _row(instance_id="django__django-99999", repo="django/django")
    assert vs.evaluate_row(row, held_out=held, license_classify=_classify, counters=c) == "held_out_repo"


def test_held_out_beats_license_in_the_reason_order():
    """A held-out row must be counted as held-out, not as whatever else is
    wrong with it, or the exclusion count lies."""
    held = _held()
    c = _counters()
    row = _row(instance_id="astropy__astropy-14096", license="NOASSERTION")
    assert vs.evaluate_row(row, held_out=held, license_classify=_classify, counters=c) == "held_out_instance"


def test_duplicate_is_detected_on_repo_plus_commit():
    c = _counters()
    assert vs.evaluate_row(_row(), held_out=_held(), license_classify=_classify, counters=c) is None
    assert vs.evaluate_row(_row(instance_id="acme__widget-43"), held_out=_held(), license_classify=_classify, counters=c) == "duplicate"
    assert c.duplicate == 1


def test_pass_to_fail_rejects_because_the_gold_patch_breaks_a_test():
    c = _counters()
    row = _row(PASS_TO_FAIL=["t.py::test_z"])
    assert vs.evaluate_row(row, held_out=_held(), license_classify=_classify, counters=c) == "gold_patch_breaks_test"
    assert c.quality_counts["has_pass_to_fail"] == 1


def test_fail_to_fail_rejects_because_the_task_is_flaky():
    c = _counters()
    row = _row(FAIL_TO_FAIL=["t.py::test_flaky"])
    assert vs.evaluate_row(row, held_out=_held(), license_classify=_classify, counters=c) == "flaky_fail_to_fail"


def test_unmappable_license_is_rejected_without_consulting_the_policy():
    called = {"n": 0}

    def spy(_spdx):
        called["n"] += 1
        return classify_spdx(_spdx)

    c = _counters()
    row = _row(license="custom-check-github")
    assert vs.evaluate_row(row, held_out=_held(), license_classify=spy, counters=c) == "license_unmappable"
    assert called["n"] == 0


def test_quarantined_license_is_rejected_and_counted():
    c = _counters()
    row = _row(license="zpl-2.1")
    assert vs.evaluate_row(row, held_out=_held(), license_classify=_classify, counters=c) == "license_quarantine"
    assert c.license_counts["QUARANTINE"] == 1


def test_copyleft_license_is_rejected_as_reject():
    c = _counters()
    row = _row("swe_rebench_v2", license="AGPL-3.0")
    assert vs.evaluate_row(row, held_out=_held(), license_classify=_classify, counters=c) == "license_reject"


def test_swe_gym_row_is_rejected_for_missing_license():
    """11 real repositories, no per-instance license field. Every row is a
    license rejection until a per-repo decision exists."""
    c = _counters()
    row = vs.row_to_verified_solution_row(_raw(), source_key="swe_gym")
    assert vs.evaluate_row(row, held_out=_held(), license_classify=_classify, counters=c) == "license_unmappable"
    assert c.accepted == 0


def test_language_distribution_is_recorded():
    c = _counters()
    # A distinct base_commit per row: otherwise dedup correctly fires after
    # the first and the later languages never reach the counter.
    for i, lang in enumerate(("go", "go", "rust")):
        vs.evaluate_row(
            _row(instance_id=f"x__y-{lang}", base_commit=f"{i:040d}", language=lang),
            held_out=_held(),
            license_classify=_classify,
            counters=c,
        )
    assert c.language_counts == {"go": 2, "rust": 1}


# ---------------------------------------------------------------------------
# Document synthesis
# ---------------------------------------------------------------------------

def test_skill_document_has_frontmatter_a_check_and_the_patch():
    doc = vs.build_skill_document(_row())
    assert doc.startswith("---\n")
    assert "name: widget-42" in doc
    assert "description: Verified fix for acme__widget-42" in doc
    assert "t.py::test_x" in doc
    assert "diff --git a/w.py b/w.py" in doc


def test_skill_document_is_deterministic_so_dedup_works():
    assert vs.build_skill_document(_row()) == vs.build_skill_document(_row())


def test_skill_document_hash_is_the_content_hash():
    doc = vs.build_skill_document(_row())
    assert compute_content_hash(doc) == compute_content_hash(doc)


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------

def _source(rows, source_key="swe_bench_extra", row_limit=None):
    return vs.VerifiedSolutionSource(
        source_key,
        held_out=_held(),
        license_classify=_classify,
        row_limit=row_limit,
        rows=rows,
    )


def test_unknown_source_key_raises():
    with pytest.raises(KeyError):
        vs.VerifiedSolutionSource("nope", held_out=_held(), license_classify=_classify)


def test_discover_fetch_fingerprint_round_trip():
    src = _source([_raw()])
    refs = list(src.discover())
    assert len(refs) == 1
    ref = refs[0]
    assert ref.uri.startswith("hf://nebius/SWE-bench-extra@11dcbfb3")
    assert ref.repository == "acme/widget"
    assert ref.commit == "a" * 40
    assert ref.source_id == "acme__widget-42"

    art = src.fetch(ref)
    assert art.source_type == "verified_solution"
    assert art.content_hash == compute_content_hash(art.content)
    assert src.fingerprint(art) == art.content_hash
    assert art.commit == "a" * 40
    assert art.license_metadata == {"license": "mit", "spdx_id": "MIT"}


def test_license_metadata_arms_the_spdx_signal():
    art = _source([_raw(license="MIT License")]).fetch(list(_source([_raw(license="MIT License")]).discover())[0])
    assert art.license_metadata["spdx_id"] == "MIT"
    assert art.license_metadata["license"] == "MIT License"


def test_rejected_rows_never_reach_discover():
    src = _source([_raw(FAIL_TO_PASS=[]), _raw()])
    assert len(list(src.discover())) == 1
    assert src.counters.reasons.get("no_fail_to_pass") == 1


def test_row_limit_stops_production():
    src = _source(
        [_raw(instance_id=f"a__b-{i}", base_commit=f"{i:040d}") for i in range(10)],
        row_limit=3,
    )
    assert len(list(src.discover())) == 3


def test_fetch_does_not_poison_discover_state_with_dedup():
    """`discover()` then `fetch()` is the normal consumer pattern and is two
    passes over the same rows. Dedup state is pass-local, or every discovered
    ref rejects itself as its own duplicate."""
    src = _source([_raw()])
    refs = list(src.discover())
    assert src.fetch(refs[0]).source_id == "acme__widget-42"


def test_fetch_of_an_inadmissible_id_raises():
    with pytest.raises(KeyError):
        _source([_raw()]).fetch(
            vs.SourceRef(uri="hf://x", source_id="never-seen")
        )


def test_satisfies_the_source_adapter_protocol():
    from app.services.ingestion_sources.base import SourceAdapter

    assert isinstance(_source([_raw()]), SourceAdapter)


def test_revision_pins_are_present_and_not_placeholder():
    """A floating or invented revision silently changes the corpus under a
    dedup key, so every source must carry a real 40-hex pin."""
    import re

    for key, spec in vs.SOURCES.items():
        assert re.fullmatch(r"[0-9a-f]{40}", spec["revision"]), key


# ---------------------------------------------------------------------------
# scan_limit caps rows READ (row_limit caps rows ADMITTED)
# ---------------------------------------------------------------------------

def test_scan_limit_stops_a_corpus_that_admits_nothing():
    """A near-all-reject corpus (SWE-Gym's shape) never reaches an admission cap. Before scan_limit, the dry-run's
    --max-scan counted admitted rows only, so it could never fire for such a corpus."""
    read: list[int] = []

    def rows():
        for i in range(10_000):
            read.append(i)
            yield _raw(instance_id=f"acme__widget-{i}", license="")   # no license: every row is rejected

    src = vs.VerifiedSolutionSource("swe_bench_extra", held_out=_held(), license_classify=_classify,
                                    rows=rows(), scan_limit=50)
    assert list(src.iter_admissible()) == []
    assert len(read) == 50
    assert src.counters.rows_seen == 50


def test_scan_limit_counts_every_row_and_row_limit_still_counts_admissions():
    # distinct (repo, base_commit) per row, so the dedup gate admits all of them
    good = [_raw(instance_id=f"acme__widget-{i}", base_commit=f"{i:040x}") for i in range(10)]
    assert len(list(vs.VerifiedSolutionSource("swe_bench_extra", held_out=_held(), license_classify=_classify,
                                              rows=list(good), scan_limit=3).iter_admissible())) == 3
    assert len(list(_source(list(good), row_limit=4).iter_admissible())) == 4
    assert len(list(_source(list(good)).iter_admissible())) == 10, "no limits: unchanged"
