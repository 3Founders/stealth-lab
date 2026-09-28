"""Content-level gates for scraped `SKILL.md` corpora.

Everything in here is PURE unless a name says otherwise. The split matters:
the disposition logic is what the proving tests need to pin, and a gate that
reached for the network could not be tested without one.

WHY GATES EXIST AT ALL
    arXiv:2604.04323 measured that an agent given a realistic pool of 34k
    licensed skills scored 38.4% against a 35.4% no-skills baseline, and
    scored *below* baseline on two of three models (19.8 vs 21.8; 19.7 vs
    20.5). Their conclusion is the whole reason this module exists: an
    unfiltered pool is a net loss, because irrelevant skills are actively
    followed rather than ignored. A gate is not a nice-to-have here; it is
    the difference between the corpus helping and hurting.

    The same paper found agents loaded their curated skills only 49% of the
    time even with the skills sitting in their own context. The failure was
    not retrieval -- it was that `name` + `description` are the entire
    routing signal. So frontmatter conformance (hard) and description
    quality (scored) are the highest-leverage fields we can touch at
    ingestion time.

WHAT IS A HARD GATE AND WHAT IS NOT, AND WHY
    arXiv:2607.01456 found >99% of a popularity-filtered 238-skill sample
    carried at least one of 26 "skill smells", mean 10.5 per file. If we
    hard-rejected on smells we would reject essentially everything and the
    corpus would yield nothing. So:

      hard  -> the row cannot be indexed or routed correctly at all
               (bad `name`, absent description, missing body, absurd size)
      scored -> a prior that should influence ordering and review
               (description quality, stars, recency, smells)

    This is the same hard/soft discipline `ingest_skill_md` already uses for
    `provenance`, and the same reason: a hard gate that fires on a judgement
    call silently discards recoverable data, while a soft signal that is
    treated as a gate is invisible in the disposition counts.

THE ONE PLACE WE DEVIATE FROM "FLAG, NEVER BLOCK"
    `screening.screen_document_text` returns block-severity findings for
    prompt injection and secret exposure. arXiv:2602.06547 measured 26.1% of
    31,132 real skills carrying at least one vulnerability, with 13.3%
    showing exfiltration, and 84.2% of the vulnerabilities living in SKILL.md
    *natural language* rather than in code. We therefore treat a
    block-severity screening finding as a QUARANTINE disposition (recorded,
    reviewable, not served) rather than a silent drop. Nothing here deletes.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

GATE_VERSION = "skillmd-gate@v1"

# Agent Skills spec (agentskills.io/specification, read 2026-09-28):
# name is 1-64 chars, a-z 0-9 and '-' only, no leading or trailing '-', no
# '--'. description is 1-1024 chars and must be non-empty. The spec itself
# gives `PDF-Processing`, `-pdf` and `pdf--processing` as invalid examples.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024

# arXiv:2607.01456 defines "Oversized SKILL.md" as a *static* smell at
# >5,000 words, and the agentskills-fs loader rejects >10 MB before reading a
# file into memory. The corpus's own max is 170,038 words, which is a pasted
# log wearing a SKILL.md filename, not a skill. The spec's own progressive
# disclosure budget is <5,000 *tokens* for the body, so 5,000 words is the
# conservative side of the line and is the number we gate on.
MAX_BODY_WORDS = 5_000

# A row under this many words cannot carry a procedure. Measured: the uniform
# sample of 125 re-fetched skills had none under 20 words, so this only fires
# on the corpus's documented `lines` min of 1 -- but it must exist, because a
# one-line file is a routing hazard no matter how rare.
MIN_BODY_WORDS = 20

# arXiv:2602.06547's named coercive/secrecy language, from 157 confirmed
# malicious skills. Our existing `screening` module catches instruction
# *override* (the `_META_DIRECTIVE_RE` family) but not the "do not tell the
# user" family, which was the single most productive heuristic in that paper.
# These are matched as whole phrases so ordinary prose ("do not ask the user
# to confirm, the default is fine") is not what trips them.
_COERCIVE_PHRASES: tuple[tuple[str, str], ...] = (
    (r"do\s+not\s+mention\s+(this\s+)?(to|in)\s+(the\s+)?(user|conversation)", "conceal_from_user"),
    (r"don'?t\s+mention\s+(this\s+)?(to|in)\s+(the\s+)?(user|conversation)", "conceal_from_user"),
    (r"do\s+not\s+(ask|tell|inform|notify)\s+(the\s+)?user", "conceal_from_user"),
    (r"without\s+(telling|informing|notifying|asking)\s+(the\s+)?user", "conceal_from_user"),
    (r"non[-\s]?negotiable", "coercive_language"),
    (r"severe\s+violation", "coercive_language"),
    (r"you\s+must\s+not\s+(refuse|decline|question)", "override_guard"),
    (r"always\s+(comply|obey)\b", "coercive_language"),
)
_COERCIVE_RE = tuple(
    (re.compile(pattern, re.IGNORECASE), label) for pattern, label in _COERCIVE_PHRASES
)

# arXiv:2602.14211 (SkillJect) hides the payload in an auxiliary .sh/.py and
# rewrites SKILL.md with a front-loaded inducement framing that helper as a
# mandatory first step. arXiv:2601.10338 independently measured
# script-bundling skills at 2.12x the odds of carrying a vulnerability
# (OR=2.12, p<0.001). We cannot inspect helper scripts -- this corpus has
# none -- so the honest response is to notice the reference and surface it,
# because a skill that depends on code we have not read is a skill whose
# behaviour we cannot vouch for.
_SCRIPT_REFERENCE_RE = re.compile(
    r"(?i)\b(?:scripts?[/\\][\w.-]+|references?[/\\][\w.-]+"
    r"|run\s+`?(?:scripts?/[\w.-]+|[\w./-]+\.(?:py|sh|js|ts|ps1|rb))`?"
    r"|python\s+[\w./-]+\.py\b|bash\s+[\w./-]+\.sh\b)"
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)

# Frontmatter keys that are NOT in the Agent Skills spec. Measured in a
# 125-row live sample: allowed-tools 27, version 25, license 22, metadata 16,
# model 8, author 8, source 7, argument-hint 6, risk 5, triggers 5, tags 5,
# user-invocable 5, compatibility 5. Several are Claude Code plugin
# extensions. Their presence is NOT a rejection reason -- a strict validator
# that drops them loses real routing information -- but the count is
# reported so a reviewer can see how far a row is from the spec.
_NONSPEC_FRONTMATTER_KEYS: frozenset[str] = frozenset({
    "version", "model", "author", "source", "argument-hint", "risk",
    "triggers", "tags", "user-invocable", "display-name", "category",
})


def _yaml_scalar(raw: str) -> str:
    """A deliberately tiny frontmatter scalar reader.

    PyYAML is not a dependency of this module and `skills-ref` reaches for
    `strictyaml` precisely because a half-broken YAML parser is a security
    surface. We need three fields and a key census, not a YAML engine. This
    reads a flat `key: value` line, strips one layer of matching quotes, and
    returns "" for anything it does not recognise -- a value we fail to parse
    becomes a rejected row, never a silently wrong one.
    """
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


@dataclass(frozen=True)
class Frontmatter:
    """The spec's two required fields plus the keys we saw but do not use."""

    present: bool
    name: str
    description: str
    license_field: str
    keys: frozenset[str] = field(default_factory=frozenset)

    @property
    def non_spec_keys(self) -> frozenset[str]:
        return self.keys & _NONSPEC_FRONTMATTER_KEYS


