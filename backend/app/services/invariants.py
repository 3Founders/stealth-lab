"""
Numeric invariant checking for procedures -- the one constraint shape
applicability.py structurally cannot express.

WHY THIS EXISTS. check_hard_constraints()'s precondition evaluation is
equality-only against claim strings:

    c["properties"].get("predicate") == predicate
    and c["properties"].get("object") == expected_object

That answers "is fact P asserted about subject S" and nothing else. A
real constraint like "the payment amount must not exceed the account
balance" is a RELATION between two runtime quantities, not a fact about
the world that project_state() could ever return -- there is no claim to
look up, and `object` is compared with `==` against a string. No amount
of precondition modelling reaches it.

WHY `invariants` AND NOT `preconditions`. Two independent reasons, both
real:
  1. v1_precondition_groundedness requires every precondition predicate
     to appear in environment_probe.PROBE_PREDICATE_VOCABULARY -- a
     closed vocabulary of probe-derived facts. A numeric relation is not
     in it and should not be added to it (it is not something the
     environment probe can observe). Putting numeric constraints in
     preconditions would mean either failing V1 forever or corrupting
     that vocabulary's meaning.
  2. procedures.invariants already exists (db/18_procedures.sql) and is
     currently dead -- written by capture_procedure(), read by nothing.
     This is what it is for.

DELIBERATELY NOT A SOLVER FOR EVERYTHING. This module answers exactly
one question -- does a set of linear numeric relations hold under a set
of bindings -- and refuses anything else rather than growing into a
general expression evaluator. `expr` is parsed with Python's own `ast`
module in a whitelist, NOT eval(): procedures can be authored by an LLM
and stored in a database, so an expression string is untrusted input and
must never reach eval().

SOLVER RUNTIME IS BOUNDED AND OFF THE EVENT LOOP. Every Solver gets a
wall-clock timeout (DEFAULT_SOLVER_TIMEOUT_MS), and an expression the
solver cannot settle inside that budget lands in `undecidable` -- the
same bucket as unbound variables, and for the same reason: "could not
decide" is not evidence of violation. The retrieval-time caller
(applicability.check_hard_constraints) goes through
check_invariants_async(), which runs the solve on a worker thread via
asyncio.to_thread -- a pathological expression must never stall the
event loop every other request multiplexes on.
"""
from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

# Comparisons and arithmetic this module admits. Anything outside these
# sets is a refusal, not a best-effort interpretation.
_ALLOWED_COMPARE = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)
_ALLOWED_BINOP = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)

# Wall-clock budget per z3 query, milliseconds. Generous relative to the
# whitelist's actual expressiveness (linear arithmetic over a handful of
# symbols decides in microseconds); tight enough that a pathological or
# adversarial expression cannot wedge retrieval. Configuration, not a
# proven-correct number -- flagged as such rather than tuned to a suite.
DEFAULT_SOLVER_TIMEOUT_MS = 5_000


@dataclass
class InvariantResult:
    """
    Three outcomes, kept distinct on purpose -- collapsing `undecidable`
    into `violated` would make a procedure look inapplicable merely
    because its quantities are not bound yet, which is the normal state
    at retrieval time (nobody has stated an amount). Callers decide what
    an undecidable invariant means for them; this module does not decide
    for them.
    """
    satisfied: bool
    violated: list[str] = field(default_factory=list)
    undecidable: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_problems(self) -> bool:
        return bool(self.violated or self.errors)


def _z3_available() -> bool:
    try:
        import z3  # noqa: F401
    except ImportError:
        return False
    return True


