"""
ROM/pyboy-dependent tests live in *_e2e.py and skip themselves (same
convention as backend/tests/*_e2e.py) -- they need a real, legally
obtained ROM this environment cannot provide. Everything else here is a
pure offline unit test with no PyBoy/ROM dependency.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

ROM_AVAILABLE = bool(os.environ.get("POKEMON_RED_ROM")) and Path(
    os.environ.get("POKEMON_RED_ROM", "")
).is_file()

try:
    import pyboy  # noqa: F401

    PYBOY_AVAILABLE = True
except ImportError:
    PYBOY_AVAILABLE = False

requires_rom = pytest.mark.skipif(
    not (ROM_AVAILABLE and PYBOY_AVAILABLE),
    reason="requires a real POKEMON_RED_ROM path and pyboy installed -- see README",
)
