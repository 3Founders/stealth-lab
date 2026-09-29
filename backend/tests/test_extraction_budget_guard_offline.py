"""
Proving tests for the pre-spend budget guard on every paid extraction path.

The audit finding these close: the extraction paths called
`ingest_budget.record_completion` and never `ingest_budget.guard`. Spend was
therefore written to the ledger after every call and checked before none of
them, so `DAILY_LLM_BUDGET_USD` was a post-hoc tally rather than a ceiling.
Four call sites, one shape:

- `app.services.repo_ingestion._one_provider_call`  (already had both)
- `app.services.trajectory_semantics.extract_trajectory_semantics` (had neither)
- `app.services.claim_extraction.extract_claim_candidates_cached`
- `app.services.skill_extraction.grounded.extract_document`
- `app.services.skill_extraction.ungrounded.extract_document`

This file is deliberately a SWEEP over those call sites rather than five
hand-written tests: the failure mode being closed is "someone adds a new
extraction path and forgets the gate", and a per-site test is exactly what that
failure mode survives.

Offline: no database, no network, no LLM, real `ingest_budget` with a
`CostGovernor`-shaped fake pool. Stubbing `guard` to a recorder would prove
nothing about the gate, so the real guard runs against a fake ledger.
"""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from app.services import ingest_budget
from app.services.governance import BudgetExceeded
import app.services.skill_extraction.grounded as grounded
import app.services.skill_extraction.ungrounded as ungrounded


class LedgerPool:
    def __init__(self, spent: float):
        self.spent = spent
        self.inserted: list[tuple] = []

    async def fetchval(self, sql, *args):
        return self.spent

    async def execute(self, sql, *args):
        self.inserted.append((sql, args))


class CountingClient:
    def __init__(self, content: str):
        self.calls = 0
        self._content = content

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls += 1
            return type("R", (), {
                "choices": [type("C", (), {
                    "message": type("M", (), {"content": self._outer._content})(),
                    "finish_reason": "stop",
                })()],
                "usage": type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})(),
            })()

    @property
    def chat(self):
        outer = self

        class _Chat:
            completions = CountingClient._Completions(outer)

        return _Chat()


@pytest.fixture
def budget():
    ingest_budget.uninstall()
    yield ingest_budget
    ingest_budget.uninstall()


# --------------------------------------------------------------- the sweep

#: (module, function, the paid call is the first thing after the guard).
PAID_EXTRACTION_PATHS = [
    ("app.services.repo_ingestion", "_one_provider_call"),
    ("app.services.trajectory_semantics", "extract_trajectory_semantics"),
    ("app.services.claim_extraction", "extract_claim_candidates_cached"),
    ("app.services.skill_extraction.grounded", "extract_document"),
    ("app.services.skill_extraction.ungrounded", "extract_document"),
]


@pytest.mark.parametrize("module_name,func_name", PAID_EXTRACTION_PATHS)
def test_every_paid_extraction_path_checks_the_budget_before_it_spends(module_name, func_name):
    """Structural sweep. `guard` must be called BEFORE the provider call in the
    function body -- ordering is the entire property.

    `ast`, not string offsets: a comment that mentions `completions.create`
    (which the fix itself does, explaining what it replaced) must not read as
    a call site, and a line-number comparison is what a reviewer can check.
    """
    import ast
    import importlib

    mod = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(getattr(mod, func_name)).lstrip())
    fn = tree.body[0]
    assert isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)), tree.body[0]

    def _lines_where(pred) -> list[int]:
        return sorted({node.lineno for node in ast.walk(fn) if pred(node)})

    def _dotted(node) -> str:
        """`ingest_budget.guard` -> "ingest_budget.guard"; imports are `from
        app.services import ingest_budget`, so the receiver is a Name, not an
        Attribute."""
        if isinstance(node, ast.Attribute):
            return f"{_dotted(node.value)}.{node.attr}"
        if isinstance(node, ast.Name):
            return node.id
        return ""

    guard_lines = _lines_where(
        lambda n: isinstance(n, ast.Call) and _dotted(n.func) == "ingest_budget.guard"
    )
    assert guard_lines, f"{module_name}.{func_name} records spend but never guards it"

    # The provider call appears in two shapes across this repo: called
    # directly, or handed to `asyncio.to_thread` (a sync client kept off the
    # event loop) which is where the actual call lives. Both are a spend, and
    # both have to sit after the guard.
    spend_lines = _lines_where(
        lambda n: (
            _dotted(n).endswith("completions.create")
            or (isinstance(n, ast.Call) and _dotted(n.func) == "asyncio.to_thread")
        )
    )
    assert spend_lines, (
        f"{module_name}.{func_name} has no provider call -- the sweep is stale, "
        f"this path was refactored and needs re-pinning"
    )
    assert min(guard_lines) < min(spend_lines), (
        f"{module_name}.{func_name} calls the provider on line {min(spend_lines)} "
        f"but only guards on line {min(guard_lines)}: the cap is post-hoc"
    )


