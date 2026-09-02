"""Ablation arm configuration (evaluation-suite Phase 7, spec section 25).

DECLARATIVE ONLY -- config describing what each arm adds, not working
agent implementations. Per an explicit user scope decision for this whole
evaluation-suite effort: no live runs execute this pass. Wiring one of
these configs to a real running arm (a new AgentAdapter subclass honoring
the flags below) is separate future work, gated on an explicit go-ahead
given the real API spend involved.

Spec section 25 asks for the SMALLEST SENSIBLE MATRIX, not a full
combinatorial sweep. This harness's existing A/B/C arms (scripted_arms.py,
openrouter_arms.py) already ARE most of that matrix's low end:

  A = frontier agent from scratch                    (spec's ablation "A")
  B = A + conventional/unverified memory (RAG)        (a DIFFERENT axis --
                                                        not on spec's A-G
                                                        ladder, kept as the
                                                        baseline-vs-Stealth
                                                        THIRD arm, not an
                                                        ablation step)
  C = A + retrieval + applicability + procedure reuse (spec's ablation
                                                        "B"+"C"+"D" collapsed
                                                        into one arm, since
                                                        this harness's C
                                                        already gates every
                                                        reuse through a real
                                                        check_applicability
                                                        call before crediting
                                                        it -- see
                                                        RealProcedureAgent/
                                                        VerifiedProcedureAgent)

So the genuinely NEW capability steps beyond existing C, in spec's own
order, are:

  E = C + decomposition
        (the task is broken into subgoals before retrieval/applicability
        run per-subgoal, rather than once for the whole task -- no
        decomposition step exists anywhere in this harness today)
  F = C + implementation selection
        (procedure reuse resolves to a SPECIFIC bound implementation via
        backend/app/execution/implementation_registry.py's real
        resolve()/bind semantics, rather than crediting reuse of the
        procedure alone)
  G = E + F (full stack: decomposition AND implementation selection
        together, on top of C's retrieval+applicability+reuse)

D is deliberately absorbed into C above rather than kept as its own arm --
see the comment on C: this harness's C already IS "retrieval +
applicability + procedure reuse" as one gate-enforced unit, and splitting
procedure-reuse-without-the-applicability-gate out as its own arm would
mean building and running an intentionally-unsafe agent (reuse without
verifying applicability first) purely to ablate it, which is a different
kind of cost/risk than the other steps. Noted here rather than silently
dropped: if a future pass wants D isolated, RealProcedureAgent's gate
call would need to become an optional/toggleable step, not a new file.

Each entry is intentionally A CONFIG, not code: `capabilities` is the
flag set a real future adapter would branch on; `builds_on` names the
arm whose behavior it strictly extends; `new_machinery_required` names
what does not exist yet and would need to be built before this arm could
actually run (so the LIST of new engineering work implied by "just add
arm F" is visible up front, not discovered mid-implementation).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AblationArmConfig:
    arm_id: str
    label: str
    builds_on: str | None
    capabilities: frozenset[str]
    new_machinery_required: tuple[str, ...] = ()


ABLATION_MATRIX: dict[str, AblationArmConfig] = {
    "A": AblationArmConfig(
        arm_id="A", label="frontier_solo", builds_on=None,
        capabilities=frozenset(),
    ),
    "C": AblationArmConfig(
        arm_id="C", label="frontier_plus_retrieval_applicability_reuse",
        builds_on="A",
        capabilities=frozenset({"retrieval", "applicability", "procedure_reuse"}),
    ),
    "E": AblationArmConfig(
        arm_id="E", label="frontier_plus_C_plus_decomposition",
        builds_on="C",
        capabilities=frozenset({"retrieval", "applicability", "procedure_reuse", "decomposition"}),
        new_machinery_required=(
            "a subgoal-decomposition step run before per-subgoal retrieval/"
            "applicability -- does not exist in this harness today",
        ),
    ),
    "F": AblationArmConfig(
        arm_id="F", label="frontier_plus_C_plus_implementation_selection",
        builds_on="C",
        capabilities=frozenset({"retrieval", "applicability", "procedure_reuse", "implementation_selection"}),
        new_machinery_required=(
            "a real MCP-surface call into backend/app/execution/"
            "implementation_registry.py's resolve()/bind semantics from "
            "this harness's arm C, rather than crediting reuse of the "
            "procedure alone -- mcp_surface.McpSurface has no such method "
            "yet (search/check_applicability/get_procedure only)",
        ),
    ),
    "G": AblationArmConfig(
        arm_id="G", label="full_stack",
        builds_on="C",
        capabilities=frozenset({
            "retrieval", "applicability", "procedure_reuse",
            "decomposition", "implementation_selection",
        }),
        new_machinery_required=(
            "both E's and F's machinery, combined",
        ),
    ),
}


def capability_delta(arm_id: str) -> frozenset[str]:
    """Capabilities `arm_id` adds beyond the arm it builds on -- the actual
    ablation question ("what does adding X contribute") reduces to a diff
    over ABLATION_MATRIX, not a fresh run per pair."""
    arm = ABLATION_MATRIX[arm_id]
    if arm.builds_on is None:
        return arm.capabilities
    base = ABLATION_MATRIX[arm.builds_on]
    return arm.capabilities - base.capabilities


def smallest_sensible_matrix() -> list[str]:
    """spec section 25's own instruction: the smallest matrix that still
    answers "which mechanisms materially contribute." A (no capabilities)
    through G (everything) with every intermediate step's delta
    attributable to exactly one new capability -- C's delta is three
    capabilities at once (see the module docstring's note on why D isn't
    split out), everything after C adds exactly one."""
    return ["A", "C", "E", "F", "G"]