class _ExprBuilder(ast.NodeVisitor):
    """
    Walks a parsed expression and rebuilds it as a z3 term. Raises
    ValueError on anything not explicitly whitelisted -- calls,
    attributes, subscripts, comprehensions, lambdas, names that are not
    declared variables. The whitelist is the security boundary; there is
    no fallback branch that "tries anyway".
    """

    def __init__(self, bindings: dict[str, float]):
        self._bindings = bindings
        self.unbound: set[str] = set()

    def visit_Compare(self, node: ast.Compare) -> Any:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise ValueError("only single comparisons are supported (a <= b), not chains")
        op = node.ops[0]
        if not isinstance(op, _ALLOWED_COMPARE):
            raise ValueError(f"comparison {type(op).__name__} is not allowed")
        left = self.visit(node.left)
        right = self.visit(node.comparators[0])
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
        if isinstance(op, ast.Eq):
            return left == right
        return left != right

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        import z3
        values = [self.visit(v) for v in node.values]
        return z3.And(*values) if isinstance(node.op, ast.And) else z3.Or(*values)

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        if not isinstance(node.op, _ALLOWED_BINOP):
            raise ValueError(f"operator {type(node.op).__name__} is not allowed")
        left, right = self.visit(node.left), self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise ValueError(f"unary {type(node.op).__name__} is not allowed")
        operand = self.visit(node.operand)
        return operand if isinstance(node.op, ast.UAdd) else -operand

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in self._bindings:
            return self._bindings[node.id]
        # Unbound: still build a real z3 symbol so the expression parses
        # and we can report WHICH variable was missing, rather than
        # failing with a generic parse error.
        import z3
        self.unbound.add(node.id)
        return z3.Real(node.id)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"only numeric constants are allowed, got {node.value!r}")
        return node.value

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"expression node {type(node).__name__} is not allowed")


def check_invariants(
    invariants: Optional[list[dict]], bindings: Optional[dict[str, float]] = None,
    *, timeout_ms: int = DEFAULT_SOLVER_TIMEOUT_MS,
) -> InvariantResult:
    """
    Decide a procedure's numeric invariants under `bindings`.

    Each invariant is {"kind": "numeric", "expr": "amount <= balance"}.
    A `kind` other than "numeric" is IGNORED, not an error -- this
    function claims only the numeric shape, and refusing to run because
    some future invariant kind exists would make it impossible to add
    one incrementally.

    Empty/None invariants is trivially satisfied -- the overwhelmingly
    common case (every procedure row today), and the reason wiring this
    into the applicability cascade costs nothing for existing data.

    A malformed expression is reported in `errors`, never raised:
    procedures can be LLM-authored, and one bad row must not take down
    retrieval for every other candidate.

    `timeout_ms` bounds each z3 query. A query that comes back
    `unknown` -- timeout, or genuinely undecidable arithmetic -- is
    reported in `undecidable`, NOT in violated/errors: "the solver gave
    up" is no evidence of violation, and reporting it as either would
    break the satisfied/undecidable asymmetry this module exists to
    keep. Retrieval-time callers should prefer check_invariants_async()
    so even a bounded solve never occupies the event loop.
    """
    result = InvariantResult(satisfied=True)
    if not invariants:
        return result

    numeric = [
        inv for inv in invariants
        if isinstance(inv, dict) and inv.get("kind", "numeric") == "numeric"
    ]
    if not numeric:
        return result

    if not _z3_available():
        # Honest degradation, loudly: without the solver we cannot claim
        # these hold. Reported as an error (not silently satisfied), so a
        # missing dependency surfaces instead of masquerading as a pass.
        result.satisfied = False
        result.errors.append(
            "z3-solver is not installed -- numeric invariants cannot be checked"
        )
        return result

    import z3

    bindings = bindings or {}
    for inv in numeric:
        expr = inv.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            result.errors.append(f"invariant has no usable 'expr': {inv!r}")
            continue

        builder = _ExprBuilder(bindings)
        try:
            tree = ast.parse(expr, mode="eval")
            term = builder.visit(tree)
        except (SyntaxError, ValueError) as exc:
            result.errors.append(f"invariant {expr!r} could not be parsed: {exc}")
            continue

        if builder.unbound:
            result.undecidable.append(
                f"{expr} (unbound: {', '.join(sorted(builder.unbound))})"
            )
            continue

        # Fully bound: the term reduced to a concrete Python bool via
        # operator overloading on plain numbers in most cases, but route
        # everything through z3 uniformly so mixed z3/native terms behave
        # identically. Violation == unsat; unknown (timeout or otherwise
        # undecided) is explicitly NOT a violation.
        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        solver.add(term if z3.is_expr(term) else z3.BoolVal(bool(term)))
        outcome = solver.check()
        if outcome == z3.unsat:
            result.violated.append(expr)
        elif outcome == z3.unknown:
            result.undecidable.append(f"{expr} (solver returned unknown within {timeout_ms}ms)")

    result.satisfied = not result.violated and not result.errors
    return result