@pytest.mark.parametrize("module_name,func_name", PAID_EXTRACTION_PATHS)
def test_every_paid_extraction_path_records_its_spend(module_name, func_name):
    import importlib

    mod = importlib.import_module(module_name)
    src = inspect.getsource(getattr(mod, func_name))
    if module_name == "app.services.trajectory_semantics":
        assert "ingest_budget.record_completion(" in src
        return
    # The other four funnel their recording through a helper or a usage sink.
    assert (
        "record_completion(" in src
        or "record_judge(" in src
        or "usage_sink" in src
        or "flush_spend" in src
    ), f"{module_name}.{func_name} spends money and records nothing"


def test_no_paid_path_in_these_modules_escapes_the_gate():
    """Belt and braces: no other function in these modules calls
    `completions.create` at all. If one appears, this sweep above is
    incomplete and should be extended rather than quietly bypassed."""
    import ast
    import importlib

    def _calls_provider(obj) -> bool:
        try:
            tree = ast.parse(inspect.getsource(obj))
        except (OSError, TypeError, SyntaxError):
            return False
        return any(
            isinstance(n, ast.Attribute) and n.attr == "create"
            and isinstance(n.value, ast.Attribute) and n.value.attr == "completions"
            for n in ast.walk(tree)
        )

    allowed_by_module = {
        # The single guarded orchestrator; the compaction judge it delegates to
        # is budgeted by its own caller.
        "app.services.trajectory_semantics": {"extract_trajectory_semantics"},
        "app.services.repo_ingestion": {"_one_provider_call"},
        # `_extract_from_chunk` is the sync per-chunk worker the async wrapper
        # guards once and then runs in a thread.
        "app.services.claim_extraction": {"_extract_from_chunk"},
        "app.services.skill_extraction.grounded": {"extract_document"},
        "app.services.skill_extraction.ungrounded": {"extract_document"},
    }
    for module_name, allowed in allowed_by_module.items():
        mod = importlib.import_module(module_name)
        callers = {
            name for name, obj in vars(mod).items()
            if inspect.isfunction(obj) and obj.__module__ == mod.__name__
            and _calls_provider(obj)
        }
        assert callers <= allowed, (
            f"{module_name}: {sorted(callers - allowed)} call a provider "
            f"outside the swept path"
        )


# ---------------------------------------------------- the guard really stops

@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["grounded", "ungrounded"])
async def test_skill_extraction_refuses_before_spending_when_over_cap(budget, which):
    mod = grounded if which == "grounded" else ungrounded
    budget.install(LedgerPool(spent=50.0), cap_usd=10.0, cache_s=0)
    client = CountingClient("{}")

    with pytest.raises(BudgetExceeded):
        await mod.extract_document(client, "some document text")

    assert client.calls == 0, "the provider was called while over the daily cap"


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["grounded", "ungrounded"])
async def test_skill_extraction_spends_normally_under_the_cap(budget, which):
    """The gate must not be a blanket refusal: under the cap the call happens
    and the response is returned."""
    mod = grounded if which == "grounded" else ungrounded
    budget.install(LedgerPool(spent=0.0), cap_usd=10.0, cache_s=0)
    client = CountingClient(json.dumps({"procedures": [], "goals": [], "implementations": [],
                                        "reference_resources": []}))

    await mod.extract_document(client, "some document text")

    assert client.calls == 1


@pytest.mark.asyncio
async def test_skill_extraction_budget_stop_is_not_a_transient_failure(budget):
    """The extraction paths convert everything into
    `SkillExtractionTransientFailure`, which callers treat as a retryable
    rejection. A cost stop is not retryable, so it must not be laundered
    through that exception -- hence the guard sits outside the try/except."""
    from app.services.skill_extraction.schema import SkillExtractionTransientFailure

    budget.install(LedgerPool(spent=50.0), cap_usd=10.0, cache_s=0)
    client = CountingClient("{}")

    with pytest.raises(BudgetExceeded) as excinfo:
        await grounded.extract_document(client, "doc")
    assert not isinstance(excinfo.value, SkillExtractionTransientFailure)

    src = inspect.getsource(grounded.extract_document)
    assert src.index("ingest_budget.guard(") < src.index("try:"), (
        "the guard must precede the try/except that converts failures"
    )


@pytest.mark.asyncio
async def test_no_budget_installed_leaves_every_path_unchanged(budget):
    """The API and MCP surfaces never install one. A guard that raised without
    one would break every non-ingestion extraction call."""
    assert ingest_budget.active() is None
    client = CountingClient("{}")
    await grounded.extract_document(client, "doc")
    assert client.calls == 1
