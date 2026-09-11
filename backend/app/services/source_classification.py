"""
G4 / spec §6 / audit B13 -- structural source-content classification.

A deterministic, DB-free classifier that labels an ingested document's
*content* as one of:

    PROCEDURE  -- ordered, imperative actions the reader is meant to carry
                 out ("how to X", numbered steps of verbs).
    REFERENCE  -- lookup material: API / schema / config / CLI option
                 tables, field lists, "parameters" / "returns" / "see
                 also". Numbered items here are enumerations, not steps.
    CLAIM      -- assertion / rationale / decision prose ("we decided",
                 "because", comparative "X is faster than Y"), no actions.
    MIXED      -- meaningful signal for more than one of the above.
    UNKNOWN    -- not enough signal to say.

This is a SIGNAL recorded with provenance on the Observation, not a hard
gate: `parse_skill_md` already structurally rejects a document with no
step section. What this adds is "this doc HAS numbered items but they are
API response fields, not steps" -- a `REFERENCE` verdict a downstream
policy can act on. It is a heuristic and says so (`confidence`).
"""
from __future__ import annotations

import re
from typing import Iterable

SOURCE_KINDS: tuple[str, ...] = ("PROCEDURE", "REFERENCE", "CLAIM", "MIXED", "UNKNOWN")
SOURCE_CLASSIFIER_VERSION = "source_classifier@v1"

# imperative-verb openings typical of a real step
_STEP_VERB_RE = re.compile(
    r"^\s*(?:\d+[.)]\s*|[-*]\s*)?"
    r"(run|install|create|add|remove|delete|set|configure|open|edit|write|"
    r"copy|move|clone|checkout|commit|push|pull|build|deploy|start|stop|"
    r"restart|enable|disable|update|upgrade|verify|check|ensure|apply|"
    r"generate|export|import|register|call|invoke|send|wait|select|click|"
    r"navigate|choose|paste|replace|rename|drop|grant|revoke)\b",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"\b(parameters?|arguments?|returns?|response fields?|request body|"
    r"query params?|headers?|status codes?|options?:|flags?:|environment "
    r"variables?|schema|data type|default:|enum|see also|api reference|"
    r"endpoint|method signature|attributes?:|properties?:)\b",
    re.IGNORECASE,
)
_CLAIM_RE = re.compile(
    r"\b(we (?:decided|chose|found|believe|concluded)|because|therefore|"
    r"as a result|the reason|trade-?off|rationale|is (?:faster|slower|"
    r"better|worse|more reliable) than|outperforms|we recommend|in our "
    r"experience|empirically|benchmark shows)\b",
    re.IGNORECASE,
)


def _lines(text: str) -> list[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def classify_source_content(
    text: str,
    *,
    name: str = "",
    headings: Iterable[str] = (),
    steps: Iterable[str] = (),
) -> dict:
    """Return
    {"kind": <SOURCE_KINDS>, "confidence": float, "signals": {proc,ref,claim: int},
     "classifier_version": SOURCE_CLASSIFIER_VERSION}.
    Pure. Never raises on empty input (-> UNKNOWN, confidence 0.0)."""
    body = text or ""
    step_list = [s for s in steps if s and s.strip()]
    heading_blob = " ".join(h for h in headings if h)
    hay = "\n".join([body, name or "", heading_blob])

    proc = sum(1 for s in step_list if _STEP_VERB_RE.match(s))
    # a bare numbered/bulleted line in the body that opens with a verb also counts
    proc += sum(1 for ln in _lines(body)
                if re.match(r"^\s*(?:\d+[.)]|[-*])\s", ln) and _STEP_VERB_RE.match(ln))
    ref = len(_REFERENCE_RE.findall(hay))
    claim = len(_CLAIM_RE.findall(hay))

    signals = {"procedure": proc, "reference": ref, "claim": claim}
    ranked = sorted(signals.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top_score = ranked[0]
    second_score = ranked[1][1]

    if top_score == 0:
        # no lexical signal: fall back to "did a step section parse?"
        if step_list:
            return {"kind": "PROCEDURE", "confidence": 0.3, "signals": signals,
                    "classifier_version": SOURCE_CLASSIFIER_VERSION}
        return {"kind": "UNKNOWN", "confidence": 0.0, "signals": signals,
                "classifier_version": SOURCE_CLASSIFIER_VERSION}

    kind_map = {"procedure": "PROCEDURE", "reference": "REFERENCE", "claim": "CLAIM"}
    # meaningful runner-up -> MIXED
    if second_score >= 2 and second_score >= 0.5 * top_score:
        kind = "MIXED"
        confidence = round(min(0.9, 0.4 + 0.05 * (top_score + second_score)), 2)
    else:
        kind = kind_map[top_name]
        confidence = round(min(0.95, 0.45 + 0.08 * top_score - 0.04 * second_score), 2)
        confidence = max(confidence, 0.3)

    return {"kind": kind, "confidence": confidence, "signals": signals,
            "classifier_version": SOURCE_CLASSIFIER_VERSION}
