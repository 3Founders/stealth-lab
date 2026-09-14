from __future__ import annotations

from pokemon_stealth.memory_map import decode_badges, decode_bcd, decode_status


def test_decode_status_healthy():
    assert decode_status(0) is None


def test_decode_status_asleep_bits():
    for b in range(1, 8):
        assert decode_status(b) == "asleep"


def test_decode_status_poisoned():
    assert decode_status(0x08) == "poisoned"


def test_decode_status_paralyzed():
    assert decode_status(0x40) == "paralyzed"


def test_decode_bcd_zero():
    assert decode_bcd(b"\x00\x00\x00") == 0


def test_decode_bcd_known_value():
    # 0x12 0x34 0x56 packed BCD -> 123456
    assert decode_bcd(b"\x12\x34\x56") == 123456


def test_decode_badges_none():
    assert decode_badges(0) == []


def test_decode_badges_boulder_only():
    assert decode_badges(0b00000001) == ["boulder"]


def test_decode_badges_multiple():
    # boulder (bit0) + earth (bit7)
    assert decode_badges(0b10000001) == ["boulder", "earth"]
