"""
Deterministic Implementation goal/verification-contract classification --
the founder directive's own §10 rule applied literally: "parse
deterministically where possible... never use an LLM to rediscover
deterministic structure unnecessarily."

SCOPE, stated honestly (see migration 80's own header for the full
rationale): this module classifies a bundled skill-package SCRIPT resource
from its filename alone -- a narrow, real, keyword-based heuristic, not a
semantic decomposition pass. It never calls a model. A resource whose
filename carries no recognizable signal gets `classification="unclassified"`
and `goal=None` -- never a guessed goal, per the directive's own "do not
fabricate" rule (§15). A real LLM-driven semantic decomposition pass (goal
inference from the script's actual CONTENT, expected_outcome, etc.) is
explicitly out of scope for this pass -- `classification="llm_classified"`
is reserved for it and nothing in this codebase sets it yet.

`expected_outcome` is not computed here at all: a filename alone gives no
real evidence of a script's intended post-state, and this module never
fabricates one (same discipline `procedure_extraction`'s
ExtractedProcedure already enforces for capability_statement).
"""
from __future__ import annotations

import re
from typing import Optional

# Ordered: first match wins, so a name matching multiple keywords (rare)
# gets a stable, deterministic classification rather than one that depends
# on dict iteration order. Kept intentionally small and literal -- this is
# the "keep goal types broad enough for contextual ranking later, do not
# encode environment into the goal name" vocabulary the directive asks for,
# not an attempt at completeness.
_GOAL_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\btest"), "test_execution"),
    (re.compile(r"\b(check|verify|validate)"), "verification"),
    (re.compile(r"\b(scan|audit|lint)"), "static_analysis"),
    (re.compile(r"\bmigrat"), "schema_migration"),
    (re.compile(r"\b(deploy|release)"), "deployment"),
    (re.compile(r"\bbuild"), "build"),
    (re.compile(r"\b(generate|codegen|scaffold)"), "code_generation"),
    (re.compile(r"\b(backfill|reindex)"), "data_backfill"),
)

# The founder directive's own §7 vocabulary. `human`/`llm`/`external_system`
# have no real deterministic writer in this codebase yet -- only
# `deterministic` (an arbitrary script/CLI, the ONLY kind this module's
# real caller, _persist_package_relations, ever creates) is populated here.
VERIFICATION_CONTRACT_TYPES: tuple[str, ...] = (
    "deterministic", "test", "external_system", "llm", "human",
)


_SEPARATOR_RE = re.compile(r"[_\-.]")


def normalize_goal_from_path(resource_path: str) -> Optional[str]:
    """A real, narrow, deterministic classification from a resource's own
    path -- never a guess. Matches against the basename only (directory
    segments like `scripts/` carry no goal signal, per the directive's own
    "do not encode environment into the goal name" rule). Separators
    (`_`, `-`, `.`) are normalized to spaces first so `\\b` finds a real
    word boundary at `run_tests.py` -> `run tests py`, not just at the
    string's own start/end -- `_`/`-`/`.` are word-adjacent to `\\w` in
    Python's regex engine, so `\\btest` alone would miss "run_tests.py"."""
    basename = resource_path.rsplit("/", 1)[-1].lower()
    normalized = _SEPARATOR_RE.sub(" ", basename)
    for pattern, goal in _GOAL_PATTERNS:
        if pattern.search(normalized):
            return goal
    return None


def default_verification_contract(kind: str) -> Optional[dict]:
    """The one real, non-fabricated default this module can offer: a
    `kind='deterministic'` Implementation (an arbitrary script/CLI
    invocation) has, BY CONSTRUCTION of that kind, exactly one universally
    true success signal -- it ran without erroring. This is not a guess
    about what the script DOES; it is the honest floor every deterministic
    mechanism already satisfies. Any richer contract (a specific exit code
    meaning, a file it must produce) needs real evidence this module does
    not have and therefore never invents."""
    if kind == "deterministic":
        return {"type": "deterministic", "check": "exit_code_zero"}
    return None


def classify_skill_package_script(resource_path: str, *, kind: str = "deterministic") -> dict:
    """The one real entry point `_persist_package_relations()` calls.
    Returns exactly the four new `implementations` columns (migration 80)
    for one bundled script resource -- `expected_outcome` always None here,
    per this module's own docstring."""
    goal = normalize_goal_from_path(resource_path)
    return {
        "goal": goal,
        "goal_spec": None,
        "expected_outcome": None,
        "verification_contract": default_verification_contract(kind),
        "classification": "heuristic" if goal else "unclassified",
    }
