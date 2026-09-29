"""Structural features for Python code spans (stage S2/S3 of the code cascade). Pure: no I/O, no model.

WHY STRUCTURE AND NOT "LOOKS IMPORTANT"
    The cascade has to decide, cheaply and for thousands of files, which spans of a repository are worth a model's time and
    a small model's context. Size alone picks generated tables and giant switch statements; stars pick the repository, not
    the file. What separates code worth studying from code worth skipping is measurable without a model: how many
    independent decisions it makes (cyclomatic complexity), how deeply they nest, how many distinct things it calls, whether
    it is an algorithm (loops + state + comprehensions) or plumbing (a getter, a delegate, a re-export).

HONEST LIMITS
    These are heuristics that rank; they do not prove a span is good. A high score means "worth the S4 judge", nothing more.
    Python only here (the stdlib `ast` is exact); other languages use `generic_features`, which is coarser and says so.
"""
from __future__ import annotations

import ast
import hashlib
import dataclasses
from dataclasses import dataclass, field
from typing import Optional

# A span longer than this stops being a useful exemplar for a small model's context; it is split by method instead.
MAX_SPAN_LINES = 150
MIN_SPAN_LINES = 5

_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.comprehension, ast.Assert)
_NEST_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)


@dataclass(frozen=True)
class SpanFeatures:
    name: str
    kind: str                     # function | method | class
    line_start: int               # 1-based, inclusive (decorators included)
    line_end: int
    code_lines: int               # non-blank, non-comment, non-docstring
    cyclomatic: int
    max_nesting: int
    distinct_calls: int
    branches: int
    has_loop: bool
    has_recursion: bool
    is_async: bool
    is_generator: bool
    uses_context_manager: bool
    uses_regex_or_bitops: bool
    has_docstring: bool
    annotated_fraction: float
    trivial_reason: Optional[str]  # why it is plumbing, or None
    fingerprint: str               # identifier-normalised structure hash: copy-paste shows up as an equal fingerprint
    calls: frozenset[str] = field(default_factory=frozenset)

    @property
    def lines(self) -> int:
        return self.line_end - self.line_start + 1


def _docstring_node(body: list[ast.stmt]) -> Optional[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[0]
    return None


def _call_name(node: ast.Call) -> Optional[str]:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _max_depth(node: ast.AST, depth: int = 0) -> int:
    best = depth
    for child in ast.iter_child_nodes(node):
        bump = 1 if isinstance(child, _NEST_NODES) else 0
        best = max(best, _max_depth(child, depth + bump))
    return best


class _Normaliser(ast.NodeTransformer):
    """Replace identifiers and constants so two copy-pasted functions with renamed variables hash equal."""

    def visit_Name(self, node: ast.Name):  # noqa: N802
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg):  # noqa: N802
        return ast.copy_location(ast.arg(arg="_", annotation=None), node)

    def visit_Constant(self, node: ast.Constant):  # noqa: N802
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)

    def visit_FunctionDef(self, node: ast.FunctionDef):  # noqa: N802
        node.name = "_"
        node.decorator_list = []
        node.returns = None
        self.generic_visit(node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]


def _fingerprint(node: ast.AST) -> str:
    import copy

    clone = _Normaliser().visit(copy.deepcopy(node))
    return hashlib.sha1(ast.dump(clone, annotate_fields=False).encode("utf-8")).hexdigest()[:16]


def _trivial_reason(node: ast.AST, body: list[ast.stmt], code_lines: int) -> Optional[str]:
    """Plumbing: a span that carries no decisions of its own."""
    stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
    if not stmts:
        return "empty_or_docstring_only"
    if all(isinstance(s, ast.Pass) for s in stmts):
        return "pass_only"
    if len(stmts) == 1:
        only = stmts[0]
        if isinstance(only, ast.Raise) and "NotImplementedError" in ast.dump(only):
            return "abstract_stub"
        if isinstance(only, ast.Return):
            value = only.value
            if value is None or isinstance(value, (ast.Constant, ast.Name)):
                return "constant_or_name_return"
            if isinstance(value, ast.Attribute):
                return "attribute_getter"
            if isinstance(value, ast.Call) and not any(isinstance(n, _BRANCH_NODES) for n in ast.walk(value)):
                return "pure_delegate"
        if isinstance(only, ast.Assign) and all(isinstance(t, (ast.Attribute, ast.Name)) for t in only.targets):
            return "attribute_setter"
    if code_lines < 4:
        return "too_small"
    if isinstance(node, ast.ClassDef):
        members = [s for s in stmts if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if not members:
            return "data_container"
    return None


def _code_lines(source_lines: list[str], node: ast.AST) -> int:
    start, end = node.lineno, node.end_lineno or node.lineno  # type: ignore[attr-defined]
    doc = _docstring_node(getattr(node, "body", []))
    doc_range = set(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1)) if doc is not None else set()
    count = 0
    for lineno in range(start, end + 1):
        if lineno in doc_range:
            continue
        text = source_lines[lineno - 1].strip() if lineno - 1 < len(source_lines) else ""
        if text and not text.startswith("#"):
            count += 1
    return count


