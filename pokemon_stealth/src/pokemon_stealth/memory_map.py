"""
Pokemon Red (English, Red/Blue-era WRAM layout) memory map.

PROVENANCE / HONESTY NOTE (read before trusting an address):

These addresses come from the public, decades-old community disassembly of
Pokemon Red (the pret/pokered project) and are the same constants widely
reused by open-source PyBoy-based Pokemon RL/bot projects (e.g.
PWhiddy/PokemonRedExperiments). They are NOT independently re-verified
against a real ROM in this codebase -- no ROM was available in this
environment to test against. Every address below is tagged with a
Confidence level; state.py refuses to report a HIGH-only field if decoding
looks inconsistent, and never fabricates a value for a field tagged
UNVERIFIED that fails a sanity check.

Run `pokemon-stealth verify-memory-map` once you have a real ROM loaded at
the New Game state -- it prints the decoded values next to what they should
be (map=Pallet Town, party_count=0, badges=0) so you can catch a wrong
offset immediately instead of trusting this file blindly.

If you are running a ROM hack, a non-English localization, or a Yellow
cartridge, these offsets are NOT guaranteed to match -- see
`GameVersion` / `detect_version()` below, which reads the real cartridge
header (a documented, stable Game Boy hardware structure, not
version-specific reverse-engineering) to at least tell you which cartridge
you loaded.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Confidence(str, Enum):
    HIGH = "high"  # widely cross-confirmed across multiple independent public sources
    MEDIUM = "medium"  # commonly cited, less cross-confirmed
    UNVERIFIED = "unverified"  # best-effort; do not trust without checking


# --- Cartridge header (real GB hardware spec, not ROM-specific) ----------
HEADER_TITLE_START = 0x134
HEADER_TITLE_END = 0x144
HEADER_GLOBAL_CHECKSUM = 0x14E  # 2 bytes, big-endian


@dataclass(frozen=True)
class Addr:
    address: int
    size: int
    confidence: Confidence


# Overworld / progression
CUR_MAP = Addr(0xD35E, 1, Confidence.HIGH)
Y_COORD = Addr(0xD361, 1, Confidence.HIGH)
X_COORD = Addr(0xD362, 1, Confidence.HIGH)
BADGES = Addr(0xD356, 1, Confidence.HIGH)  # bitfield, 1 bit per badge
MONEY = Addr(0xD347, 3, Confidence.HIGH)  # 3-byte BCD, big-endian

# Bag
BAG_ITEM_COUNT = Addr(0xD31D, 1, Confidence.MEDIUM)
BAG_ITEMS_START = Addr(0xD31E, 40, Confidence.MEDIUM)  # (item_id, qty) pairs, 0xFF-terminated

# Party
PARTY_COUNT = Addr(0xD163, 1, Confidence.HIGH)
PARTY_SPECIES_LIST = Addr(0xD164, 6, Confidence.HIGH)  # 0xFF-terminated
PARTY_MON_STRUCT_START = 0xD16B
PARTY_MON_STRUCT_SIZE = 44  # 0x2C bytes per mon

# Offsets WITHIN one party-mon struct (relative to PARTY_MON_STRUCT_START + i*44)
PMON_OFF_SPECIES = 0x00
PMON_OFF_CUR_HP = 0x01  # 2 bytes, big-endian
PMON_OFF_STATUS = 0x04
PMON_OFF_MOVES = 0x08  # 4 bytes, 1 move id each, 0 = empty slot
PMON_OFF_PP = 0x0C  # 4 bytes, 1 per move
PMON_OFF_LEVEL = 0x21
PMON_OFF_MAX_HP = 0x22  # 2 bytes, big-endian

# Battle (best-effort -- lower confidence, cross-checked less thoroughly)
BATTLE_TYPE = Addr(0xD057, 1, Confidence.MEDIUM)  # 0 = not in battle
ENEMY_MON_SPECIES = Addr(0xCFE5, 1, Confidence.MEDIUM)
ENEMY_MON_CUR_HP = Addr(0xCFE6, 2, Confidence.MEDIUM)
ENEMY_MON_MAX_HP = Addr(0xCFF4, 2, Confidence.UNVERIFIED)
ENEMY_MON_LEVEL = Addr(0xCFF3, 1, Confidence.UNVERIFIED)

# Fields deliberately NOT mapped here because no confident public source was
# available: facing direction, dialogue/text-box active state. state.py
# reports these as unavailable rather than guessing an address. Dialogue
# presence is instead inferred behaviorally (frame-freeze heuristic) in
# emulator.py, and clearly labeled as a heuristic, not a RAM read.

STATUS_BITS = {
    0: None,  # healthy
    1: "asleep",  # bits 0-2 hold sleep counter when nonzero; simplified here
    2: "asleep",
    3: "asleep",
    4: "asleep",
    5: "asleep",
    6: "asleep",
    7: "asleep",
    8: "poisoned",
    0x10: "burned",
    0x20: "frozen",
    0x40: "paralyzed",
}


def decode_status(byte: int) -> str | None:
    if byte == 0:
        return None
    if byte & 0x07:
        return "asleep"
    for mask, name in STATUS_BITS.items():
        if mask and byte & mask:
            return name
    return "unknown_status"


def decode_bcd(raw: bytes) -> int:
    """3-byte packed-BCD money value -> int."""
    value = 0
    for byte in raw:
        value = value * 100 + ((byte >> 4) * 10 + (byte & 0x0F))
    return value


def decode_badges(byte: int) -> list[str]:
    names = [
        "boulder", "cascade", "thunder", "rainbow",
        "soul", "marsh", "volcano", "earth",
    ]
    return [name for i, name in enumerate(names) if byte & (1 << i)]