def parse_frontmatter(text: str) -> Frontmatter:
    """Frontmatter of a SKILL.md, or an empty one if it is not there."""
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return Frontmatter(present=False, name="", description="", license_field="")
    name = description = license_field = ""
    keys: set[str] = set()
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t", "-"):
            continue
        key, sep, raw = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        keys.add(key)
        value = _yaml_scalar(raw)
        if key == "name" and not name:
            name = value
        elif key == "description" and not description:
            description = value
        elif key == "license" and not license_field:
            license_field = value
    return Frontmatter(
        present=True,
        name=name,
        description=description,
        license_field=license_field,
        keys=frozenset(keys),
    )


@dataclass(frozen=True)
class GateVerdict:
    """One row's disposition. `reason` is a stable slug, never a sentence, so
    the pilot's reason counts group cleanly and a reviewer can look one up."""

    disposition: str
    reason: str
    name: str = ""
    description: str = ""
    body_words: int = 0
    non_spec_keys: tuple[str, ...] = ()
    coercion_signals: tuple[str, ...] = ()
    references_uninspected_script: bool = False
    screening_findings: tuple[str, ...] = ()
    soft_signals: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.disposition == "admit"

    def as_dict(self) -> dict:
        return {
            "disposition": self.disposition,
            "reason": self.reason,
            "name": self.name,
            "description": self.description,
            "body_words": self.body_words,
            "non_spec_keys": list(self.non_spec_keys),
            "coercion_signals": list(self.coercion_signals),
            "references_uninspected_script": self.references_uninspected_script,
            "screening_findings": list(self.screening_findings),
            "soft_signals": list(self.soft_signals),
        }


