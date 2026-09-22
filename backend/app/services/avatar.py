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

import base64
import functools
import hashlib
import io
import os
from typing import Optional

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB, enforced by the caller before this module ever sees the bytes
TARGET_SIZE = 512
WEBP_QUALITY = 85
_ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

# The fallback-avatar initials are set in Barlow -- the site's own declared
# companion face for Bahnschrift (see app/globals.css's --font stack:
# Bahnschrift first, Barlow second, loaded everywhere else via
# @fontsource/barlow). A plain `font-family="Barlow"` would only resolve on
# a viewer that happens to have it installed as a SYSTEM font, which almost
# no one does -- this SVG is served standalone (as an <img> src), so it
# can't reach the app's own page-level web font the way normal site text
# does. Embedding the woff2 bytes directly as a data: URI inside the SVG's
# own @font-face makes it self-contained: the initials render in the exact
# same face as every button/heading on the site, on any viewer, with no
# extra network request.
_FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "barlow-semibold.woff2")


@functools.lru_cache(maxsize=1)
def _embedded_font_face() -> str:
    try:
        with open(_FONT_PATH, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""  # font asset missing -- text still renders, just via the sans-serif fallback below
    return (
        "<style>@font-face{font-family:'Barlow';font-weight:600;font-style:normal;"
        f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}</style>"
    )


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

# Each entry is a two-stop gradient (dark, light), curated to stay in the
# same muted/editorial register as the rest of the site (see app/globals.css's
# --ink/--yellow/--cobalt) rather than going bright/neon -- these are meant
# to read as "a calm identity chip", not a logo.
_PALETTE: tuple[tuple[str, str], ...] = (
    ("#2C3E63", "#5B7FB8"),  # cobalt
    ("#5A3E7A", "#9B6FC4"),  # violet
    ("#1F5C4E", "#4FA88C"),  # teal
    ("#7A4A1F", "#D19A4E"),  # copper
    ("#3E2C63", "#7B5FB8"),  # indigo
    ("#1F4A5C", "#4E8DA8"),  # slate blue
    ("#5C1F3E", "#B85B85"),  # plum
    ("#2C5C1F", "#7BA84E"),  # moss
    ("#5C4A1F", "#B8954E"),  # amber
    ("#1F2C5C", "#5F6FB8"),  # deep blue
    ("#4A1F5C", "#A85FB8"),  # magenta
    ("#5C3E1F", "#B87F4E"),  # rust
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


def _gradient_for(seed: str) -> tuple[str, str]:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def _angle_for(seed: str) -> int:
    """One of 4 diagonal angles -- enough variety that same-palette avatars
    don't all look identical, still deterministic per identity."""
    digest = hashlib.sha256((seed + ":angle").encode("utf-8")).digest()
    return (0, 45, 90, 135)[digest[0] % 4]


def fallback_avatar_svg(display_text: str, *, seed: Optional[str] = None) -> bytes:
    """A small deterministic SVG: initials over a two-tone diagonal
    gradient chip, derived entirely from `seed` (falls back to
    display_text). Never randomized per render -- same identity always
    renders the same fallback, in the same site-appropriate muted palette."""
    key = seed or display_text
    initials = initials_for(display_text)
    dark, light = _gradient_for(key)
    angle = _angle_for(key)
    gid = f"g{abs(hash(key)) % 100000}"
    size = TARGET_SIZE
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}">'
        f'<defs>'
        f'<linearGradient id="{gid}" gradientTransform="rotate({angle} 0.5 0.5)">'
        f'<stop offset="0%" stop-color="{dark}"/>'
        f'<stop offset="100%" stop-color="{light}"/>'
        f'</linearGradient>'
        f'<radialGradient id="{gid}-sheen" cx="30%" cy="26%" r="75%">'
        f'<stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.22"/>'
        f'<stop offset="55%" stop-color="#FFFFFF" stop-opacity="0"/>'
        f'</radialGradient>'
        f'</defs>'
        f'{_embedded_font_face()}'
        f'<rect width="100%" height="100%" fill="url(#{gid})"/>'
        f'<rect width="100%" height="100%" fill="url(#{gid}-sheen)"/>'
        f'<text x="50%" y="50%" dy=".07em" text-anchor="middle" dominant-baseline="middle" '
        f'font-family="Barlow, Helvetica Neue, Helvetica, Arial, sans-serif" font-size="{size * 0.42:.0f}" '
        f'letter-spacing="1" fill="#F8F6EF" fill-opacity="0.96" font-weight="600" '
        f'style="text-shadow:0 1px 3px rgba(0,0,0,.25)">{initials}</text>'
        f'</svg>'
    )
    return svg.encode("utf-8")
