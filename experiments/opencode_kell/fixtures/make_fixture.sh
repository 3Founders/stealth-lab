#!/usr/bin/env bash
# Builds the small repo the spike runs on: a clamp() with a bug, its tests (one fails), and a git history.
# usage: make_fixture.sh <empty-dir>
set -e
d="$1"; mkdir -p "$d"; cd "$d"
cat > mathx.py <<'P'
"""Small numeric helpers."""


def clamp(x, lo, hi):
    """Return x limited to the range lo..hi (inclusive)."""
    if x < lo:
        return lo
    if x > hi:
        return lo          # bug: should return hi
    return x


def mean(values):
    """Arithmetic mean of a non-empty list."""
    return sum(values) / len(values)
P
cat > test_mathx.py <<'P'
from mathx import clamp, mean


def test_clamp_inside():
    assert clamp(5, 0, 10) == 5


def test_clamp_low():
    assert clamp(-1, 0, 10) == 0


def test_clamp_high():
    assert clamp(99, 0, 10) == 10


def test_mean():
    assert mean([1, 2, 3]) == 2
P
git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -q -m "fixture"