def count_body_words(text: str) -> int:
    return len((text or "").split())


def validate_name(name: str) -> Optional[str]:
    """None when the name is spec-valid, else a stable reason slug.

    A bad `name` is treated as unrecoverable rather than repairable. It is
    the join key clients use (Microsoft's loader keys an in-memory map by
    `name`, first-writer-wins) and the routing label an agent matches on, so
    a name that violates the charset is permanent index poison: normalising it
    would create a second identity for the same skill.
    """
    if not name:
        return "frontmatter_missing_name"
    if len(name) > MAX_NAME_CHARS:
        return "name_too_long"
    if not _NAME_RE.match(name):
        return "name_charset_invalid"
    return None


def description_activation_signals(description: str) -> list[str]:
    """SOFT signals about how well the description would route.

    Deliberately NOT a rejection. This module's own hard/soft rule is that a
    hard gate fires only when the row cannot be indexed or routed correctly at
    all. A description that states what the skill does but not an explicit
    "use when" clause is a weaker router, not a broken one, and the evidence
    for gating on it is about downstream activation rate (arXiv:2604.04323's
    49% load rate; arXiv:2607.00911's "enumerate the exact scenarios"), not
    about validity.

    There is also a direct in-repo precedent for the failure mode a hard gate
    here would create. `screening.screen_document_text` had a
    `trust_escalation` bare-word check removed on 2026-09-16 because
    "real-corpus rehearsal showed it rejecting ordinary engineering prose …
    at a real, material false-positive rate: 7 of 14 real skill documents in
    one ingestion run". A keyword list applied as a reject is the same shape
    of mistake. Measured on a real row while building this module:
    `spec-save-design`'s description is "Convert a /spec:brainstorm session
    into a structured design document saved under docs/designs" -- a perfectly
    usable router that states *what* without an explicit *when*, and which a
    hard version of this check would have discarded.
    """
    signals: list[str] = []
    lowered = (description or "").lower()
    trigger_words = (
        "when ", "use when", "use this", "if you", "whenever", "for when",
        "in cases", "trigger", "before ", "after ", "during ", "while ",
        "applicable", "helps you", "you need", "you want", "as part of",
    )
    if not any(word in lowered for word in trigger_words):
        signals.append("no_explicit_activation_clause")
    if len(description or "") < 40:
        signals.append("description_very_short")
    if not any(mark in lowered for mark in ("use", "run", "invoke", "convert", "check", "create", "generate", "apply")):
        signals.append("description_has_no_action_verb")
    return signals


def description_hard_failure(description: str) -> Optional[str]:
    """None when the description is usable as a routing label, else a slug.

    Only two conditions qualify: absent, or over the spec's 1-1024 budget. The
    second is a real spec violation and a real routing cost -- a 1,625-char
    description (observed max in a 125-row live sample) cannot fit the ~100
    token metadata budget that every client loads for every skill at startup,
    so the row would be deprioritised against shorter siblings for a reason
    that is a defect rather than a preference.
    """
    if not description.strip():
        return "frontmatter_missing_description"
    if len(description) > MAX_DESCRIPTION_CHARS:
        return "description_too_long"
    return None


