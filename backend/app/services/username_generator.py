"""
Public keळ username generation + validation.

Product decision (see the V1 identity spec this implements): the public
keळ username is NOT authentication and NOT `users.display_name` (the
IdP-supplied name, copied once at first login and never user-facing). It
lives in `contributor_profiles.username` (migration 106) and is generated
here server-side, then reserved through a DB unique index -- the frontend
never gets to claim a name the server hasn't verified.

Word lists are curated for a calm, technical, pseudonymous tone -- explicit
non-goals: nothing childish, nothing "gamer handle"-flavored, no numbers
baked into the base word (a numeric suffix is only ever a last-resort
collision fallback, appended by generate_candidates, never by the lists).
"""
from __future__ import annotations

import re
import secrets
from typing import Iterable

MIN_LEN = 3
MAX_LEN = 32

# ASCII letters/digits only, no leading digit (keeps "generated adjective+
# noun" and "typed by a person" the same shape), no underscores/hyphens --
# a username is a display token, not a slug.
_VALID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,31}$")

ADJECTIVES: tuple[str, ...] = (
    "Quiet", "Copper", "Lunar", "Steady", "Curious", "Clear", "Lucid", "Keen",
    "Calm", "Bright", "Sharp", "Distant", "Patient", "Silent", "Vivid",
    "Northern", "Southern", "Amber", "Cobalt", "Granite", "Cedar", "Slate",
    "Quiet", "Even", "True", "Deep", "Long", "Wide", "Still", "Swift",
    "Honest", "Exact", "Precise", "Ready", "Solid", "Sound", "Level",
)

NOUNS: tuple[str, ...] = (
    "Otter", "Fox", "Badger", "Raven", "Panda", "Falcon", "Vector", "Meridian",
    "Heron", "Wolf", "Hawk", "Crane", "Lynx", "Sparrow", "Bear", "Owl",
    "River", "Ridge", "Harbor", "Summit", "Orbit", "Compass", "Anchor",
    "Beacon", "Bridge", "Forge", "Loom", "Cairn", "Ledger", "Axiom",
    "Circuit", "Signal", "Current", "Keystone", "Pathway",
)

# Reserved regardless of whether an account happens to generate/request them
# -- routes, product nouns, and obvious impersonation vectors.
RESERVED_USERNAMES: frozenset[str] = frozenset(
    n.lower()
    for n in (
        "admin", "administrator", "root", "system", "support", "help",
        "keळ", "kel", "stealthlab", "stealth-lab", "official", "staff",
        "moderator", "mod", "security", "api", "null", "undefined", "anonymous",
        "everyone", "here", "settings", "account", "profile", "onboarding",
        "sign-in", "signin", "sign-up", "signup", "login", "logout", "u",
        "problems", "search", "docs", "credits", "contributors", "me",
    )
)

# Small, deliberately conservative blocklist -- substring match against the
# lowercased candidate. V1 goal is "no obviously offensive default/claimed
# username", not a comprehensive profanity model.
_BLOCKED_SUBSTRINGS: tuple[str, ...] = (
    "fuck", "shit", "bitch", "cunt", "nigger", "nigga", "faggot", "retard",
    "rape", "nazi", "hitler", "kike", "spic", "chink", "tranny", "whore",
)


def normalize(username: str) -> str:
    """Canonical form used for uniqueness comparisons (case-insensitive)."""
    return username.strip().lower()


def is_well_formed(username: str) -> bool:
    return bool(_VALID_RE.match(username)) and MIN_LEN <= len(username) <= MAX_LEN


def is_blocked(username: str) -> bool:
    norm = normalize(username)
    if norm in RESERVED_USERNAMES:
        return True
    return any(bad in norm for bad in _BLOCKED_SUBSTRINGS)


class InvalidUsername(ValueError):
    """A username failed validation -- malformed, reserved, or blocked.
    `.reason` is safe to show a user (no internals, no blocklist contents)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate_username(username: str) -> str:
    """Raises InvalidUsername with a user-safe reason, else returns the
    username unchanged (callers still normalize separately for storage/
    comparison -- the STORED value keeps the user's chosen casing)."""
    username = (username or "").strip()
    if not (MIN_LEN <= len(username) <= MAX_LEN):
        raise InvalidUsername(f"username must be {MIN_LEN}-{MAX_LEN} characters")
    if not is_well_formed(username):
        raise InvalidUsername("username must start with a letter and contain only ASCII letters and numbers")
    if is_blocked(username):
        raise InvalidUsername("that username isn't available")
    return username


def _one_candidate() -> str:
    return f"{secrets.choice(ADJECTIVES)}{secrets.choice(NOUNS)}"


def generate_candidates(n: int = 8) -> Iterable[str]:
    """Yield up to `n` DISTINCT adjective+noun candidates, unblocked. The
    caller (contributors.py) checks each against the DB in turn and takes
    the first free one; this function never touches the database, so it is
    safe to call from anywhere (including a pure "suggest a name" read path)
    without reserving anything."""
    seen: set[str] = set()
    attempts = 0
    while len(seen) < n and attempts < n * 20:
        attempts += 1
        cand = _one_candidate()
        if cand in seen or is_blocked(cand):
            continue
        seen.add(cand)
        yield cand


def numeric_suffix_fallback(base: str, *, start: int = 2, count: int = 50) -> Iterable[str]:
    """Last-resort fallback once plain adjective+noun candidates are
    exhausted (collision-heavy DB): BaseName2, BaseName3, ... Still capped
    at MAX_LEN by truncating the base, never the digits."""
    for i in range(start, start + count):
        suffix = str(i)
        base_trunc = base[: MAX_LEN - len(suffix)]
        yield f"{base_trunc}{suffix}"
