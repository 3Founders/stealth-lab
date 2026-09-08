"""
Human-facing procedure display metadata (``display_name`` /
``display_description``).

MACHINE IDENTITY vs DISPLAY NAME
-------------------------------
``procedures.name`` is stable machine identity -- a slug like
``mcp-lazy-tool-schema-loading`` -- and is used as a lookup handle in
ingestion dedup, tests, and provenance. It must never change and must
never be used as a UI title.

``display_name`` / ``display_description`` are DERIVED, VERSIONED,
human-facing text. They are not identifiers. This module builds them
deterministically from the procedure's own recorded data (``name`` +
``goal``), which is possible for the overwhelming majority of rows
because ingestion already stores ``goal`` as readable prose (very often
literally "Use when ..."). Rows whose ``goal`` is too thin or too noisy
for a deterministic description fail ``display_metadata_quality`` and are
routed to the ingestion LLM abstraction path
(``skill_ingestion.abstract_display_description``) -- the SAME model call
already used for ``capability_statement``, never a new one, and its
output is still persisted + version-stamped + validated.

DETERMINISTIC. No model, no network, no randomness here.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Optional

# Bump when the deterministic recipe changes; the backfill
# (--display-metadata) then rebuilds every row whose stored
# display_metadata_version differs.
DISPLAY_METADATA_VERSION = "disp_v1"
# Stamped instead when the deterministic path could not produce usable
# text and the LLM fallback also abstained -- these rows are a
# data-quality finding, surfaced, never silently shown as a raw slug.
DISPLAY_METADATA_FALLBACK_VERSION = "disp_v1_deslug_only"

_MAX_NAME_CHARS = 80
_MAX_DESCRIPTION_CHARS = 240
_MIN_DESCRIPTION_CHARS = 16

_WS_RE = re.compile(r"\s+")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__|`)")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)+$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

# Tokens that keep a fixed casing when a slug is expanded into a title.
_ACRONYMS = {
    "mcp": "MCP", "cli": "CLI", "api": "API", "sdk": "SDK", "ai": "AI",
    "ml": "ML", "sql": "SQL", "http": "HTTP", "https": "HTTPS", "json": "JSON",
    "yaml": "YAML", "toml": "TOML", "csv": "CSV", "html": "HTML", "css": "CSS",
    "aws": "AWS", "gcp": "GCP", "gpu": "GPU", "cpu": "CPU", "os": "OS",
    "id": "ID", "ui": "UI", "ux": "UX", "url": "URL", "uri": "URI",
    "npm": "npm", "ci": "CI", "cd": "CD", "pr": "PR", "orm": "ORM",
    "htn": "HTN", "rrf": "RRF", "tms": "TMS", "rls": "RLS", "nvidia": "NVIDIA",
    "aiq": "AI-Q", "openai": "OpenAI", "github": "GitHub", "gitlab": "GitLab",
    "postgres": "Postgres", "k8s": "K8s",
}

# Ingestion boilerplate stripped from the front of a `goal` so the
# description leads with the capability, not "Use this skill when ...".
_GOAL_PREAMBLE_RE = re.compile(
    r"^(?:use\s+this\s+skill\s+when(?:ever)?|use\s+this\s+when|use\s+when(?:ever)?|"
    r"invoke\s+this\s+when|apply\s+this\s+when|trigger\s+(?:this\s+)?(?:for|when)|"
    r"this\s+skill\s+(?:is\s+used\s+to|will|helps?\s+you)|use\s+for)\b[\s:,-]*",
    re.IGNORECASE,
)


_DROP_CATEGORIES = frozenset({"Cf", "Co", "Cs", "Cn", "So"})


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _MD_EMPHASIS_RE.sub("", text)
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(" ")
        elif ch < " " or ch == "�" or unicodedata.category(ch) in _DROP_CATEGORIES:
            continue
        else:
            out.append(ch)
    return _WS_RE.sub(" ", "".join(out)).strip()


def _titlecase_token(token: str) -> str:
    low = token.lower()
    if low in _ACRONYMS:
        return _ACRONYMS[low]
    if len(token) <= 3 and token.isupper():
        return token
    return token[:1].upper() + token[1:] if token else token


def build_display_name(proc: Mapping[str, Any]) -> str:
    """A concise, readable title derived from the machine ``name``.

    A slug is expanded to words with sensible acronym casing. An
    already-human name is normalized and title-cased only if it was
    entirely lowercase. Never returns an empty string as long as ``name``
    is set.
    """
    raw = _norm(proc.get("name"))
    if not raw:
        return ""
    if _SLUG_RE.match(raw):
        words = re.split(r"[-_\s]+", raw)
        title = " ".join(_titlecase_token(w) for w in words if w)
    elif raw == raw.lower():
        title = " ".join(_titlecase_token(w) for w in raw.split(" "))
    else:
        title = raw
    return title[:_MAX_NAME_CHARS].rstrip()


def build_display_description(proc: Mapping[str, Any]) -> str:
    """One or two sentences of capability-first prose derived from ``goal``.

    Strips ingestion preamble ("Use this skill when ...") so the sentence
    leads with what the procedure does. Falls back to the first sentence
    of ``capability_statement`` when present. Returns "" when nothing
    usable can be produced -- the caller then decides (LLM fallback or a
    surfaced data-quality error).
    """
    source = _norm(proc.get("capability_statement")) or _norm(proc.get("goal"))
    if not source:
        return ""
    stripped = _GOAL_PREAMBLE_RE.sub("", source).strip()
    if stripped and stripped[0].islower():
        stripped = stripped[0].upper() + stripped[1:]
    if not stripped:
        stripped = source
    sentences = _SENTENCE_SPLIT_RE.split(stripped)
    out = sentences[0].strip()
    if len(out) < _MIN_DESCRIPTION_CHARS and len(sentences) > 1:
        out = (out + " " + sentences[1].strip()).strip()
    if len(out) > _MAX_DESCRIPTION_CHARS:
        cut = out.rfind(" ", 0, _MAX_DESCRIPTION_CHARS)
        out = out[: cut if cut > _MAX_DESCRIPTION_CHARS * 0.6 else _MAX_DESCRIPTION_CHARS].rstrip()
        out = out.rstrip(".,;:") + "…"
    return out


def _looks_like_step_fragment(text: str) -> bool:
    """A `goal` that is actually a numbered/bulleted instruction or a
    red-flag checklist line, not a capability statement."""
    low = text.lower()
    return (
        bool(re.match(r"^\s*(?:step\s*\d|[-*•]|\d+[.)])", text))
        or low.startswith(("never ", "do not ", "always ", "don't "))
        or text.count(" ") < 2
    )


def display_metadata_quality(display_name: str, display_description: str) -> Optional[str]:
    """Return a data-quality error string if the produced metadata is not
    fit to show a user; ``None`` when it is fine.

    Used by the ingestion contract (a procedure may not enter the corpus
    with unusable display metadata) and by the backfill (decide whether to
    fall back to the LLM path).
    """
    if not display_name or _SLUG_RE.match(display_name.lower().replace(" ", "-")) and " " not in display_name:
        return "display_name is empty or still a raw slug"
    if not display_description:
        return "display_description is empty"
    if len(display_description) < _MIN_DESCRIPTION_CHARS:
        return f"display_description too short ({len(display_description)} chars)"
    if _looks_like_step_fragment(display_description):
        return "display_description looks like a step/checklist fragment, not a capability"
    return None


def build_display_metadata(proc: Mapping[str, Any]) -> tuple[str, str, Optional[str]]:
    """Convenience: ``(display_name, display_description, quality_error)``.

    ``quality_error`` is ``None`` when the deterministic result is usable.
    A non-``None`` value tells the caller to try the LLM fallback and, if
    that also abstains, to record ``DISPLAY_METADATA_FALLBACK_VERSION`` and
    surface the finding.
    """
    name = build_display_name(proc)
    description = build_display_description(proc)
    return name, description, display_metadata_quality(name, description)
