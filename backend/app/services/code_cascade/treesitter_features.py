"""Structural features for spans in non-Python languages, from tree-sitter (stage S2/S3 of the code cascade). Pure.

The output is the SAME `SpanFeatures` the Python extractor produces, so `scoring` ranks a Go function and a Python function
on one scale. Each language is a small table of grammar node names (what counts as a branch, a loop, a nesting level, a
call); the walk itself is shared. Adding a language is a table, not new logic.

WHY OUR OWN WALK AND NOT `code_index.outline`
    `outline` finds top-level functions, classes and methods, and stops there: it does not see the arrow functions and
    function expressions that hold most modern JS/TS (`export const handler = async (req) => {...}`), and it returns byte
    offsets, not the branch/nesting/call counts the score needs. This walk finds those functions itself.

HONEST LIMITS
    Cyclomatic complexity here counts branch NODES and boolean operators; it is not the language's official metric. Call
    names are syntactic (a callee's last identifier), like `call_graph`: two unrelated functions sharing a name collide. A
    file with syntax errors is skipped whole rather than scored from a partial tree.
"""
from __future__ import annotations

import hashlib
import re
import dataclasses
from dataclasses import dataclass
from typing import Any, Optional

from app.services.code_cascade.python_features import MAX_SPAN_LINES, SpanFeatures


@dataclass(frozen=True)
class LangSpec:
    tree_language: str
    functions: frozenset[str]           # declared functions and methods
    lambdas: frozenset[str]             # anonymous functions, named by the thing they are assigned to
    classes: frozenset[str]
    containers: frozenset[str]          # nodes to look THROUGH for top-level definitions (export wrappers, modules, impls)
    branches: frozenset[str]
    bool_ops: frozenset[str]
    nesting: frozenset[str]
    loops: frozenset[str]
    calls: frozenset[str]
    awaits: frozenset[str]
    yields: frozenset[str]
    scoped: frozenset[str]              # defer / with / try-with-resources
    body_fields: tuple[str, ...] = ("body",)
    assign_parents: frozenset[str] = frozenset({"variable_declarator", "assignment_expression", "pair"})


_JS_BRANCH = frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
                        "switch_case", "catch_clause", "ternary_expression"})
