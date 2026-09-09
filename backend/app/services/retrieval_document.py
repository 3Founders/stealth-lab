"""
Canonical procedure *retrieval representation* (retrieval contract, not a
storage type).

WHY THIS EXISTS
---------------
Before this module, the text embedded for a procedure was built two
different ways in two places and neither carried much of the procedure:

  - ``skill_ingestion.compile_skill_artifact`` embedded
    ``capability_statement or goal`` + ``"Workflow:"`` + step goals.
  - ``skill_ingestion.ingest_skill_md`` embedded the bare ``goal``.
  - ``scripts/backfill_procedure_embeddings.py`` re-implemented the first
    of those a third time.

None included when-to-use text, tools, dependencies, domain, constraints
or failure conditions -- all of which are real columns / ``domain_payload``
keys on the row. A search embedding built from a one-line goal cannot
distinguish "rotate a Postgres connection secret" from "rotate an AWS IAM
key", so retrieval surfaces semantically-adjacent-but-useless matches.

This module builds ONE canonical, deterministic, normalized, versioned,
inspectable text for a procedure version. That text is what gets embedded
(semantic leg) and what feeds ``to_tsvector`` (lexical leg). It is part of
the retrieval contract: change the recipe -> bump
``RETRIEVAL_DOCUMENT_VERSION`` -> the backfill re-embeds every row whose
stored version is older.

DELIBERATE EXCLUSIONS
--------------------
Volatile / bookkeeping fields never enter the document, because they carry
no semantic signal and would make "same content -> same text" false:
row ids, ``procedure_id``, ``family_id``, every ``t_*`` timestamp,
``evidence_refs``, ``source_episode_ids``, ``verification_stats``,
``embedding*`` columns, ``provenance``, ``created_by`` / ``owner_id`` /
``approved_by``, ``domain_payload['source']`` (the raw provenance dict),
``domain_payload['embedding']`` (self-referential), ``domain_payload
['resource_manifest']`` (file hashes/sizes).

DETERMINISM RULES
-----------------
* every collection is ``sorted()`` before rendering (no dict/set iteration
  order leaks in);
* no timestamps, no random, no ``now()``;
* whitespace and control characters are normalized identically every call;
* sections appear in a fixed order and are omitted (not blank) when empty;
* per-section caps keep one huge field (a 69-step tutorial) from drowning
  the rest.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable, Mapping, Optional, Sequence

# Bump on ANY change to the recipe below. The backfill
# (scripts/backfill_procedure_embeddings.py --representation) re-embeds
# every live procedure whose stored retrieval_document_version differs.
#
#   procdoc_v1 (2026-09-08): Name / Purpose / When to use / Domain / Steps /
#     Tools / Depends on / Constraints / Fails when.
#   procdoc_v2 (2026-09-09): + "When not to use" (scope/exclusions +
#     domain_payload.when_not_to_use), + "Expected outcome" (postconditions
#     + expected_effects; moved out of Constraints), "Purpose" also reads
#     domain_payload.purpose, "When to use" also renders required_state,
#     and _norm() now strips Markdown link syntax / autolinks / bare URLs /
#     relative file paths from every rendered field. Materially richer text
#     -> every procdoc_v1 row is stale and owed a re-embed.
RETRIEVAL_DOCUMENT_VERSION = "procdoc_v2"

# Stamped on a row whose embedding was produced OUTSIDE this recipe (a
# local-procedure publish, a one-off seed that pre-computed a vector). The
# canonical document is still built and stored for inspection, but the
# --representation backfill treats anything other than
# RETRIEVAL_DOCUMENT_VERSION as owing a re-embed.
RETRIEVAL_DOCUMENT_IMPORT_VERSION = "import_pending_reembed"

# Per-section budgets. Chosen so a pathological row (a 69-step onboarding
# tutorial, a 4 KB compatibility paragraph) cannot dominate the vector --
# not because the exact numbers are meaningful. Applied after
# normalization, measured in characters.
_MAX_STEPS = 40
_MAX_STEPS_CHARS = 4000
_MAX_PURPOSE_CHARS = 1200
_MAX_WHEN_CHARS = 1500
_MAX_CONSTRAINTS_CHARS = 1500
_MAX_FAILURE_CHARS = 1200
_MAX_TOOLS = 40
_MAX_DEPENDENCIES = 40

_WS_RE = re.compile(r"\s+")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|`)")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)+$")

# Markdown / link / path noise carries no retrieval signal and pollutes both
# the vector and the tsvector. Stripped in _norm() before emphasis markers.
#   [Qdrant Console](https://cloud.qdrant.io/) -> Qdrant Console
#   <https://x>, bare https://x           -> dropped
#   ../../../qdrant-monitoring/SKILL.md    -> dropped
_MD_LINK_RE = re.compile(r"\[([^\]\n]*?)\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
_AUTOLINK_RE = re.compile(r"<https?://[^>\s]+>")
_BARE_URL_RE = re.compile(r"https?://\S+")
_REL_PATH_RE = re.compile(r"(?<![\w.])\.{1,2}/[\w./\-]+")


# Unicode categories dropped from retrieval text: they carry ~no retrieval
# signal and would let a stray emoji / mojibake byte make "same procedure
# content -> same document" false. Cf format, Co private-use, Cs surrogate,
# Cn unassigned, So other-symbol (emoji, ✓, ⛔, ...). Sm/Sc (math/currency)
# and ordinary punctuation are kept.
_DROP_CATEGORIES = frozenset({"Cf", "Co", "Cs", "Cn", "So"})


def _norm(value: Any) -> str:
    """Normalize a scalar to a single clean line.

    NFC unicode, strip markdown emphasis markers, drop control chars, the
    replacement char and symbol/format noise, collapse every whitespace
    run to one space, trim. Identical output for identical input, always --
    this is what makes the whole document stable.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _AUTOLINK_RE.sub(" ", text)
    text = _BARE_URL_RE.sub(" ", text)
    text = _REL_PATH_RE.sub(" ", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    out = []
    for ch in text:
        if ch == "\n" or ch == "\t":
            out.append(" ")
        elif ch < " " or ch == "�":
            continue
        elif unicodedata.category(ch) in _DROP_CATEGORIES:
            continue
        else:
            out.append(ch)
    return _WS_RE.sub(" ", "".join(out)).strip()


def _deslug(name: str) -> str:
    """``mcp-lazy-tool-schema-loading`` -> ``mcp lazy tool schema loading``.

    Only rewrites a string that is unambiguously a slug (all lowercase,
    hyphen/underscore separated); anything already human-written passes
    through untouched.
    """
    n = _norm(name)
    if _SLUG_RE.match(n):
        return n.replace("-", " ").replace("_", " ")
    return n


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # Clip on a word boundary so the vector never sees a half token.
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit * 0.6 else limit].rstrip()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _step_text(step: Any) -> str:
    if isinstance(step, Mapping):
        for key in ("goal", "description", "action", "name", "title", "text"):
            if step.get(key):
                return _norm(step[key])
        return ""
    return _norm(step)


