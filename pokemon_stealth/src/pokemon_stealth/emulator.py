"""
Thin PyBoy wrapper. This is the ONLY module that talks to PyBoy directly --
everything else (state.py, actions.py) goes through PokemonEnv so a PyBoy
API change or version bump has one place to fix.

Targets PyBoy >= 2.0's API (`pyboy.memory[addr]`, `button_press`/
`button_release`, `save_state`/`load_state` on file objects). If your
installed PyBoy version differs, this is where to adjust -- do not scatter
direct PyBoy calls elsewhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pokemon_stealth import memory_map as mm
from pokemon_stealth.config import require_rom_path


class PokemonEnv:
    def __init__(self, rom_path: Optional[Path] = None, headless: bool = True, speed: int = 0):
        """
        rom_path: explicit path, or None to read POKEMON_RED_ROM from env
                  (fails loudly via require_rom_path() if unset/missing --
                  never substitutes or downloads a ROM).
        headless: True -> window="null" (fast, no rendering).
        speed: PyBoy emulation_speed; 0 = uncapped (fastest), 1 = real-time.
        """
        self.rom_path = rom_path or require_rom_path()
        self.headless = headless
        self._pyboy = None
        self._speed = speed
        self._frame_count = 0

    def start(self) -> None:
        from pyboy import PyBoy

        window = "null" if self.headless else "SDL2"
        self._pyboy = PyBoy(str(self.rom_path), window=window)
        self._pyboy.set_emulation_speed(self._speed)

    @property
    def pyboy(self):
        if self._pyboy is None:
            raise RuntimeError("PokemonEnv.start() was not called")
        return self._pyboy

    def close(self) -> None:
        if self._pyboy is not None:
            self._pyboy.stop(save=False)
            self._pyboy = None

    # --- ticking -----------------------------------------------------
    def tick(self, frames: int = 1) -> None:
        for _ in range(frames):
            self.pyboy.tick()
            self._frame_count += 1

    @property
    def frame_count(self) -> int:
        return self._frame_count

    # --- checkpoints ---------------------------------------------------
    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            self.pyboy.save_state(f)

    def load_state(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"no savestate at {path}")
        with open(path, "rb") as f:
            self.pyboy.load_state(f)

    # --- input -----------------------------------------------------------
    _BUTTONS = {"up", "down", "left", "right", "a", "b", "start", "select"}

    def press(self, button: str, hold_frames: int = 8, release_frames: int = 4) -> None:
        button = button.lower()
        if button not in self._BUTTONS:
            raise ValueError(f"unknown button '{button}', expected one of {self._BUTTONS}")
        self.pyboy.button_press(button)
        self.tick(hold_frames)
        self.pyboy.button_release(button)
        self.tick(release_frames)

    # --- raw memory ------------------------------------------------------
    def read_byte(self, addr: int) -> int:
        return int(self.pyboy.memory[addr])

    def read_bytes(self, addr: int, size: int) -> bytes:
        return bytes(self.pyboy.memory[addr : addr + size])

    def read(self, field: mm.Addr) -> bytes:
        return self.read_bytes(field.address, field.size)

    def cartridge_title(self) -> str:
        raw = self.read_bytes(mm.HEADER_TITLE_START, mm.HEADER_TITLE_END - mm.HEADER_TITLE_START)
        return raw.split(b"\x00")[0].decode("ascii", errors="replace")

    def screenshot(self):
        """Returns a PIL Image via PyBoy's screen buffer. Only used in demo mode."""
        return self.pyboy.screen.image