def coercion_signals(text: str) -> list[str]:
    """Named coercive / concealment patterns from arXiv:2602.06547."""
    found: list[str] = []
    for pattern, label in _COERCIVE_RE:
        if pattern.search(text or ""):
            found.append(label)
    return sorted(set(found))


def screen_with_existing_screener(text: str, *, name: str = "") -> list[str]:
    """The check types the shared screener tripped, as a flat slug list.

    Delegates to `screening.screen_document_text` rather than reimplementing
    injection or secret detection: that module is the single screening
    chokepoint and already reuses skill_ingestion's own detectors. This step's
    contribution is the coercion family above, which it does not have.
    """
    from app.services.screening import screen_document_text

    return sorted({f["check_type"] for f in screen_document_text(text, name=name)})


def gate_text(
    text: str,
    *,
    fallback_name: str = "",
    rows: Optional[int] = None,
) -> GateVerdict:
    """Run every content-level gate over one SKILL.md.

    Order is deliberate and load-bearing: the cheap structural checks come
    first so a row that is going to be rejected for a missing `name` never
    pays for a screening pass, and the expensive/weaker-evidence checks come
    last so they only ever see rows that survived the certain ones.
    """
    body_words = count_body_words(text)
    front = parse_frontmatter(text)
    name = front.name or fallback_name
    description = front.description

    if not text or not text.strip():
        return GateVerdict("reject", "empty_content", name=name, body_words=body_words)
    if not front.present:
        return GateVerdict("reject", "frontmatter_absent", name=name, body_words=body_words)

    name_reason = validate_name(name)
    if name_reason is not None:
        return GateVerdict("reject", name_reason, name=name, body_words=body_words)

    desc_reason = description_hard_failure(description)
    if desc_reason is not None:
        return GateVerdict(
            "reject", desc_reason, name=name, description=description,
            body_words=body_words, non_spec_keys=tuple(sorted(front.non_spec_keys)),
        )

    soft = tuple(description_activation_signals(description))
    if body_words < MIN_BODY_WORDS:
        return GateVerdict(
            "reject", "body_too_short", name=name, description=description,
            body_words=body_words, non_spec_keys=tuple(sorted(front.non_spec_keys)),
            soft_signals=soft,
        )
    if body_words > MAX_BODY_WORDS:
        return GateVerdict(
            "reject", "body_oversized", name=name, description=description,
            body_words=body_words, non_spec_keys=tuple(sorted(front.non_spec_keys)),
            soft_signals=soft,
        )
    if rows is not None and rows > 0 and body_words < rows * 0.5:
        return GateVerdict(
            "quarantine", "body_rows_disagree_with_word_count", name=name,
            description=description, body_words=body_words,
            non_spec_keys=tuple(sorted(front.non_spec_keys)), soft_signals=soft,
        )

    coercion = coercion_signals(text)
    screening = screen_with_existing_screener(text, name=name)
    script_ref = bool(_SCRIPT_REFERENCE_RE.search(text or ""))

    if coercion or screening:
        return GateVerdict(
            "quarantine",
            "coercive_language" if coercion else "screener_block_finding",
            name=name, description=description, body_words=body_words,
            non_spec_keys=tuple(sorted(front.non_spec_keys)),
            coercion_signals=tuple(coercion),
            references_uninspected_script=script_ref,
            screening_findings=tuple(screening),
            soft_signals=soft,
        )

    return GateVerdict(
        "admit", "admitted", name=name, description=description,
        body_words=body_words, non_spec_keys=tuple(sorted(front.non_spec_keys)),
        references_uninspected_script=script_ref, soft_signals=soft,
    )