def _render_preconditions(preconditions: Iterable[Any]) -> list[str]:
    """A structured precondition -> one plain clause. Best-effort: unknown
    shapes are skipped rather than dumped as JSON."""
    out: list[str] = []
    for pc in preconditions:
        if not isinstance(pc, Mapping):
            text = _norm(pc)
            if text:
                out.append(text)
            continue
        subject = _norm(pc.get("subject"))
        predicate = _norm(pc.get("predicate"))
        obj = _norm(pc.get("object"))
        if subject and predicate:
            clause = f"requires {subject} {predicate}"
            if obj and obj.lower() != "none":
                clause += f" {obj}"
            out.append(clause)
        elif pc.get("expr") or pc.get("description"):
            out.append(_norm(pc.get("expr") or pc.get("description")))
    return out


def _render_constraints(proc: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for inv in _as_list(proc.get("invariants")):
        if isinstance(inv, Mapping):
            expr = _norm(inv.get("expr") or inv.get("description"))
            kind = _norm(inv.get("kind"))
            if expr:
                out.append(f"{kind}: {expr}" if kind else expr)
        else:
            text = _norm(inv)
            if text:
                out.append(text)
    payload = proc.get("domain_payload")
    if isinstance(payload, Mapping):
        compat = _norm(payload.get("compatibility"))
        if compat:
            out.append(compat)
    return out


def _render_expected_outcome(proc: Mapping[str, Any]) -> list[str]:
    """What a successful application should produce -- postconditions and
    expected_effects, rendered as plain result clauses (no ``ensures``
    prefix; this is its own section now, not a Constraints sub-line)."""
    out: list[str] = []
    for post in _as_list(proc.get("postconditions")):
        if isinstance(post, Mapping):
            text = _norm(post.get("description") or post.get("expr") or post.get("predicate"))
        else:
            text = _norm(post)
        if text:
            out.append(text)
    for eff in _as_list(proc.get("expected_effects")):
        if isinstance(eff, Mapping):
            text = _norm(eff.get("description") or eff.get("effect") or eff.get("expr"))
        else:
            text = _norm(eff)
        if text:
            out.append(text)
    return out


def _render_exclusions(proc: Mapping[str, Any]) -> list[str]:
    """"Do not use this when ..." -- source-authored non-applicability.
    Reads procedures.exclusions (list) plus domain_payload.when_not_to_use
    (skill-md prose). Never inferred: only what the source actually said."""
    out: list[str] = []
    for ex in _as_list(proc.get("exclusions")):
        if isinstance(ex, Mapping):
            text = _norm(ex.get("description") or ex.get("expr") or ex.get("reason"))
        else:
            text = _norm(ex)
        if text:
            out.append(text)
    payload = proc.get("domain_payload")
    if isinstance(payload, Mapping):
        wnt = _norm(payload.get("when_not_to_use"))
        if wnt and wnt.lower() not in ("none", "null"):
            out.append(wnt)
    # exclusions and domain_payload.when_not_to_use are frequently the same
    # source prose stored twice (skill ingestion writes both) -- collapse.
    seen: set[str] = set()
    deduped: list[str] = []
    for clause in out:
        for piece in (p.strip() for p in clause.split(";")):
            low = piece.lower()
            if piece and low not in seen:
                seen.add(low)
                deduped.append(piece)
    return deduped


def _render_failure_conditions(proc: Mapping[str, Any]) -> list[str]:
    """Failure conditions are included ONLY when they materially bound
    applicability -- i.e. they name a triggering condition, not just a
    generic 'it can fail' note."""
    out: list[str] = []
    for fc in _as_list(proc.get("failure_conditions")):
        if isinstance(fc, Mapping):
            when = _norm(fc.get("when") or fc.get("condition") or fc.get("trigger"))
            what = _norm(fc.get("description") or fc.get("failure") or fc.get("effect"))
            if when:
                out.append(f"{when}: {what}" if what else when)
            elif what and len(what) > 12:
                out.append(what)
        else:
            text = _norm(fc)
            if len(text) > 12:
                out.append(text)
    return out


def _tool_requirements(proc: Mapping[str, Any]) -> list[str]:
    payload = proc.get("domain_payload")
    tools: list[str] = []
    if isinstance(payload, Mapping):
        tools = [_norm(t) for t in _as_list(payload.get("tool_requirements")) if _norm(t)]
    tools += [_norm(t) for t in _as_list(proc.get("tool_requirements")) if _norm(t)]
    return sorted({t for t in tools if t})


def _dependency_refs(proc: Mapping[str, Any], dependencies: Optional[Sequence[Any]]) -> list[str]:
    """Human-facing dependency names only. A resolved target row id carries
    no semantic signal; the *reference* ("research", "aiq-deploy") does."""
    raw: list[Any] = list(dependencies or [])
    if not raw:
        payload = proc.get("domain_payload")
        if isinstance(payload, Mapping):
            raw = _as_list(payload.get("dependencies"))
    names: list[str] = []
    for dep in raw:
        if isinstance(dep, Mapping):
            ref = dep.get("dependency_ref") or dep.get("reference") or dep.get("target_skill_path")
        else:
            ref = dep
        # NOTE: not _norm() here -- this function does its OWN path parsing
        # (../aiq-deploy/SKILL.md -> aiq-deploy) and _norm's relative-path
        # stripping would erase the very thing being parsed. The final
        # _deslug() below normalises the extracted leaf.
        ref = "" if ref is None else str(ref).strip()
        if not ref:
            continue
        # ../aiq-deploy/SKILL.md -> aiq-deploy ; skill:foo -> foo
        ref = ref.split(":", 1)[-1].strip()
        parts = [p for p in re.split(r"[\\/]+", ref) if p and p.lower() != "skill.md"]
        if parts:
            ref = parts[-1]
        ref = re.sub(r"\.(md|py|sh|ya?ml|json|toml)$", "", ref, flags=re.IGNORECASE)
        if ref:
            names.append(_deslug(ref))
    return sorted(set(names))[:_MAX_DEPENDENCIES]


def _when_to_use(proc: Mapping[str, Any], purpose: str) -> str:
    parts: list[str] = []
    payload = proc.get("domain_payload")
    if isinstance(payload, Mapping):
        aw = _norm(payload.get("applies_when"))
        if aw and aw.lower() not in ("none", "null"):
            parts.append(aw)
    goal = _norm(proc.get("goal"))
    # Many rows' `goal` is itself "Use when ..." trigger prose. Fold it in
    # here when it adds something the Purpose line didn't already say.
    if goal and goal != purpose and re.match(r"(?i)\b(use|apply|trigger|invoke|when)\b", goal):
        parts.append(goal)
    parts += _render_preconditions(_as_list(proc.get("preconditions")))
    required_state = proc.get("required_state")
    if isinstance(required_state, Mapping) and required_state:
        kv = "; ".join(
            f"{_norm(k)} {_norm(v)}".strip()
            for k, v in sorted(required_state.items(), key=lambda kv: str(kv[0]))
            if _norm(k)
        )
        if kv:
            parts.append(f"requires state {kv}")
    seen: set[str] = set()
    deduped = [p for p in parts if not (p.lower() in seen or seen.add(p.lower()))]
    return _clip(" ".join(deduped), _MAX_WHEN_CHARS)


def _steps_block(proc: Mapping[str, Any]) -> str:
    raw_steps = _as_list(proc.get("steps"))
    rendered: list[str] = []
    seen: set[str] = set()
    for step in raw_steps:
        text = _step_text(step)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        rendered.append(text)
        if len(rendered) >= _MAX_STEPS:
            break
    numbered = [f"{i}. {t}" for i, t in enumerate(rendered, 1)]
    return _clip(" ".join(numbered), _MAX_STEPS_CHARS)


def build_procedure_retrieval_document(
    proc: Mapping[str, Any],
    *,
    dependencies: Optional[Sequence[Any]] = None,
) -> str:
    """Build the canonical retrieval text for one procedure version row.

    ``proc`` is any mapping with the ``procedures`` column names (an
    asyncpg ``Record`` or a plain dict). ``dependencies`` optionally
    supplies resolved rows from ``procedure_dependencies`` when the caller
    has them; otherwise ``domain_payload['dependencies']`` is used.

    Returns a section-labelled plain-text block. Deterministic: identical
    procedure content always yields byte-identical output.
    """
    payload = proc.get("domain_payload") if isinstance(proc.get("domain_payload"), Mapping) else {}
    name = _deslug(_norm(proc.get("display_name") or proc.get("name")))
    purpose = _clip(
        _norm(
            proc.get("capability_statement")
            or payload.get("purpose")
            or proc.get("goal")
            or proc.get("name")
        ),
        _MAX_PURPOSE_CHARS,
    )

    sections: list[tuple[str, str]] = []
    if name:
        sections.append(("Name", name))
    if purpose:
        sections.append(("Purpose", purpose))

    when = _when_to_use(proc, purpose)
    if when:
        sections.append(("When to use", when))

    not_when = _render_exclusions(proc)
    if not_when:
        sections.append(("When not to use", _clip("; ".join(not_when), _MAX_WHEN_CHARS)))

    domain = _norm(proc.get("domain"))
    if domain:
        sections.append(("Domain", domain))

    steps = _steps_block(proc)
    if steps:
        sections.append(("Steps", steps))

    tools = _tool_requirements(proc)[:_MAX_TOOLS]
    if tools:
        sections.append(("Tools", ", ".join(tools)))

    deps = _dependency_refs(proc, dependencies)
    if deps:
        sections.append(("Depends on", ", ".join(deps)))

    constraints = _render_constraints(proc)
    if constraints:
        sections.append(("Constraints", _clip("; ".join(constraints), _MAX_CONSTRAINTS_CHARS)))

    outcome = _render_expected_outcome(proc)
    if outcome:
        sections.append(("Expected outcome", _clip("; ".join(outcome), _MAX_CONSTRAINTS_CHARS)))

    failures = _render_failure_conditions(proc)
    if failures:
        sections.append(("Fails when", _clip("; ".join(failures), _MAX_FAILURE_CHARS)))

    return "\n".join(f"{label}: {body}" for label, body in sections)


def retrieval_document_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Presentation helpers (plan Part 7 / Part 13 / Part 14). Deterministic,
# built only from the procedure's own recorded structure -- never an
# invented claim, never a query-time model call.
# --------------------------------------------------------------------------


def build_applicability_summary(proc: Mapping[str, Any]) -> Optional[str]:
    """One "when to use" line for the search card / detail page: the
    procedure's own ``applies_when`` prose plus any trigger-shaped goal
    text plus its rendered preconditions. ``None`` when the procedure
    records nothing about when it applies."""
    purpose = _norm(proc.get("capability_statement") or proc.get("goal") or "")
    text = _when_to_use(proc, purpose)
    return text or None


def build_failure_modes(proc: Mapping[str, Any]) -> list[str]:
    """Known failure conditions as short human-readable lines, or []."""
    return _render_failure_conditions(proc)