async def check_invariants_async(
    invariants: Optional[list[dict]], bindings: Optional[dict[str, float]] = None,
    *, timeout_ms: int = DEFAULT_SOLVER_TIMEOUT_MS,
) -> InvariantResult:
    """
    The retrieval-time entry point: identical decision to
    check_invariants(), computed on a worker thread instead of the event
    loop. The applicability cascade runs once per candidate procedure --
    under it, every caller in a concurrent request pays for one slow
    solve unless the solve leaves the loop.
    """
    return await asyncio.to_thread(
        check_invariants, invariants, bindings, timeout_ms=timeout_ms,
    )


def authoring_problems(
    invariants: Optional[list[dict]], *, timeout_ms: int = DEFAULT_SOLVER_TIMEOUT_MS,
) -> list[str]:
    """
    Authoring-time validation (validators.V6's engine): what is WRONG
    with these invariants as authored, before any runtime bindings
    exist. Returns human-readable problems; empty list means clean.

    Checks two things per numeric invariant:
      1. it parses under this module's whitelist (same parser the
         runtime path uses -- authoring must never accept something
         retrieval will have to error on), and
      2. it is SATISFIABLE by SOME binding -- proved with free Real
         symbols, since at authoring time no quantities exist yet. An
         expression like "amount <= balance and amount > balance" can
         never hold under ANY binding, so a procedure carrying it can
         never legitimately run; that is a defect worth rejecting at
         authoring time, exactly analogous to V1's permanently-
         unsatisfiable precondition.

    An expression the solver cannot decide within `timeout_ms` IS
    reported here (unlike the runtime path, where unknown lands in
    undecidable and stays non-disqualifying): authoring is offline and
    human-reviewed, and an expression whose satisfiability we could not
    establish deserves scrutiny before being persisted -- worst case the
    reviewer sees a false alarm on a weird-but-fine formula, while the
    silent alternative ships procedures that can never fire.
    """
    problems: list[str] = []
    if not invariants:
        return problems

    numeric = [
        inv for inv in invariants
        if isinstance(inv, dict) and inv.get("kind", "numeric") == "numeric"
    ]
    if not numeric:
        return problems

    if not _z3_available():
        return ["z3-solver is not installed -- invariants cannot be validated"]

    import z3

    for inv in numeric:
        expr = inv.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            problems.append(f"invariant has no usable 'expr': {inv!r}")
            continue

        builder = _ExprBuilder({})
        try:
            tree = ast.parse(expr, mode="eval")
            term = builder.visit(tree)
        except (SyntaxError, ValueError) as exc:
            problems.append(f"invariant {expr!r} could not be parsed: {exc}")
            continue

        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        # Same uniform handling as the runtime path: a fully-constant
        # expression reduces to a plain Python bool, which must be
        # wrapped before it can be asserted.
        solver.add(term if z3.is_expr(term) else z3.BoolVal(bool(term)))
        outcome = solver.check()
        if outcome == z3.unsat:
            problems.append(
                f"invariant {expr!r} is unsatisfiable -- no binding of "
                f"{sorted(builder.unbound)} can ever make it hold, so the "
                f"procedure can never legitimately run"
            )
        elif outcome == z3.unknown:
            problems.append(
                f"invariant {expr!r} could not be decided within {timeout_ms}ms "
                f"-- review manually before persisting"
            )
    return problems
