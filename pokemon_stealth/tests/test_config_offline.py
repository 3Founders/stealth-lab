from __future__ import annotations

import hashlib

import pytest

from pokemon_stealth.config import RomNotConfiguredError, require_rom_path, rom_sha256


def test_require_rom_path_raises_when_unset(monkeypatch):
    monkeypatch.delenv("POKEMON_RED_ROM", raising=False)
    with pytest.raises(RomNotConfiguredError, match="POKEMON_RED_ROM is not set"):
        require_rom_path()


def test_require_rom_path_raises_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMON_RED_ROM", str(tmp_path / "does_not_exist.gb"))
    with pytest.raises(RomNotConfiguredError, match="does not exist"):
        require_rom_path()


def test_require_rom_path_returns_real_path(monkeypatch, tmp_path):
    rom = tmp_path / "fake.gb"
    rom.write_bytes(b"\x00" * 32)
    monkeypatch.setenv("POKEMON_RED_ROM", str(rom))
    assert require_rom_path() == rom


def test_rom_sha256_matches_real_hash(tmp_path):
    path = tmp_path / "data.bin"
    content = b"some rom bytes" * 1000
    path.write_bytes(content)
    assert rom_sha256(path) == hashlib.sha256(content).hexdigest()
