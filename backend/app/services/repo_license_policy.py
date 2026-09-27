"""Repo-ingestion license verdict: a pure SPDX allowlist decision, with
nearest-ancestor in-repo LICENSE resolution.

WHY AN ALLOWLIST, AND NOT `screening.spdx_license_signal`
    `screening._SPDX_DENYLIST` is a curated DENYLIST: it fires only on a
    license GitHub positively identified as copyleft, and lets everything
    else through unflagged. That is the right shape for an advisory screen
    whose findings a caller may route either way. This module answers the
    stricter question a repo ingester must answer: "is this a license we
    have DISCLOSED a decision about?" A license nobody has ruled on is not
    a pass, it is a QUARANTINE -- an allowlist that defaults to permissive
    silently admits every license nobody thought about, and the omission
    is invisible after the fact.

    The two are complementary rather than duplicated. Screening records
    what a detector found; this returns the verdict. Neither classifies
    SPDX -- that is Licensee's job. This module only decides what to do
    with an id it is handed.

HARD FLOOR
    The copyleft / non-commercial families (GPL, AGPL, LGPL, SSPL,
    CC-BY-NC, CC-BY-ND) are rejected regardless of configuration: `allow`
    extends what MAY be allowed, it can never buy an ALLOW for a family
    this module refuses. Extending the allowlist is a deliberate ruling
    about a license nobody has judged yet (MPL-2.0 is the canonical
    example, and is deliberately absent from the default set for exactly
    that reason). Overriding the reject floor is not a configuration knob
    at all. Note the consequence, stated so it is not discovered later: a
    caller that adds a share-alike id (MPL-2.0, CC-BY-SA-4.0) to `allow`
    does move that license out of QUARANTINE. That is the point of an
    allowlist, and it is why every verdict carries the allowlist version
    back to the audit row.

SCOPE LIMITS (stated in-code, not implied)
    - `classify_spdx` takes an spdx id at face value; a caller that
      supplies a wrong id gets a confident verdict about the wrong id.
      `identify_spdx_from_text` is the narrow mitigation, not a detector
      replacement: it recognises a fixed set of license HEADERS by
      substring and returns None for everything else, so an unrecognized
      license becomes QUARANTINE rather than a guessed id. It reads text;
      it does not hash it, diff it against a corpus, or reason about
      `LicenseRef` custom fields.
    - Nearest-ancestor resolution sees only the paths present in the
      tree slice it was handed, and only the ids the caller already
      resolved for those paths. A license outside the slice, or attached
      to a single file rather than a directory, is invisible here.
      `license_paths` is the index builder's own answer to "which blobs
      could govern anything", so an index built from `license_paths` never
      leaves a governing blob unresolvable.
    - "Nearest ancestor" is a directory rule, the Apache-2.0 §4 shape.
      A per-file `LicenseRef` override is invisible to it.
    - Nothing here writes. `screening.record_screening_decision` is the
      audit path; this module returns the values to record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

VERDICTS: tuple[str, ...] = ("ALLOW", "QUARANTINE", "REJECT")

ALLOWLIST_VERSION = "repo-license-allowlist@v1"

DEFAULT_ALLOWLIST: frozenset[str] = frozenset({
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "0BSD",
    "Unlicense",
    "CC0-1.0",
})

REJECT_FAMILIES: tuple[str, ...] = (
    "GPL",
    "AGPL",
    "LGPL",
    "SSPL",
    "CC-BY-NC",
    "CC-BY-ND",
)

_MISSING_IDS: frozenset[str] = frozenset({"", "NONE"})
_UNIDENTIFIED_IDS: frozenset[str] = frozenset({"NOASSERTION"})

_LICENSE_STEMS: tuple[str, ...] = ("license", "licence", "copying")

_GNU_HEADERS: tuple[tuple[str, str], ...] = (
    ("gnu affero general public license", "AGPL"),
    ("gnu library general public license", "LGPL"),
    ("gnu lesser general public license", "LGPL"),
    ("gnu general public license", "GPL"),
)

_GNU_VERSIONS: tuple[tuple[str, str], ...] = (
    ("version 3", "3.0-only"),
    ("version 2.1", "2.1-only"),
    ("version 2", "2.0-only"),
    ("version 1", "1.0-only"),
)


@dataclass(frozen=True)
class LicenseVerdict:
    decision: str
    reason: str
    spdx_id: Optional[str]
    source_path: Optional[str]
    allowlist_version: str

    def as_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "spdx_id": self.spdx_id,
            "source_path": self.source_path,
            "allowlist_version": self.allowlist_version,
        }


def _norm_id(spdx_id: Optional[str]) -> str:
    return (spdx_id or "").strip()


def _reject_family(spdx_id: str) -> Optional[str]:
    upper = spdx_id.upper()
    return next((f for f in REJECT_FAMILIES if upper.startswith(f)), None)


def _effective_allowlist(allow: Optional[Iterable[str]]) -> tuple[frozenset[str], str]:
    """Configured allowlist, upper-cased for matching, minus anything on
    the reject floor, plus the version string an audit row should carry."""
    source = DEFAULT_ALLOWLIST if allow is None else allow
    kept = {i for i in (_norm_id(a).upper() for a in source) if not _reject_family(i)}
    version = ALLOWLIST_VERSION if allow is None else f"{ALLOWLIST_VERSION}+caller"
    return frozenset(kept), version


def _norm_path(path: Optional[str]) -> str:
    normed = (path or "").replace("\\", "/").strip().lstrip("/")
    while normed.startswith("./"):
        normed = normed[2:]
    return normed


def _is_license_blob(entry: Any) -> Optional[str]:
    if not isinstance(entry, Mapping):
        return None
    if entry.get("type") not in (None, "blob"):
        return None
    raw = entry.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    return _norm_path(raw)


def _license_stem_rank(path: str) -> int:
    stem = path.rsplit("/", 1)[-1].split(".")[0].lower()
    return next((i for i, s in enumerate(_LICENSE_STEMS) if stem.startswith(s)), -1)


def license_paths(tree: Optional[Iterable[Mapping[str, Any]]] = None) -> list[str]:
    """Every license blob in `tree`, normalized, deduplicated and sorted.

    This is the exact set `resolve_license_file` can ever return, which is
    what makes it the right key set for a caller's license-spdx index: a
    governing blob is never absent from the index, so a miss there can
    only be "we could not identify this one", never "we forgot to look".
    Sorted, so two runs over the same tree produce the same index in the
    same order whatever order GitHub listed the tree in.
    """
    found: set[str] = set()
    for entry in tree or ():
        blob = _is_license_blob(entry)
        if blob is not None and _license_stem_rank(blob) >= 0:
            found.add(blob)
    return sorted(found)


def resolve_license_file(
    path: Optional[str],
    tree: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Optional[str]:
    """The in-repo LICENSE blob governing `path`, or None.

    Nearest ancestor: the license in the DEEPEST directory that contains
    `path`, so a subfolder LICENSE overrides both a shallower one and the
    repository-level SPDX id. `path` may itself be the license file, in
    which case it resolves to itself. A license blob in a directory that
    is not an ancestor of `path` is not a candidate at all.

    Ties inside one directory -- `LICENSE` alongside `LICENSE-MIT`,
    `COPYING`, `LICENSE.txt` -- are broken by stem rank (LICENSE, then
    LICENCE, then COPYING) and then by the full path, so the result is a
    deterministic function of the tree and not of iteration order.
    """
    target = _norm_path(path)
    if not target:
        return None
    candidates: list[tuple[int, int, str]] = []
    for entry in tree or ():
        blob = _is_license_blob(entry)
        if blob is None or _license_stem_rank(blob) < 0:
            continue
        directory = blob.rsplit("/", 1)[0] if "/" in blob else ""
        if blob != target and directory and not target.startswith(f"{directory}/"):
            continue
        candidates.append((-blob.count("/"), _license_stem_rank(blob), blob))
    if not candidates:
        return None
    return sorted(candidates)[0][2]


def classify_spdx(
    spdx_id: Optional[str],
    *,
    allow: Optional[Iterable[str]] = None,
    source_path: Optional[str] = None,
    from_file: bool = False,
) -> LicenseVerdict:
    """The pure decision: one SPDX id in, one ALLOW/QUARANTINE/REJECT verdict out.

    `from_file=True` means the id (or the absence of one) came from an
    in-repo LICENSE blob rather than from a repository-level declaration,
    which changes the reason text for the unresolvable case: a LICENSE we
    found but could not identify is NOT silently replaced by the
    repository-level id, because the closer file is the one that governs
    and substituting the farther one would launder its terms.
    """
    effective, version = _effective_allowlist(allow)
    raw = _norm_id(spdx_id)
    upper = raw.upper()

    family = _reject_family(raw)
    if family is not None:
        return LicenseVerdict(
            decision="REJECT",
            reason=(
                f"{raw or '(no id)'} falls in the {family} copyleft/non-commercial "
                f"reject floor, which configuration cannot allow"
            ),
            spdx_id=raw or None,
            source_path=source_path,
            allowlist_version=version,
        )

    if upper in effective:
        configured = allow is not None
        reason = (
            f"{raw} is on the "
            + ("caller-configured" if configured else "disclosed")
            + f" permissive allowlist ({version})"
        )
        return LicenseVerdict("ALLOW", reason, raw or None, source_path, version)

    if upper in _UNIDENTIFIED_IDS:
        reason = (
            "license is NOASSERTION: the detector declined to identify it, "
            "so no ruling can be applied"
        )
    elif upper in _MISSING_IDS:
        reason = (
            f"no license id available from {source_path}" if from_file
            else "no license id available: no in-repo LICENSE file and no repository SPDX id"
        )
    else:
        reason = (
            f"{raw} is not on the disclosed permissive allowlist ({version}); "
            "an unidentified license is quarantined, not assumed permissive"
        )

    return LicenseVerdict("QUARANTINE", reason, raw or None, source_path, version)


def _normalized(text: Optional[str]) -> str:
    return " ".join((text or "").split()).lower()


def _gnu_id(normalized: str, family: str) -> str:
    for needle, suffix in _GNU_VERSIONS:
        if needle in normalized:
            return f"{family}-{suffix}"
    return family


def _creative_commons_id(normalized: str) -> Optional[str]:
    if "zero" in normalized and "1.0 universal" in normalized:
        return "CC0-1.0"
    noncommercial = "attribution-noncommercial" in normalized
    no_derivatives = "noderivatives" in normalized
    if noncommercial and no_derivatives:
        return "CC-BY-NC-ND"
    if noncommercial:
        return "CC-BY-NC"
    if no_derivatives:
        return "CC-BY-ND"
    return None


def identify_spdx_from_text(text: Optional[str]) -> Optional[str]:
    """The SPDX id a license blob's OWN text identifies, or None.

    A deliberately narrow header matcher, not a license detector and not a
    Licensee replacement: it recognises the fixed set of headers the
    decision actually turns on -- the four copyleft GNU families, SSPL,
    the Creative Commons non-commercial/CC0 shapes, Apache-2.0, MPL-2.0,
    the Unlicense, MIT, and the two BSD clause counts -- and returns None
    for everything else. Matching a header rather than a whole licence
    body is what makes it safe to run blind: a wrong id here would be
    worse than no id, because `classify_spdx` trusts whatever it is
    handed, so a misidentification becomes a confident verdict about the
    wrong license. None, by contrast, becomes QUARANTINE, which is the
    outcome the caller can audit and revisit.

    A GNU header is recognized as a FAMILY ("GPL", "LGPL", "AGPL") when
    its version line is not one of the four spelled out in `_GNU_VERSIONS`.
    Those family tokens are not canonical SPDX expressions and are not
    meant to be treated as one -- they exist because the reject floor
    matches on the family prefix, so a copyleft license stays rejected
    whether or not the exact version was read off the header. A versioned
    id is returned whenever the header states one. The Creative Commons
    branch is coarser still, and in the safe direction: it does not read a
    share-alike clause, so a full CC-BY-SA legal text -- which names every
    CC license type in its own definitions -- reports at the
    non-commercial family it also mentions. That is stricter than the
    license deserves, never looser, and the alternative (trusting a
    substring to pick out the one license type a several-thousand-word
    document is actually governed by) is a worse error than a coarse one.
    """
    t = _normalized(text)
    if not t:
        return None
    for header, family in _GNU_HEADERS:
        if header in t:
            return _gnu_id(t, family)
    if "server side public license" in t:
        return "SSPL-1.0"
    if "creative commons" in t:
        return _creative_commons_id(t)
    if "apache license" in t:
        return "Apache-2.0" if "version 2.0" in t else None
    if "mozilla public license" in t:
        return "MPL-2.0" if "version 2.0" in t else None
    if "this is free and unencumbered software released into the public domain" in t:
        return "Unlicense"
    if "permission is hereby granted" in t and ("mit license" in t or "expat" in t):
        return "MIT"
    if "redistribution and use in source and binary forms" in t:
        if "redistributions of source code must retain" not in t:
            return None
        return "BSD-3-Clause" if "neither the name of" in t else "BSD-2-Clause"
    if "permission to use, copy, modify, and/or distribute this software" in t:
        return "ISC"
    return None


def decide_repo_license(
    *,
    path: Optional[str] = None,
    tree: Optional[Iterable[Mapping[str, Any]]] = None,
    repo_spdx: Optional[str] = None,
    license_spdx_by_path: Optional[Mapping[str, str]] = None,
    allow: Optional[Iterable[str]] = None,
) -> LicenseVerdict:
    """Resolve the license governing one in-repo path, then classify it.

    Precedence: the nearest in-repo LICENSE blob wins over the
    repository-level `repo_spdx`. With no path, no tree, or no governing
    blob, the repository-level id is the fallback. `license_spdx_by_path`
    maps LICENSE blob paths to the SPDX ids a caller has already resolved
    for them; an entry the caller could not resolve is treated as no id at
    all, not as permission to fall back further up the tree.
    """
    resolved = resolve_license_file(path, tree)
    if resolved is not None:
        by_path: dict[str, str] = {}
        for key, value in (license_spdx_by_path or {}).items():
            norm = _norm_path(key)
            if norm:
                by_path.setdefault(norm, value)
        return classify_spdx(
            by_path.get(resolved),
            allow=allow,
            source_path=resolved,
            from_file=True,
        )
    return classify_spdx(repo_spdx, allow=allow, source_path=None, from_file=False)