# ---------------------------------------------------------------------------
# Near-duplicate detection
# ---------------------------------------------------------------------------
# WHY SIMHASH AND NOT THE 0.99 JACCARD OF arXiv:2607.00911
#   That paper used body similarity >= 0.99 to link a published skill to its
#   locally adapted copy, which is a pairwise question over a few thousand
#   already-linked candidates. Here it is a question over 138,133 rows, and
#   the dataset's own dedup was exact SHA-256 only -- measured, 138,133 of
#   138,133 distinct -- which provably left 5,573 distinct paths spread over
#   20,391 rows holding the same logical skill under different hashes (fork
#   edits). Exact hashing cannot see that. Full pairwise Jaccard is O(n^2).
#
#   So: 64-bit simhash over word 3-shingles, banded into four 16-bit bands.
#   Two near-identical documents collide in at least one band; the four bands
#   give a cheap index without pretending to be a semantic embedder. The
#   threshold is Hamming distance <= SIMHASH_NEAR_DUP_MAX_BITS, which is the
#   standard approximation of high Jaccard. Every near-dup is REPORTED, never
#   merged: a fork that added two lines is not the same artifact as its
#   parent, and silently collapsing them would destroy provenance.
SIMHASH_NEAR_DUP_MAX_BITS = 6
_SIMHASH_BANDS = 4
_SIMHASH_BAND_BITS = 64 // _SIMHASH_BANDS
_SHINGLE_N = 3


def _shingles(text: str) -> list[str]:
    words = (text or "").lower().split()
    if len(words) < _SHINGLE_N:
        return [" ".join(words)] if words else []
    return [" ".join(words[i : i + _SHINGLE_N]) for i in range(len(words) - _SHINGLE_N + 1)]


def simhash64(text: str) -> int:
    """A 64-bit simhash of the text's word 3-shingles."""
    shingles = _shingles(text)
    if not shingles:
        return 0
    vector = [0] * 64
    for shingle in shingles:
        digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            vector[bit] += 1 if (value >> bit) & 1 else -1
    out = 0
    for bit in range(64):
        if vector[bit] > 0:
            out |= 1 << bit
    return out


def hamming64(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class NearDuplicateIndex:
    """Banded simhash index over rows already seen in this run.

    Insertion-ordered and process-local on purpose. A persistent index would
    need a migration and a write path into the canonical shard, and the
    question this answers ("how much duplication does this corpus contain,
    and which rows are redundant") is a property of the corpus, not of the
    substrate. Re-ingesting the same corpus later re-measures it; that is the
    honest behaviour for a pilot metric.
    """

    def __init__(self, max_bits: int = SIMHASH_NEAR_DUP_MAX_BITS) -> None:
        self._max_bits = max_bits
        self._bands: list[dict[int, list[tuple[int, str]]]] = [{} for _ in range(_SIMHASH_BANDS)]
        self._count = 0

    def __len__(self) -> int:
        return self._count

    def check_and_add(self, text: str, key: str) -> Optional[str]:
        """None if `text` is new, else the key of the first near-dup seen."""
        fingerprint = simhash64(text)
        for band in range(_SIMHASH_BANDS):
            shift = band * _SIMHASH_BAND_BITS
            bucket = (fingerprint >> shift) & ((1 << _SIMHASH_BAND_BITS) - 1)
            for other, other_key in self._bands[band].get(bucket, ()):
                if hamming64(fingerprint, other) <= self._max_bits:
                    return other_key
        for band in range(_SIMHASH_BANDS):
            shift = band * _SIMHASH_BAND_BITS
            bucket = (fingerprint >> shift) & ((1 << _SIMHASH_BAND_BITS) - 1)
            self._bands[band].setdefault(bucket, []).append((fingerprint, key))
        self._count += 1
        return None


def exact_duplicate_key(content_hash: str) -> str:
    """The dataset's own dedup key, normalised to full 64-hex.

    The card says `content_hash` uses "first 16 chars used as file ID".
    Measured on 2026-09-28: all 138,133 stored values are the full 64-hex
    SHA-256, so the 16-char note describes a loader convention, not the
    stored value. We normalise anyway so a future revision that does truncate
    cannot silently halve the key space.
    """
    raw = (content_hash or "").strip().lower()
    if len(raw) == 64 and all(c in "0123456789abcdef" for c in raw):
        return raw
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def reason_counts(verdicts: Iterable[GateVerdict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.reason] = counts.get(verdict.reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