_JS = dict(
    functions=frozenset({"function_declaration", "generator_function_declaration", "method_definition"}),
    lambdas=frozenset({"arrow_function", "function_expression", "function", "generator_function"}),
    classes=frozenset({"class_declaration"}),
    containers=frozenset({"program", "export_statement", "class_body", "lexical_declaration", "variable_declaration",
                          "variable_declarator", "internal_module", "module", "statement_block", "expression_statement",
                          "assignment_expression", "parenthesized_expression", "object", "pair"}),
    branches=_JS_BRANCH, bool_ops=frozenset({"&&", "||", "??"}),
    nesting=frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
                       "switch_statement", "try_statement"}),
    loops=frozenset({"for_statement", "for_in_statement", "while_statement", "do_statement"}),
    calls=frozenset({"call_expression", "new_expression"}), awaits=frozenset({"await_expression"}),
    yields=frozenset({"yield_expression"}), scoped=frozenset({"try_statement"}),
)
SPECS: dict[str, LangSpec] = {
    "javascript": LangSpec(tree_language="javascript", **_JS),
    "typescript": LangSpec(tree_language="typescript", **_JS),
    "tsx": LangSpec(tree_language="tsx", **_JS),
    "go": LangSpec(
        tree_language="go",
        functions=frozenset({"function_declaration", "method_declaration"}), lambdas=frozenset({"func_literal"}),
        classes=frozenset(), containers=frozenset({"source_file"}),
        branches=frozenset({"if_statement", "for_statement", "expression_case", "type_case", "communication_case"}),
        bool_ops=frozenset({"&&", "||"}),
        nesting=frozenset({"if_statement", "for_statement", "expression_switch_statement", "type_switch_statement",
                           "select_statement"}),
        loops=frozenset({"for_statement"}), calls=frozenset({"call_expression"}), awaits=frozenset({"go_statement",
        "send_statement", "receive_statement"}), yields=frozenset(), scoped=frozenset({"defer_statement"}),
        assign_parents=frozenset({"short_var_declaration", "var_spec"})),
    "rust": LangSpec(
        tree_language="rust", functions=frozenset({"function_item"}), lambdas=frozenset({"closure_expression"}),
        classes=frozenset(), containers=frozenset({"source_file", "impl_item", "declaration_list", "mod_item", "trait_item"}),
        branches=frozenset({"if_expression", "for_expression", "while_expression", "loop_expression", "match_arm"}),
        bool_ops=frozenset({"&&", "||"}),
        nesting=frozenset({"if_expression", "for_expression", "while_expression", "loop_expression", "match_expression"}),
        loops=frozenset({"for_expression", "while_expression", "loop_expression"}),
        calls=frozenset({"call_expression", "macro_invocation"}), awaits=frozenset({"await_expression"}),
        yields=frozenset(), scoped=frozenset({"unsafe_block"}), assign_parents=frozenset({"let_declaration"})),
    "java": LangSpec(
        tree_language="java", functions=frozenset({"method_declaration", "constructor_declaration"}),
        lambdas=frozenset({"lambda_expression"}), classes=frozenset({"class_declaration", "record_declaration"}),
        containers=frozenset({"program", "class_body", "enum_body", "interface_body"}),
        branches=frozenset({"if_statement", "for_statement", "enhanced_for_statement", "while_statement", "do_statement",
                            "switch_label", "catch_clause", "ternary_expression"}),
        bool_ops=frozenset({"&&", "||"}),
        nesting=frozenset({"if_statement", "for_statement", "enhanced_for_statement", "while_statement", "do_statement",
                           "switch_expression", "try_statement", "try_with_resources_statement"}),
        loops=frozenset({"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}),
        calls=frozenset({"method_invocation", "object_creation_expression"}), awaits=frozenset(),
        yields=frozenset({"yield_statement"}), scoped=frozenset({"try_with_resources_statement", "synchronized_statement"}),
        assign_parents=frozenset({"variable_declarator"})),
}
EXTENSION_LANGUAGE = {".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
                      ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".rs": "rust", ".java": "java"}
_BITOPS = frozenset({"&", "|", "^", "<<", ">>", ">>>"})
_COMMENT_TYPES = frozenset({"comment", "line_comment", "block_comment"})
_NAME_TYPES = ("identifier", "property_identifier", "field_identifier", "type_identifier", "shorthand_property_identifier")


def supported(language: str) -> bool:
    return language in SPECS


def _text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _callee(node: Any) -> Optional[str]:
    """The last identifier of what is being called (`a.b.c(x)` -> `c`)."""
    target = (node.child_by_field_name("function") or node.child_by_field_name("name")
              or node.child_by_field_name("constructor") or node.child_by_field_name("type") or node.child_by_field_name("macro"))
    if target is None and node.named_child_count:
        target = node.named_children[0]
    while target is not None and target.type not in _NAME_TYPES:
        nxt = (target.child_by_field_name("property") or target.child_by_field_name("field")
               or target.child_by_field_name("name"))
        if nxt is None:
            named = target.named_children
            nxt = named[-1] if named else None
        if nxt is None or nxt is target:
            break
        target = nxt
    text = _text(target)
    return text or None


def _name_of(node: Any) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return _text(name)
    for child in node.children:
        if child.type in _NAME_TYPES:
            return _text(child)
    return "?"


def _last_identifier(node: Any) -> str:
    """`res.send` -> `send`, `module.exports.handler` -> `handler`, `x` -> `x`, `"key"` -> `key`."""
    while node is not None and node.type not in _NAME_TYPES and node.type not in {"string", "computed_property_name"}:
        nxt = node.child_by_field_name("property") or node.child_by_field_name("field") or node.child_by_field_name("name")
        if nxt is None and node.named_children:
            nxt = node.named_children[-1]
        if nxt is None or nxt is node:
            break
        node = nxt
    return _text(node).strip("'\"`")


def _lambda_name(node: Any, spec: LangSpec) -> Optional[str]:
    """What an anonymous function is called: its own name (`function send(...)`), else what it is assigned to."""
    own = node.child_by_field_name("name")
    if own is not None and _text(own):
        return _text(own)
    parent = node.parent
    if parent is None or parent.type not in spec.assign_parents:
        return None
    if parent.type == "pair":
        target = parent.child_by_field_name("key")
    elif parent.type == "assignment_expression":
        target = parent.child_by_field_name("left")
    else:
        target = parent.child_by_field_name("name") or parent.child_by_field_name("left") or parent.child_by_field_name("pattern")
        if target is None and parent.named_children:
            target = parent.named_children[0]
    name = _last_identifier(target) if target is not None else ""
    return name.split(",")[0].strip() or None


def _lambda_declaration(node: Any) -> Any:
    """The enclosing statement that gives the function its context (`const f = ...`, `res.send = ...;`, `key: ...`)."""
    parent = node.parent
    if parent is None:
        return node
    decl = parent
    if parent.type == "variable_declarator" and parent.parent is not None \
            and parent.parent.type in {"lexical_declaration", "variable_declaration"}:
        decl = parent.parent
    elif parent.type == "assignment_expression" and parent.parent is not None and parent.parent.type == "expression_statement":
        decl = parent.parent
    if decl.parent is not None and decl.parent.type == "export_statement":
        decl = decl.parent
    return decl


def _statements(body: Any) -> list[Any]:
    if body is None:
        return []
    kids = [c for c in body.named_children if c.type not in _COMMENT_TYPES]
    if len(kids) == 1 and kids[0].type in {"statement_list"}:
        kids = [c for c in kids[0].named_children if c.type not in _COMMENT_TYPES]
    return kids


def _trivial_reason(node: Any, spec: LangSpec, kind: str, code_lines: int, branches: int, calls: set[str]) -> Optional[str]:
    body = next((node.child_by_field_name(f) for f in spec.body_fields if node.child_by_field_name(f) is not None), None)
    if kind != "class":
        stmts = _statements(body)
        if body is not None and body.type in {"statement_block", "block", "constructor_body"} and not stmts:
            return "empty_or_docstring_only"
        if len(stmts) == 1:
            only = stmts[0]
            text = _text(only)
            if only.type in {"return_statement", "expression_statement"}:
                arg = only.named_children[0] if only.named_children else None
                if only.type == "return_statement":
                    if arg is None or arg.type in {"identifier", "number", "string", "true", "false", "null", "this", "nil",
                                                   "integer_literal", "string_literal"}:
                        return "constant_or_name_return"
                    if arg.type in {"member_expression", "selector_expression", "field_expression", "field_access"}:
                        return "attribute_getter"
                    if arg.type in spec.calls and branches == 0 and len(calls) <= 1:
                        return "pure_delegate"
                elif arg is not None and arg.type in {"assignment_expression", "augmented_assignment_expression"}:
                    return "attribute_setter"
                elif arg is not None and arg.type in spec.calls and branches == 0 and len(calls) <= 1:
                    return "pure_delegate"
            if only.type in {"throw_statement", "panic"} and re.search(r"not.?implemented|unimplemented|todo", text, re.I):
                return "abstract_stub"
        if kind == "function" and stmts and all(s.type in {"expression_statement"} and "=" in _text(s).split("(")[0]
                                                for s in stmts) and branches == 0 and not calls:
            return "attribute_setter"
    if code_lines < 4:
        return "too_small"
    return None


def _preceded_by_doc(node: Any) -> bool:
    anchor = node
    if node.parent is not None and node.parent.type == "export_statement":
        anchor = node.parent
    prev = anchor.prev_sibling
    return bool(prev is not None and prev.type in _COMMENT_TYPES and prev.end_point[0] >= anchor.start_point[0] - 1)


def _code_line_count(lines: list[str], start: int, end: int) -> int:
    count = 0
    in_block = False
    for raw in lines[start - 1:end]:
        text = raw.strip()
        if not text:
            continue
        if in_block:
            if "*/" in text:
                in_block = False
            continue
        if text.startswith("/*"):
            in_block = "*/" not in text
            continue
        if text.startswith(("//", "*", "#")):
            continue
        count += 1
    return count


def _features_for(node: Any, spec: LangSpec, kind: str, name: str, lines: list[str], decl: Any) -> SpanFeatures:
    branches = 0
    bool_extra = 0
    calls: set[str] = set()
    has_loop = has_await = has_yield = scoped = bitops = False
    max_nest = 0
    tokens: list[str] = []
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        cur, depth = stack.pop()
        t = cur.type
        if cur.is_named and t not in _COMMENT_TYPES:
            tokens.append(t)
        if t in spec.branches:
            branches += 1
        if t == "binary_expression" or t == "binary_operator":
            op = _text(cur.child_by_field_name("operator"))
            if op in spec.bool_ops:
                bool_extra += 1
            elif op in _BITOPS:
                bitops = True
        if t in spec.loops:
            has_loop = True
        if t in spec.calls:
            callee = _callee(cur)
            if callee:
                calls.add(callee)
        if t in spec.awaits:
            has_await = True
        if t in spec.yields:
            has_yield = True
        if t in spec.scoped:
            scoped = True
        if t in {"regex", "regex_literal"}:
            bitops = True
        next_depth = depth + 1 if t in spec.nesting else depth
        max_nest = max(max_nest, next_depth)
        stack.extend((child, next_depth) for child in reversed(cur.children) if child.is_named)
    start = (decl.start_point[0] + 1) if decl is not None else node.start_point[0] + 1
    end = node.end_point[0] + 1
    code_lines = _code_line_count(lines, start, end)
    is_async = has_await or any(c.type == "async" for c in node.children)
    return SpanFeatures(
        name=name, kind=kind, line_start=start, line_end=end, code_lines=code_lines,
        cyclomatic=1 + branches + bool_extra, max_nesting=max_nest, distinct_calls=len(calls), branches=branches,
        has_loop=has_loop, has_recursion=name in calls, is_async=is_async, is_generator=has_yield,
        uses_context_manager=scoped, uses_regex_or_bitops=bitops, has_docstring=_preceded_by_doc(decl or node),
        annotated_fraction=0.0, trivial_reason=_trivial_reason(node, spec, kind, code_lines, branches, calls),
        fingerprint=hashlib.sha1(" ".join(tokens).encode("utf-8")).hexdigest()[:16], calls=frozenset(calls))


def extract_spans(source: str, language: str) -> list[SpanFeatures]:
    """Functions, methods and classes (methods when the class is too long to be one span). Syntax errors -> no spans."""
    spec = SPECS.get(language)
    if spec is None:
        return []
    from tree_sitter_language_pack import get_parser

    tree = get_parser(spec.tree_language).parse(source.encode("utf-8"))
    root = tree.root_node
    if root.has_error:
        return []
    lines = source.splitlines()
    out: list[SpanFeatures] = []

    def visit(parent: Any, in_class: bool) -> None:
        for child in parent.children:
            t = child.type
            if t in spec.functions:
                out.append(_features_for(child, spec, "method" if in_class else "function", _name_of(child), lines, child))
            elif t in spec.lambdas and (name := _lambda_name(child, spec)):
                out.append(_features_for(child, spec, "function", name, lines, _lambda_declaration(child)))
            elif t in spec.classes:
                whole = _features_for(child, spec, "class", _name_of(child), lines, child)
                if whole.lines <= MAX_SPAN_LINES:
                    body = child.child_by_field_name("body")
                    members = [_features_for(m, spec, "method", _name_of(m), lines, m)
                               for m in (body.children if body is not None else []) if m.type in spec.functions]
                    if members and all(m.trivial_reason for m in members):
                        whole = dataclasses.replace(whole, trivial_reason="plumbing_class")
                    out.append(whole)
                else:
                    visit(child, True)
            elif t in spec.containers:
                visit(child, in_class or t in {"class_body", "impl_item", "declaration_list", "enum_body"})

    visit(root, False)
    return out
