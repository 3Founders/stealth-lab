"""
Central config. Everything is read from the environment (optionally via a
.env file in the project root) -- no hardcoded paths, no auto-download.

ROM handling is deliberately strict: this module never bundles, generates,
or fetches a ROM. If POKEMON_RED_ROM is unset or the path doesn't exist,
`require_rom_path()` raises with an explicit, actionable message instead of
silently continuing.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Loaded once at import time; a real .env in the project root (or CWD) wins,
# but real process env vars always take precedence (default dotenv behavior).
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STEALTH_DIR = PROJECT_ROOT / "stealth"
CHECKPOINTS_DIR = PROJECT_ROOT / "checkpoints"
RESULTS_DIR = PROJECT_ROOT / "results"


class RomNotConfiguredError(RuntimeError):
    pass


def require_rom_path() -> Path:
    """
    Returns the local, user-provided ROM path. Never downloads, never
    fabricates a stand-in. Fails loudly and explains exactly how to fix it.
    """
    raw = os.environ.get("POKEMON_RED_ROM")
    if not raw:
        raise RomNotConfiguredError(
            "POKEMON_RED_ROM is not set.\n"
            "This harness never downloads or bundles a ROM (Pokemon Red is "
            "copyrighted). Provide the path to your own legally obtained "
            "ROM file, e.g.:\n"
            "  POKEMON_RED_ROM=/path/to/pokemon_red.gb\n"
            "either as a real environment variable or in a .env file at "
            f"{PROJECT_ROOT / '.env'}"
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise RomNotConfiguredError(
            f"POKEMON_RED_ROM is set to '{path}' but that file does not exist. "
            "Fix the path -- this harness will not substitute or download a ROM."
        )
    return path


def rom_sha256(path: Path) -> str:
    """Used for reproducibility records (STEP 30) and revision detection."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class LLMConfig:
    """
    Minimal, standalone LLM config -- deliberately not importing
    backend/app/config.py's Settings (which pulls in Postgres/asyncpg and a
    much larger required surface than this harness needs). Reuses the same
    *pattern* (env-driven, Optional secrets, explicit `require`), not the
    same object.
    """

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    # OpenAI-compatible endpoint for a cheap "student" model (local server,
    # OpenRouter, etc.) -- optional.
    student_base_url: str | None = None
    student_api_key: str | None = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            student_base_url=os.environ.get("STUDENT_MODEL_BASE_URL"),
            student_api_key=os.environ.get("STUDENT_MODEL_API_KEY"),
        )


LLM_CONFIG = LLMConfig.from_env()
