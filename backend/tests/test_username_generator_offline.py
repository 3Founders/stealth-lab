"""DB-free tests for app/services/username_generator.py (V1 contributor
identity)."""
from __future__ import annotations

import pytest

from app.services import username_generator as ug


def test_generated_candidates_are_well_formed_and_unblocked():
    cands = list(ug.generate_candidates(10))
    assert len(cands) == 10
    assert len(set(cands)) == 10  # distinct
    for c in cands:
        assert ug.is_well_formed(c)
        assert not ug.is_blocked(c)


def test_validate_username_accepts_a_normal_name():
    assert ug.validate_username("CopperFox") == "CopperFox"


@pytest.mark.parametrize("bad", ["ab", "a" * 33, "1startswithdigit", "has space", "has-dash", "has_underscore", "emoji😀name"])
def test_validate_username_rejects_malformed(bad):
    with pytest.raises(ug.InvalidUsername):
        ug.validate_username(bad)


def test_validate_username_rejects_reserved_case_insensitively():
    with pytest.raises(ug.InvalidUsername):
        ug.validate_username("Admin")
    with pytest.raises(ug.InvalidUsername):
        ug.validate_username("ADMIN")


def test_validate_username_rejects_profane_substrings():
    with pytest.raises(ug.InvalidUsername):
        ug.validate_username("xFuckingCool")


def test_normalize_is_lowercase():
    assert ug.normalize("CopperFox") == "copperfox"
    assert ug.normalize("  CopperFox  ") == "copperfox"


def test_numeric_suffix_fallback_stays_within_max_len():
    base = "X" * ug.MAX_LEN
    for cand in ug.numeric_suffix_fallback(base, count=3):
        assert len(cand) <= ug.MAX_LEN
        assert cand[-1].isdigit()


def test_numeric_suffix_fallback_is_distinct():
    cands = list(ug.numeric_suffix_fallback("CopperFox", count=5))
    assert len(set(cands)) == 5
    assert cands == ["CopperFox2", "CopperFox3", "CopperFox4", "CopperFox5", "CopperFox6"]
