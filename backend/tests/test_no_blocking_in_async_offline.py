"""Static guard: no `async def` on the ingestion path reaches a blocking call, directly or through sync helpers.

docs/ingestion_review.md, cross-cutting lesson 1: every ingestion step independently shipped a "blocking call
inside async" (a sync HF generator, subprocess.run, httpx.get, thread-pool fetches with sleeps) that froze the
worker's event loop -- and a one-level check missed the ones hidden behind a helper. This test parses the
modules and follows calls to same-module sync functions (plain names and self.method) up to MAX_DEPTH levels.

Allowed: anything inside a call to run_blocking / asyncio.to_thread / loop.run_in_executor (their function or
lambda argument runs off the loop). Nested defs are separate scopes and are only followed when called.
A real exception goes in ALLOWED with the reason.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
SCOPE = [APP / "services" / "ingestion_sources", APP / "ingestion"]
MAX_DEPTH = 3

BLOCKING = {
    ("time", "sleep"),
    ("subprocess", "run"), ("subprocess", "call"), ("subprocess", "check_call"), ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("requests", "get"), ("requests", "post"), ("requests", "put"), ("requests", "delete"), ("requests", "head"),
    ("requests", "patch"), ("requests", "request"),
    ("httpx", "get"), ("httpx", "post"), ("httpx", "put"), ("httpx", "delete"), ("httpx", "head"),
    ("httpx", "patch"), ("httpx", "request"), ("httpx", "stream"),
    ("urllib.request", "urlopen"), ("request", "urlopen"),
    ("datasets", "load_dataset"),
}
OFFLOADERS = {"run_blocking", "to_thread", "run_in_executor"}
# The SourceAdapter contract's methods are SYNC and do network I/O (dataset streaming, raw fetches): calling one
# on the event loop without `await` is the step-3 bug (`source.discover()` in skillmd_pilot). An awaited call of
# the same name (e.g. asyncpg's `await pool.fetch(...)`) is async and fine.
SOURCE_METHODS = {"discover", "fetch", "iter_rows", "iter_admissible"}
# (module path relative to app/, async function, blocking call) -> reason. Keep this empty unless justified.
ALLOWED: dict[tuple[str, str, str], str] = {
    ("services/ingestion_sources/verified_solutions_jobs.py", "enqueue_verified_solution_jobs",
     "source.iter_admissible (sync adapter method)"):
        "creating the generator runs no code; the next line advances it with run_blocking(next, rows, None)",
    ("ingestion/step6_admin.py", "_ci_workflows", "source.discover (sync adapter method)"):
        "one-shot admin probe (its own asyncio.run, nothing else on the loop): blocking delays nobody",
    ("ingestion/step6_admin.py", "_ci_workflows", "source.fetch (sync adapter method)"):
        "one-shot admin probe (its own asyncio.run, nothing else on the loop): blocking delays nobody",
}


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _blocking_name(call: ast.Call) -> str | None:
    name = _dotted(call.func)
    if "." not in name:
        return None
    mod, attr = name.rsplit(".", 1)
    return name if (mod, attr) in BLOCKING else None


class _Calls(ast.NodeVisitor):
    """Calls made by one function body, skipping offloaded arguments and nested defs."""

    def __init__(self, *, in_async: bool = False):
        self.blocking: list[tuple[str, int]] = []
        self.local: list[tuple[str, int]] = []
        self._awaited: set[int] = set()
        self._in_async = in_async

    def visit_Await(self, node: ast.Await):
        if isinstance(node.value, ast.Call):
            self._awaited.add(id(node.value))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        fname = _dotted(node.func).rsplit(".", 1)[-1]
        if fname in OFFLOADERS:
            return                                   # its arguments run off the event loop
        b = _blocking_name(node)
        if b:
            self.blocking.append((b, node.lineno))
        elif (self._in_async and isinstance(node.func, ast.Attribute) and node.func.attr in SOURCE_METHODS
              and id(node) not in self._awaited and _dotted(node.func.value) != "self"):
            self.blocking.append((f"{_dotted(node.func)} (sync adapter method)", node.lineno))
        name = _dotted(node.func)
        if isinstance(node.func, ast.Name):
            self.local.append((name, node.lineno))
        elif name.startswith("self.") and name.count(".") == 1:
            self.local.append((name.split(".", 1)[1], node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):              # nested sync def: not executed unless called
        return

    def visit_AsyncFunctionDef(self, node):
        return

    def visit_Lambda(self, node):
        return


def _calls(fn) -> _Calls:
    v = _Calls(in_async=isinstance(fn, ast.AsyncFunctionDef))
    for stmt in fn.body:
        v.visit(stmt)
    return v


def _violations_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sync_fns: dict[str, ast.FunctionDef] = {}
    async_fns: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            sync_fns.setdefault(node.name, node)
        elif isinstance(node, ast.AsyncFunctionDef):
            async_fns.append(node)
    rel = path.relative_to(APP).as_posix()
    found = []
    for afn in async_fns:
        seen: set[str] = set()
        frontier = [(afn, [afn.name])]
        for depth in range(MAX_DEPTH + 1):
            nxt = []
            for fn, chain in frontier:
                c = _calls(fn)
                for call, line in c.blocking:
                    if (rel, afn.name, call) not in ALLOWED:
                        found.append(f"{rel}:{line}  {' -> '.join(chain)} -> {call}()")
                for name, _line in c.local:
                    target = sync_fns.get(name)
                    if target is not None and name not in seen:
                        seen.add(name)
                        nxt.append((target, chain + [name]))
            frontier = nxt
            if not frontier:
                break
    return found


def _scope_files():
    for root in SCOPE:
        yield from sorted(root.rglob("*.py"))


def test_no_async_function_on_the_ingestion_path_reaches_a_blocking_call():
    violations = [v for f in _scope_files() for v in _violations_in(f)]
    assert not violations, "blocking call reachable from async code (wrap it in run_blocking):\n" + "\n".join(violations)


def test_the_guard_catches_a_blocking_call_two_helpers_deep(tmp_path):
    """The guard itself: a direct call, one hidden two sync helpers deep, and an offloaded one (allowed)."""
    src = tmp_path / "app" / "mod.py"
    src.parent.mkdir()
    src.write_text(
        "import time, subprocess\n"
        "from app.utils.aio import run_blocking\n"
        "def inner():\n    subprocess.run(['x'])\n"
        "def middle():\n    inner()\n"
        "async def direct():\n    time.sleep(1)\n"
        "async def hidden():\n    middle()\n"
        "async def offloaded():\n    await run_blocking(middle)\n    await run_blocking(lambda: time.sleep(1))\n"
        "async def adapter_on_loop(source):\n    return list(source.discover())\n"
        "async def adapter_offloaded(source, pool):\n"
        "    refs = await run_blocking(lambda: list(source.discover()))\n"
        "    rows = await pool.fetch('SELECT 1')\n    return refs, rows\n"
    )
    global APP
    old, APP = APP, tmp_path / "app"
    try:
        found = _violations_in(src)
    finally:
        APP = old
    assert any("direct -> time.sleep" in f for f in found)
    assert any("hidden -> middle -> inner -> subprocess.run" in f for f in found)
    assert not any("offloaded ->" in f or f.endswith("offloaded") for f in found)
    assert any("adapter_on_loop -> source.discover (sync adapter method)" in f for f in found)
    assert not any("adapter_offloaded" in f for f in found), "offloaded adapter call and awaited pool.fetch are fine"
