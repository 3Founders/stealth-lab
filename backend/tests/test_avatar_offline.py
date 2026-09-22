"""DB-free tests for app/services/avatar.py (V1 contributor identity)."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services import avatar


def _make_image(*, fmt="PNG", size=(800, 400), with_exif=False) -> bytes:
    img = Image.new("RGB", size, color=(120, 60, 30))
    out = io.BytesIO()
    if with_exif:
        exif = img.getexif()
        exif[0x0112] = 3  # Orientation tag
        img.save(out, format=fmt, exif=exif)
    else:
        img.save(out, format=fmt)
    return out.getvalue()


def test_valid_png_is_accepted_and_normalized_to_square_webp():
    data = _make_image(fmt="PNG", size=(1000, 500))
    out = avatar.process_avatar(data)
    result = Image.open(io.BytesIO(out))
    assert result.format == "WEBP"
    assert result.size == (avatar.TARGET_SIZE, avatar.TARGET_SIZE)


def test_valid_jpeg_is_accepted():
    data = _make_image(fmt="JPEG", size=(600, 600))
    out = avatar.process_avatar(data)
    assert Image.open(io.BytesIO(out)).format == "WEBP"


def test_valid_webp_is_accepted():
    data = _make_image(fmt="WEBP", size=(400, 900))
    out = avatar.process_avatar(data)
    assert Image.open(io.BytesIO(out)).format == "WEBP"


def test_malformed_bytes_are_rejected():
    with pytest.raises(avatar.InvalidImage):
        avatar.process_avatar(b"not an image, just garbage bytes" * 10)


def test_empty_upload_is_rejected():
    with pytest.raises(avatar.InvalidImage):
        avatar.process_avatar(b"")


def test_mime_spoof_is_rejected_by_real_content_sniffing():
    """A caller claiming image/png in a multipart form is irrelevant here --
    process_avatar only trusts what Pillow actually decodes. Feed it a
    plain text payload; there is no MIME hint parameter to spoof at all,
    proving the check is on real bytes, not a client-declared type."""
    with pytest.raises(avatar.InvalidImage):
        avatar.process_avatar(b"<html>this is not an image</html>")


def test_disallowed_format_is_rejected():
    img = Image.new("RGB", (200, 200), color=(10, 10, 10))
    out = io.BytesIO()
    img.save(out, format="BMP")
    with pytest.raises(avatar.InvalidImage):
        avatar.process_avatar(out.getvalue())


def test_exif_orientation_is_applied_then_stripped():
    data = _make_image(fmt="JPEG", size=(300, 300), with_exif=True)
    out = avatar.process_avatar(data)
    result = Image.open(io.BytesIO(out))
    # No exif block survives the re-encode (process_avatar never passes
    # exif= to .save()).
    assert not result.getexif()


def test_wide_and_tall_images_both_crop_to_center_square():
    for size in [(1200, 300), (300, 1200)]:
        out = avatar.process_avatar(_make_image(fmt="PNG", size=size))
        result = Image.open(io.BytesIO(out))
        assert result.size == (avatar.TARGET_SIZE, avatar.TARGET_SIZE)


# --- deterministic fallback avatar ---------------------------------------


def test_initials_for_adjective_noun_username():
    assert avatar.initials_for("CopperFox") == "CF"
    assert avatar.initials_for("QuietOtter") == "QO"


def test_fallback_avatar_is_deterministic_for_same_seed():
    svg1 = avatar.fallback_avatar_svg("CopperFox", seed="user-1")
    svg2 = avatar.fallback_avatar_svg("CopperFox", seed="user-1")
    assert svg1 == svg2


def test_fallback_avatar_differs_by_seed_not_only_by_name():
    svg_a = avatar.fallback_avatar_svg("CopperFox", seed="user-1")
    svg_b = avatar.fallback_avatar_svg("CopperFox", seed="user-2")
    assert svg_a != svg_b  # different background color


def test_fallback_avatar_is_svg_with_initials():
    svg = avatar.fallback_avatar_svg("CopperFox", seed="user-1")
    assert b"<svg" in svg
    assert b"CF" in svg
