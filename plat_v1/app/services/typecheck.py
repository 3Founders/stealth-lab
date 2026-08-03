"""
Deterministic plan validation.

**Pure function. No model calls, no database access, no network, no clock.**
Everything it needs about the world outside the plan arrives in a
`TypecheckContext` the caller assembles. That constraint is the point: this
is the piece that makes a generated plan trustworthy, and a checker that
asks a model whether the model's own plan is sound is not a check.

Rules, each with its own id so a failure names the thing it violated:

  well_formed         no empty schemas, no duplicate refs, no self-edges,
                      no edge naming a ref that isn't declared
  acyclicity          no cycles over REQUIRES and PRODUCES
  dataflow_closure    every declared input is produced upstream or external
  type_compatibility  a producer's output must fit the consumer's input
  executable_leaf     every leaf has at least one enabled implementation
  composite_interface a composite's declared interface is met by its expansion
  nesting_depth       composites expand one level, not two

Order matters: well_formed runs first because every later rule assumes refs
resolve and schemas have shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional
from uuid import UUID

from app.models.plan import Expansion, Plan, PlanEdge, PlanNode

# Edge types that impose an ordering. DECOMPOSES_TO is structural (it names a
# composite's expansion) and is deliberately excluded from the cycle check --
# a composite pointing at its own children is not a cycle.
ORDERING_EDGES = ("REQUIRES", "PRODUCES")


@dataclass(frozen=True)
class Problem:
    rule: str
    message: str
    refs: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        where = f" ({', '.join(self.refs)})" if self.refs else ""
        return f"[{self.rule}] {self.message}{where}"

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "message": self.message, "refs": list(self.refs)}


@dataclass(frozen=True)
class TypecheckContext:
    """
    Everything the checker needs to know that isn't in the plan.

    The caller loads this from the database *before* calling typecheck, which
    is what keeps typecheck itself pure and trivially testable.
    """

    # task_node_id -> count of enabled implementations. A referenced task
    # missing from this mapping is treated as unknown, not as zero.
    implementation_counts: Mapping[UUID, int] = field(default_factory=dict)
    known_task_ids: frozenset[UUID] = frozenset()

    @classmethod
    def empty(cls) -> "TypecheckContext":
        return cls()


# ---------------------------------------------------------------------------
# JSON Schema structural comparison
# ---------------------------------------------------------------------------


def _declared_properties(schema: Mapping[str, Any]) -> dict[str, dict]:
    props = schema.get("properties") or {}
    return {k: (v or {}) for k, v in props.items()}


def _required_properties(schema: Mapping[str, Any]) -> dict[str, dict]:
    """
    The properties a consumer genuinely needs.

    When `required` is absent we treat every declared property as required.
    Conservative on purpose: a node that declares an input and then doesn't
    receive it is far more often a broken plan than an intentional optional,
    and the failure shows up at runtime as a KeyError three stages later.
    """
    props = _declared_properties(schema)
    required = schema.get("required")
    if required is None:
        return props
    return {name: props.get(name, {}) for name in required}


def _type_set(schema: Mapping[str, Any]) -> Optional[set[str]]:
    t = schema.get("type")
    if t is None:
        return None
    return {t} if isinstance(t, str) else set(t)


def schema_satisfies(
    produced: Mapping[str, Any], required: Mapping[str, Any], path: str = ""
) -> list[str]:
    """
    Can a value matching `produced` be consumed as `required`?

    Returns human-readable mismatches; empty means compatible. Structural and
    deliberately permissive where information is absent -- an untyped schema
    is unknown, not wrong, and rejecting it would just push authors toward
    writing `{"type": "object"}` everywhere to shut the checker up.
    """
    problems: list[str] = []
    where = path or "value"

    p_types, r_types = _type_set(produced), _type_set(required)
    if p_types is not None and r_types is not None:
        # integer is an acceptable number; the reverse is not.
        widened = r_types | {"integer"} if "number" in r_types else r_types
        if not p_types <= widened:
            problems.append(
                f"{where}: produces {'|'.join(sorted(p_types))} "
                f"but {'|'.join(sorted(r_types))} is required"
            )
            return problems  # nested comparison is meaningless once types differ

    if _declared_properties(required):
        produced_props = produced.get("properties")
        # An object with no declared properties is opaque, not empty. Recursing
        # into it would report every required key as missing on a schema that
        # simply didn't enumerate its shape.
        if produced_props is not None:
            for name, sub_required in _required_properties(required).items():
                if name not in produced_props:
                    problems.append(f"{where}.{name}: required but not produced")
                else:
                    problems += schema_satisfies(
                        produced_props[name] or {}, sub_required, f"{where}.{name}"
                    )

    if required.get("items") and produced.get("items"):
        problems += schema_satisfies(produced["items"], required["items"], f"{where}[]")

    return problems


def _is_empty_schema(schema: Mapping[str, Any]) -> bool:
    """
    An empty schema is the author declining to commit.

    `{}` is empty. So is anything carrying no type and no properties. An
    object type that never declares a `properties` key is also empty --
    "it's an object" says nothing checkable. `{"type": "object",
    "properties": {}}` is allowed: the key is present, so the author has
    explicitly said "no fields", which is a commitment.
    """
    if not schema:
        return True
    if not any(k in schema for k in ("type", "properties", "$ref", "enum", "anyOf", "allOf")):
        return True
    types = _type_set(schema)
    if types == {"object"} and "properties" not in schema:
        return True
    return False


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _all_nodes(plan: Plan) -> list[PlanNode]:
    """Top-level nodes plus every expansion node, flattened."""
    out: list[PlanNode] = []
    for node in plan.nodes:
        out.append(node)
        if node.expansion:
            out.extend(node.expansion.nodes)
    return out


def _check_well_formed(plan: Plan) -> list[Problem]:
    problems: list[Problem] = []

    seen: set[str] = set()
    for node in _all_nodes(plan):
        if not node.ref:
            problems.append(Problem("well_formed", "a node has an empty ref"))
            continue
        if node.ref in seen:
            problems.append(
                Problem("well_formed", f"duplicate node ref '{node.ref}'", (node.ref,))
            )
        seen.add(node.ref)

        for label, schema in (
            ("input_schema", node.input_schema),
            ("output_schema", node.output_schema),
        ):
            if _is_empty_schema(schema):
                problems.append(
                    Problem(
                        "well_formed",
                        f"node '{node.ref}' has an empty {label}; an empty schema "
                        f"commits to nothing and defeats every check downstream",
                        (node.ref,),
                    )
                )

    def check_edges(edges: Iterable[PlanEdge], scope: set[str], scope_name: str) -> None:
        for edge in edges:
            if edge.source_ref == edge.target_ref:
                problems.append(
                    Problem(
                        "well_formed",
                        f"self-edge on '{edge.source_ref}'",
                        (edge.source_ref,),
                    )
                )
            for ref in (edge.source_ref, edge.target_ref):
                if ref not in scope:
                    problems.append(
                        Problem(
                            "well_formed",
                            f"edge {edge.type} references undeclared ref '{ref}' "
                            f"in {scope_name}",
                            (ref,),
                        )
                    )

    check_edges(plan.edges, {n.ref for n in plan.nodes}, "the plan")
    for node in plan.nodes:
        if node.expansion:
            check_edges(
                node.expansion.edges,
                {n.ref for n in node.expansion.nodes},
                f"the expansion of '{node.ref}'",
            )

    return problems


def _check_acyclicity(nodes: list[PlanNode], edges: list[PlanEdge], scope: str) -> list[Problem]:
    refs = {n.ref for n in nodes}
    adjacency: dict[str, list[str]] = {ref: [] for ref in refs}
    indegree: dict[str, int] = {ref: 0 for ref in refs}

    for edge in edges:
        if edge.type not in ORDERING_EDGES:
            continue
        if edge.source_ref not in refs or edge.target_ref not in refs:
            continue  # already reported by well_formed
        if edge.source_ref == edge.target_ref:
            continue
        adjacency[edge.source_ref].append(edge.target_ref)
        indegree[edge.target_ref] += 1

    queue = [ref for ref, d in indegree.items() if d == 0]
    visited = 0
    while queue:
        ref = queue.pop()
        visited += 1
        for nxt in adjacency[ref]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if visited < len(refs):
        stuck = sorted(ref for ref, d in indegree.items() if d > 0)
        return [
            Problem(
                "acyclicity",
                f"{scope} contains a cycle over REQUIRES/PRODUCES involving "
                f"{', '.join(stuck)}",
                tuple(stuck),
            )
        ]
    return []


def topological_order(nodes: list[PlanNode], edges: list[PlanEdge]) -> list[PlanNode]:
    """
    Execution order. Assumes acyclicity -- run the typechecker first.

    Ties are broken by declaration order rather than set iteration order, so
    two runs of the same plan execute its stages in the same sequence and
    traces stay comparable.
    """
    by_ref = {n.ref: n for n in nodes}
    order = {n.ref: i for i, n in enumerate(nodes)}
    indegree = {n.ref: 0 for n in nodes}
    adjacency: dict[str, list[str]] = {n.ref: [] for n in nodes}

    for edge in edges:
        if edge.type not in ORDERING_EDGES:
            continue
        if edge.source_ref not in by_ref or edge.target_ref not in by_ref:
            continue
        if edge.source_ref == edge.target_ref:
            continue
        adjacency[edge.source_ref].append(edge.target_ref)
        indegree[edge.target_ref] += 1

    ready = sorted((r for r, d in indegree.items() if d == 0), key=lambda r: order[r])
    result: list[PlanNode] = []
    while ready:
        ref = ready.pop(0)
        result.append(by_ref[ref])
        newly_ready = []
        for nxt in adjacency[ref]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                newly_ready.append(nxt)
        ready = sorted(ready + newly_ready, key=lambda r: order[r])
    return result


def _producers_into(edges: list[PlanEdge], target_ref: str) -> list[str]:
    return [e.source_ref for e in edges if e.type == "PRODUCES" and e.target_ref == target_ref]


def _check_dataflow_closure(
    nodes: list[PlanNode],
    edges: list[PlanEdge],
    external: set[str],
    scope: str = "",
) -> list[Problem]:
    """
    Scoped so it can run over an expansion as well as the top-level plan.

    Inside an expansion the role of `external_inputs` is played by the
    composite's own declared inputs -- those are the only values that reach
    the subgraph from outside it.
    """
    problems: list[Problem] = []
    by_ref = {n.ref: n for n in nodes}
    where = f" in {scope}" if scope else ""
    supplied = (
        "the composite does not declare it"
        if scope
        else "external_inputs does not name it"
    )

    for node in nodes:
        upstream_props: set[str] = set()
        for source_ref in _producers_into(edges, node.ref):
            source = by_ref.get(source_ref)
            if source is not None:
                upstream_props |= set(_declared_properties(source.output_schema))

        for name in _required_properties(node.input_schema):
            if name in external or name in upstream_props:
                continue
            problems.append(
                Problem(
                    "dataflow_closure",
                    f"node '{node.ref}'{where} requires input '{name}', which no "
                    f"upstream node produces and {supplied}",
                    (node.ref,),
                )
            )
    return problems


def _check_type_compatibility(
    nodes: list[PlanNode], edges: list[PlanEdge], scope: str = ""
) -> list[Problem]:
    """Scoped, for the same reason as dataflow closure."""
    problems: list[Problem] = []
    by_ref = {n.ref: n for n in nodes}
    where = f"{scope}: " if scope else ""

    for edge in edges:
        if edge.type != "PRODUCES":
            continue
        source, target = by_ref.get(edge.source_ref), by_ref.get(edge.target_ref)
        if source is None or target is None:
            continue  # already reported by well_formed

        produced = _declared_properties(source.output_schema)
        consumed = _declared_properties(target.input_schema)
        shared = set(produced) & set(consumed)

        # A PRODUCES edge that hands over nothing the consumer declares is a
        # modelling error: either the edge is wrong or one of the schemas is.
        # Either way the dataflow the plan claims does not exist.
        if not shared:
            problems.append(
                Problem(
                    "type_compatibility",
                    f"{where}'{edge.source_ref}' produces into '{edge.target_ref}' but "
                    f"shares no property with its input schema",
                    (edge.source_ref, edge.target_ref),
                )
            )
            continue

        for name in sorted(shared):
            for detail in schema_satisfies(produced[name], consumed[name], name):
                problems.append(
                    Problem(
                        "type_compatibility",
                        f"{where}'{edge.source_ref}' -> '{edge.target_ref}': {detail}",
                        (edge.source_ref, edge.target_ref),
                    )
                )
    return problems


def _check_executable_leaves(plan: Plan, ctx: TypecheckContext) -> list[Problem]:
    problems: list[Problem] = []
    for node in _all_nodes(plan):
        if node.kind != "leaf":
            continue

        if node.existing_task_id is not None:
            task_id = node.existing_task_id
            if task_id not in ctx.known_task_ids:
                problems.append(
                    Problem(
                        "executable_leaf",
                        f"node '{node.ref}' reuses task {task_id}, which is not a live "
                        f"task node",
                        (node.ref,),
                    )
                )
            elif ctx.implementation_counts.get(task_id, 0) <= 0:
                problems.append(
                    Problem(
                        "executable_leaf",
                        f"node '{node.ref}' reuses task {task_id}, which has no enabled "
                        f"implementation",
                        (node.ref,),
                    )
                )
            continue

        if not any(impl.enabled for impl in node.implementations):
            problems.append(
                Problem(
                    "executable_leaf",
                    f"leaf node '{node.ref}' has no enabled implementation and does not "
                    f"reuse an existing task, so nothing can run it",
                    (node.ref,),
                )
            )
    return problems


def _expansion_entry_requirements(expansion: Expansion) -> dict[str, dict]:
    """
    What the expansion needs from outside itself.

    Anything a child requires that a sibling produces is satisfied internally;
    what's left has to come from the composite's own declared inputs.
    """
    internal: set[str] = set()
    for node in expansion.nodes:
        internal |= set(_declared_properties(node.output_schema))

    required: dict[str, dict] = {}
    for node in expansion.nodes:
        for name, sub in _required_properties(node.input_schema).items():
            if name not in internal:
                required.setdefault(name, sub)
    return required


def _check_composites(plan: Plan) -> list[Problem]:
    problems: list[Problem] = []

    for node in plan.nodes:
        if node.kind != "composite":
            if node.expansion and node.expansion.nodes:
                problems.append(
                    Problem(
                        "composite_interface",
                        f"node '{node.ref}' is a leaf but declares an expansion",
                        (node.ref,),
                    )
                )
            continue

        expansion = node.expansion
        if expansion is None or not expansion.nodes:
            problems.append(
                Problem(
                    "composite_interface",
                    f"composite node '{node.ref}' has no expansion, so its interface "
                    f"cannot be satisfied by anything",
                    (node.ref,),
                )
            )
            continue

        # One level of nesting. Rejected here with a clear message rather than
        # half-supported in the executor, where the failure would be a
        # confusing runtime error instead of a plan that never ran.
        for child in expansion.nodes:
            if child.kind == "composite" or child.expansion is not None:
                problems.append(
                    Problem(
                        "nesting_depth",
                        f"'{child.ref}' inside the expansion of '{node.ref}' is itself "
                        f"composite; v1 supports one level of nesting",
                        (node.ref, child.ref),
                    )
                )

        problems += _check_acyclicity(
            expansion.nodes, expansion.edges, f"the expansion of '{node.ref}'"
        )

        # Inputs are contravariant: the expansion must accept at least what
        # the composite promises to accept, so everything it needs has to be
        # something the composite declared it would be given.
        declared_in = _declared_properties(node.input_schema)
        for name, sub_required in _expansion_entry_requirements(expansion).items():
            # An input the composite fails to declare at all is a closure
            # failure, and dataflow_closure reports it against the child that
            # actually needs it. Reporting it here too would mean two
            # problems for one cause.
            if name not in declared_in:
                continue
            for detail in schema_satisfies(declared_in[name], sub_required, name):
                problems.append(
                    Problem(
                        "composite_interface",
                        f"composite '{node.ref}' input {detail}",
                        (node.ref,),
                    )
                )

        # Outputs are covariant: the expansion must produce at least what the
        # composite promises. Checked against the union of every child's
        # outputs rather than only the terminal ones -- a composite is
        # entitled to surface an intermediate value, and restricting it to
        # terminals would reject correct plans to catch nothing extra.
        produced: dict[str, dict] = {}
        for child in expansion.nodes:
            produced.update(_declared_properties(child.output_schema))

        for name, promised in _required_properties(node.output_schema).items():
            if name not in produced:
                problems.append(
                    Problem(
                        "composite_interface",
                        f"composite '{node.ref}' promises output '{name}', which nothing "
                        f"in its expansion produces",
                        (node.ref,),
                    )
                )
                continue
            for detail in schema_satisfies(produced[name], promised, name):
                problems.append(
                    Problem(
                        "composite_interface",
                        f"composite '{node.ref}' output {detail}",
                        (node.ref,),
                    )
                )

    return problems


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def typecheck(plan: Plan, ctx: Optional[TypecheckContext] = None) -> list[Problem]:
    """Validate a plan. An empty list means it is structurally sound."""
    ctx = ctx or TypecheckContext.empty()

    if not plan.nodes:
        return [Problem("well_formed", "the plan has no nodes")]

    problems = _check_well_formed(plan)
    # Later rules index nodes by ref and assume edges resolve. Running them on
    # a malformed plan produces cascades of derived noise that bury the one
    # problem worth reading.
    if problems:
        return problems

    problems += _check_acyclicity(plan.nodes, plan.edges, "the plan")
    problems += _check_dataflow_closure(plan.nodes, plan.edges, set(plan.external_inputs))
    problems += _check_type_compatibility(plan.nodes, plan.edges)

    # And again inside every expansion. Without this the rules that matter
    # most simply do not run on the reference workflow: a seeded composite
    # puts all six stages in an expansion, so every PRODUCES edge between
    # them -- the entire chain the typechecker exists to validate -- would go
    # unchecked while the plan reported clean.
    for node in plan.nodes:
        if not (node.expansion and node.expansion.nodes):
            continue
        scope = f"the expansion of '{node.ref}'"
        # _required_properties, not _declared_: an input the composite
        # declares but does not require is one the caller may legally
        # omit, so it cannot satisfy a child that requires it.
        supplied = set(_required_properties(node.input_schema))
        problems += _check_dataflow_closure(
            node.expansion.nodes, node.expansion.edges, supplied, scope
        )
        problems += _check_type_compatibility(
            node.expansion.nodes, node.expansion.edges, scope
        )

    problems += _check_executable_leaves(plan, ctx)
    problems += _check_composites(plan)
    return problems


def typecheck_report(problems: list[Problem]) -> dict[str, Any]:
    """The shape stored in `proposals.typecheck` and returned by the API."""
    return {
        "ok": not problems,
        "problems": [p.as_dict() for p in problems],
        "messages": [p.text for p in problems],
    }