def _features_for(node: ast.AST, kind: str, source_lines: list[str]) -> SpanFeatures:
    body = list(getattr(node, "body", []))
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])  # type: ignore[attr-defined]
    end = node.end_lineno or node.lineno  # type: ignore[attr-defined]
    walk = list(ast.walk(node))
    calls = frozenset(n for n in (_call_name(c) for c in walk if isinstance(c, ast.Call)) if n)
    cyclomatic = 1 + sum(isinstance(n, _BRANCH_NODES) for n in walk) + sum(
        max(0, len(n.values) - 1) for n in walk if isinstance(n, ast.BoolOp))
    name = getattr(node, "name", "?")
    args = list(getattr(getattr(node, "args", None), "args", []) or [])
    annotated = sum(1 for a in args if a.annotation is not None) + (1 if getattr(node, "returns", None) is not None else 0)
    total_slots = len(args) + (1 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 0)
    code_lines = _code_lines(source_lines, node)
    return SpanFeatures(
        name=name, kind=kind, line_start=start, line_end=end, code_lines=code_lines,
        cyclomatic=cyclomatic, max_nesting=_max_depth(node), distinct_calls=len(calls),
        branches=sum(isinstance(n, _BRANCH_NODES) for n in walk),
        has_loop=any(isinstance(n, (ast.For, ast.AsyncFor, ast.While, ast.comprehension)) for n in walk),
        has_recursion=any(isinstance(c, ast.Call) and _call_name(c) == name for c in walk),
        is_async=isinstance(node, ast.AsyncFunctionDef) or any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in walk),
        is_generator=any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in walk),
        uses_context_manager=any(isinstance(n, (ast.With, ast.AsyncWith)) for n in walk),
        uses_regex_or_bitops=any(
            (isinstance(n, ast.BinOp) and isinstance(n.op, (ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift)))
            or (isinstance(n, ast.Call) and _call_name(n) in {"compile", "match", "search", "sub", "findall", "fullmatch"})
            for n in walk),
        has_docstring=_docstring_node(body) is not None,
        annotated_fraction=(annotated / total_slots) if total_slots else 0.0,
        trivial_reason=_trivial_reason(node, body, code_lines),
        fingerprint=_fingerprint(node), calls=calls,
    )


def extract_spans(source: str) -> list[SpanFeatures]:
    """Top-level functions, classes, and the methods of classes too long to be one span. SyntaxError -> no spans."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    lines = source.splitlines()
    spans: list[SpanFeatures] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append(_features_for(node, "function", lines))
        elif isinstance(node, ast.ClassDef):
            whole = _features_for(node, "class", lines)
            if whole.lines <= MAX_SPAN_LINES:
                members = [_features_for(m, "method", lines) for m in node.body
                           if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if members and all(m.trivial_reason for m in members):
                    # A class of getters, setters, delegates and stubs is plumbing however many branches its parts sum to.
                    whole = dataclasses.replace(whole, trivial_reason="plumbing_class")
                spans.append(whole)
            else:
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        spans.append(_features_for(member, "method", lines))
    return spans


def module_imports(source: str) -> set[str]:
    """Dotted module names this file imports (relative imports keep their leading dots), for the centrality graph."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * (node.level or 0) + (node.module or "")
            if node.module:
                found.add(base)
            for alias in node.names:
                # `from pkg import mod` and `from . import mod` name a MODULE as often as a symbol; try both readings.
                found.add(base + alias.name if base.endswith(".") else f"{base}.{alias.name}")
    return found
