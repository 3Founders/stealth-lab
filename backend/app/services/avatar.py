"""
Avatar processing (V1 contributor identity).

Bytes-in, bytes-out only -- storage is the caller's job (reuses the
existing ObjectStore abstraction, app/services/object_storage.py; no second
storage path). This module's only responsibility is turning an untrusted
upload into a safe, normalized WebP:

  1. decode the ACTUAL image content (never trust the client's declared
     MIME type or the filename/extension),
  2. reject anything that isn't really JPEG/PNG/WebP,
  3. auto-orient from EXIF, then strip all metadata (the re-encode below
     never copies EXIF forward -- Pillow only preserves it if you ask),
  4. center-crop to square, resize to a fixed dimension,
  5. re-encode to WebP.

No SVG upload support in V1 (SVG is code, not pixels, and there is no
sanitizer in this repo to make that safe).

Also holds the deterministic-initials fallback avatar (SVG, generated on
every request, never stored) used when a contributor has no custom avatar.
"""
from __future__ import annotations

import hashlib
import io
from typing import Optional

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB, enforced by the caller before this module ever sees the bytes
TARGET_SIZE = 512
WEBP_QUALITY = 85
_ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class InvalidImage(ValueError):
    """The uploaded bytes are not a real, supported image. Message is
    user-safe (no internals)."""


def process_avatar(data: bytes) -> bytes:
    """Validate + normalize an uploaded avatar. Raises InvalidImage for
    anything malformed, truncated, or not JPEG/PNG/WebP -- the format check
    is against Pillow's OWN sniffed format, not any caller-supplied hint."""
    if not data:
        raise InvalidImage("empty upload")

    # First pass: verify() cheaply rejects truncated/corrupt files, but
    # invalidates the Image object for further use -- so decode again after.
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        fmt = probe.format
    except Exception as exc:  # noqa: BLE001 - any decode failure is a rejection, not a crash
        raise InvalidImage("could not read image") from exc

    if fmt not in _ALLOWED_FORMATS:
        raise InvalidImage("only JPEG, PNG, or WebP images are accepted")

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # apply real orientation BEFORE the EXIF block is dropped below
        img = img.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise InvalidImage("could not decode image") from exc

    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)

    out = io.BytesIO()
    # No `exif=` kwarg passed -- Pillow only embeds EXIF when explicitly
    # given a payload, so this re-encode is metadata-free by construction.
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=6)
    return out.getvalue()


# --- deterministic fallback (no custom avatar) ---------------------------

_PALETTE: tuple[str, ...] = (
    "#3A5A78", "#6B4C9A", "#2E7D6B", "#8A5A2E", "#5A4A8A",
    "#2E6B8A", "#7A3A5A", "#4A7A3A", "#8A6B2E", "#3A3A8A",
)


def initials_for(display_text: str) -> str:
    """CopperFox -> CF. Deterministic, no punctuation dependence."""
    letters = [c for c in display_text if c.isalpha()]
    if not letters:
        return "?"
    # Adjective+Noun usernames are the common case: split on the first
    # internal uppercase letter after position 0 if present, else just
    # take the first two letters.
    upper_positions = [i for i, c in enumerate(display_text[1:], start=1) if c.isupper()]
    if upper_positions:
        return (display_text[0] + display_text[upper_positions[0]]).upper()
    return "".join(letters[:2]).upper()


def _color_for(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def fallback_avatar_svg(display_text: str, *, seed: Optional[str] = None) -> bytes:
    """A small deterministic SVG: initials on a stable background color
    derived from `seed` (falls back to display_text). Never randomized per
    render -- same identity always renders the same fallback."""
    initials = initials_for(display_text)
    color = _color_for(seed or display_text)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {TARGET_SIZE} {TARGET_SIZE}" '
        f'width="{TARGET_SIZE}" height="{TARGET_SIZE}">'
        f'<rect width="100%" height="100%" fill="{color}"/>'
        f'<text x="50%" y="50%" dy=".08em" text-anchor="middle" dominant-baseline="middle" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="{TARGET_SIZE // 2}" '
        f'fill="#F8F6EF" font-weight="500">{initials}</text>'
        f'</svg>'
    )
    return svg.encode("utf-8")
