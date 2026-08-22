"""
Validator chain for extracted procedures (memory-substrate map). All
rules run, all failures collected -- unlike applicability.py's
DELIBERATE short-circuit (a non-compensatory filter only needs to
confirm ONE violation exists), an author reviewing a rejected extraction
wants every problem at once, not one at a time across five retries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.services.procedure_extraction.schema import ExtractedProcedure
from app.services.slot_binders import known_binder_names


@dataclass
class ValidationFailure:
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.message}"


Rule = Callable[[ExtractedProcedure, "ValidationContext"], list[ValidationFailure]]


@dataclass
class ValidationContext:
    """
    Everything a rule needs beyond the procedure itself. `probe_vocabulary`
    is REQUIRED, not defaulted from environment_probe.PROBE_PREDICATE_
    VOCABULARY here -- a caller must pass it explicitly (this module does
    not import environment_probe) so V1 can never silently validate
    against a stale import-time snapshot of a vocabulary that's meant to
    be looked up fresh. evidence_tokens is the set of concrete strings
    (file paths, symbol names) V4 scans capability_statement against.
    """
    probe_vocabulary: tuple[str, ...]
    evidence_tokens: frozenset[str]
    allowed_binders: frozenset[str]


def v1_precondition_groundedness(
    proc: ExtractedProcedure, ctx: ValidationContext,
) -> list[ValidationFailure]:
    """
    HIGHEST-VALUE RULE IN THE MODULE. check_hard_constraints() evaluates
    preconditions fail-closed under CWA -- "no claim found" and
    "precondition unsatisfied" are the same answer
    (applicability.py:179-201) -- so a precondition naming a predicate
    nothing ever asserts is PERMANENTLY unsatisfiable, and the procedure
    silently never matches. This rule is what prevents building a system
    that looks correct and never fires.
    """
    failures = []
    for p in proc.preconditions:
        if p.predicate not in ctx.probe_vocabulary:
            failures.append(ValidationFailure(
                "V1_precondition_groundedness",
                f"precondition predicate {p.predicate!r} is not in the environment probe's "
                f"vocabulary {ctx.probe_vocabulary} -- this precondition can never be satisfied "
                f"(project_state() will never return a claim for it), making the procedure "
                f"permanently unmatchable",
            ))
    return failures


def v2_step_purity(proc: ExtractedProcedure, ctx: ValidationContext) -> list[ValidationFailure]:
    """
    Structural guarantee, checked here as a real regression rather than
    trusted from ProcedureStep having no deps/requires field: ticket 05,
    steps are planner-neutral, scheduling belongs to the executor at
    instantiation. Invariant 6 ("procedure independent of execution
    implementation"), enforced mechanically, not by convention.
    """
    failures = []
    for step in proc.steps:
        if hasattr(step, "deps") or hasattr(step, "requires"):
            failures.append(ValidationFailure(
                "V2_step_purity",
                f"step {step.order} carries a deps/requires attribute -- steps must be "
                f"planner-neutral; scheduling is the executor's job at instantiation",
            ))
    return failures


def v3_slot_integrity(proc: ExtractedProcedure, ctx: ValidationContext) -> list[ValidationFailure]:
    """Every {slot} a step references must be declared, and every
    declared slot must name a binder that both EXISTS in the registry
    and is on this extractor's own allowed_binders list -- a
    well-formed-but-unpermitted binder is still a real failure, not
    silently accepted."""
    failures = []
    declared = {s.name for s in proc.slots}
    referenced: set[str] = set()
    for step in proc.steps:
        # Every string-bearing field, not just `action`. A {slot}
        # placeholder is equally real inside a step's goal or its
        # inputs, and scanning only `action` would let an undeclared slot
        # through silently -- the exact failure this rule exists to
        # catch, just via a field that was added later.
        for text in _step_texts(step):
            referenced |= {tok.strip("{}") for tok in _extract_braces(text)}

    for name in referenced - declared:
        failures.append(ValidationFailure(
            "V3_slot_integrity", f"step references {{{name}}} but no matching slot is declared",
        ))
    known = set(known_binder_names())
    for slot in proc.slots:
        if slot.binder not in known:
            failures.append(ValidationFailure(
                "V3_slot_integrity",
                f"slot {slot.name!r} names binder {slot.binder!r}, which is not registered "
                f"(known binders: {sorted(known)})",
            ))
        elif slot.binder not in ctx.allowed_binders:
            failures.append(ValidationFailure(
                "V3_slot_integrity",
                f"slot {slot.name!r} names binder {slot.binder!r}, which is not in this "
                f"extractor's allowed_binders {sorted(ctx.allowed_binders)}",
            ))
    return failures


def _step_texts(step) -> list[str]:
    """Every free-text string on a step that could carry a {slot}
    placeholder. Values inside allowed_implementations are included
    because a tool argument is exactly where a bound slot would appear
    once steps carry real tool bindings."""
    texts = [step.action]
    if step.goal:
        texts.append(step.goal)
    texts.extend(step.inputs)
    texts.extend(step.expected_outputs)
    for impl in step.allowed_implementations:
        if isinstance(impl, dict):
            texts.extend(str(v) for v in impl.values() if isinstance(v, str))
    return texts


def _extract_braces(text: str) -> list[str]:
    out, depth, start = [], 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
            if depth == 1:
                start = i
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start:i + 1])
    return out


def v4_capability_abstraction(
    proc: ExtractedProcedure, ctx: ValidationContext,
) -> list[ValidationFailure]:
    """
    capability_statement is the field retrieve_local_first-style
    cross-domain matching embeds -- if it names a concrete file/symbol
    from THIS episode, it embeds nowhere near a different domain's task,
    defeating the entire point. Checked mechanically: any evidence token
    (a real path or name from this episode's own observations)
    appearing verbatim in the statement is a real failure, not a style
    nit.
    """
    failures = []
    statement_lower = proc.capability_statement.lower()
    for token in ctx.evidence_tokens:
        if len(token) >= 4 and token.lower() in statement_lower:
            failures.append(ValidationFailure(
                "V4_capability_abstraction",
                f"capability_statement contains {token!r}, a concrete identifier drawn from "
                f"this episode's own evidence -- it must describe the ABSTRACT capability, "
                f"not name specifics that won't recur in a different task",
            ))
    return failures


def v5_evidence_sufficiency(
    proc: ExtractedProcedure, ctx: ValidationContext,
) -> list[ValidationFailure]:
    """Structural check on the PROCEDURE, not the evidence itself --
    extract_procedure() (the caller) is responsible for refusing to even
    attempt extraction from a failed/observation-free episode (that's a
    precondition on calling a strategy at all, not a post-hoc property
    of its output). This rule catches the degenerate case where a
    strategy nonetheless produced an empty-content procedure."""
    failures = []
    if not proc.steps:
        failures.append(ValidationFailure(
            "V5_evidence_sufficiency", "procedure has no steps -- nothing to extract",
        ))
    if not proc.capability_statement.strip():
        failures.append(ValidationFailure(
            "V5_evidence_sufficiency", "capability_statement is empty",
        ))
    return failures


ALL_RULES: tuple[Rule, ...] = (
    v1_precondition_groundedness,
    v2_step_purity,
    v3_slot_integrity,
    v4_capability_abstraction,
    v5_evidence_sufficiency,
)


def validate(proc: ExtractedProcedure, ctx: ValidationContext) -> list[ValidationFailure]:
    """Runs every rule, collects every failure -- see module docstring
    for why this deliberately does not short-circuit."""
    failures: list[ValidationFailure] = []
    for rule in ALL_RULES:
        failures.extend(rule(proc, ctx))
    return failures
