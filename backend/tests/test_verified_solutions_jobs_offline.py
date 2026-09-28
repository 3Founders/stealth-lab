"""
Proving tests for the `ingest_verified_solution` job wiring.

Offline and zero-spend: `compile_skill_artifact` is replaced with a recorder,
so no LLM is ever constructed and no pool is ever touched for real. What is
proven here is the WIRING, which is the part that can be silently wrong:

- the job type is actually registered (an unregistered type fails the job
  loudly at consume time, not at enqueue time);
- a payload that would ingest without provenance is REFUSED, loudly;
- the artifact handed to the compiler carries commit, path, source id and
  license_metadata, because `verified_solutions.durable_locator` needs
  commit+path+(repository|uri) to build a real `source_locator`;
- the admission gates run BEFORE anything is enqueued, so a corpus that is
  entirely unlicensed enqueues nothing and costs nothing;
- the job type is public-only, so it cannot be enqueued under a narrower
  scope than the knowledge it produces.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.queue import PUBLIC_ONLY_JOB_TYPES, ScopeError
from app.services.ingestion_sources import verified_solutions_jobs as vsj

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATHS = (
    REPO_ROOT / "experiments" / "swebench" / "runs" / "design.json",
    REPO_ROOT / "experiments" / "swebench_rebench" / "runs" / "design.json",
)


class FakePool:
    """Captures enqueue calls; never a connection."""

    def __init__(self, *, already: bool = False) -> None:
        self.calls: list[dict] = []
        self._already = already
        self._n = 0

    async def fetchrow(self, sql, *args):
        self.calls.append({"sql": " ".join(sql.split()), "args": args})
        self._n += 1
        if self._already:
            return None
        return {"id": 1000 + self._n}

    async def fetchval(self, sql, *args):
        """The queue's existing-job probe. Non-None means the idempotency key
        is already present, so the INSERT above returned no row."""
        return 777 if self._already else None


def _payload(**over):
    base = {
        "source_key": "swe_bench_extra",
        "instance_id": "acme__widget-42",
        "repo": "acme/widget",
        "base_commit": "b" * 40,
        "uri": "hf://nebius/SWE-bench-extra@11dcbfb3/train/acme__widget-42",
        "content": "---\nname: widget-42\n---\n\n# widget-42\n",
        "license_raw": "mit",
        "license_spdx": "MIT",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_job_type_is_registered():
    from app.services.ingestion_jobs import JOB_HANDLERS

    assert vsj.JOB_TYPE in JOB_HANDLERS
    assert JOB_HANDLERS[vsj.JOB_TYPE] is vsj.handle_ingest_verified_solution


def test_job_type_is_public_only():
    assert vsj.JOB_TYPE in PUBLIC_ONLY_JOB_TYPES


# ---------------------------------------------------------------------------
# Handler refusals -- loud, never a silent ingest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_content_is_refused_loudly():
    with pytest.raises(ValueError, match="no gated document"):
        await vsj.handle_ingest_verified_solution(FakePool(), _payload(content=None))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "over", [{"instance_id": ""}, {"base_commit": ""}]
)
async def test_missing_provenance_is_refused_loudly(over):
    with pytest.raises(ValueError, match="missing instance_id/base_commit"):
        await vsj.handle_ingest_verified_solution(FakePool(), _payload(**over))


# ---------------------------------------------------------------------------
# Artifact handed to the compiler
# ---------------------------------------------------------------------------

def _record_compile(monkeypatch, *, client="client"):
    """Replace the compiler with a recorder and capture the artifact."""
    from app.services import skill_ingestion

    seen: dict = {}

    async def fake_compile(pool, artifact, **kwargs):
        seen["artifact"] = artifact
        seen["kwargs"] = kwargs

        class _Outcome:
            status = "captured"

        return _Outcome()

    monkeypatch.setattr(skill_ingestion, "compile_skill_artifact", fake_compile)
    monkeypatch.setattr(
        vsj, "_trusted_identity_job_id", lambda payload: None, raising=False
    )
    return seen


@pytest.mark.asyncio
async def test_artifact_carries_provenance_the_locator_needs(monkeypatch):
    """`durable_locator` requires commit AND path AND (repository or uri).
    Losing any of them silently degrades a Procedure's source_locator."""
    import app.services.ingestion_jobs as ij
    from app.services.ingestion_sources.base import compute_content_hash

    monkeypatch.setattr(ij, "_general_compute_client", lambda: object())
    seen = _record_compile(monkeypatch)

    await vsj.handle_ingest_verified_solution(FakePool(), _payload())

    art = seen["artifact"]
    assert art.commit == "b" * 40
    assert art.path == "instances/acme__widget-42"
    assert art.repository == "acme/widget"
    assert art.uri
    assert art.source_id == "acme__widget-42"
    assert art.content_hash == compute_content_hash(art.content)
    assert art.license_metadata == {"license": "mit", "spdx_id": "MIT"}


@pytest.mark.asyncio
async def test_artifact_source_type_is_the_verified_solution_role(monkeypatch):
    import app.services.ingestion_jobs as ij

    monkeypatch.setattr(ij, "_general_compute_client", lambda: object())
    seen = _record_compile(monkeypatch)
    await vsj.handle_ingest_verified_solution(FakePool(), _payload())
    assert seen["artifact"].source_type == "verified_solution"


@pytest.mark.asyncio
async def test_licence_metadata_reaches_the_compiler_even_when_unmapped(monkeypatch):
    """`spdx_id=None` is the signal that arms the denylist downstream. Dropping
    the key entirely would let a copyleft row through."""
    import app.services.ingestion_jobs as ij

    monkeypatch.setattr(ij, "_general_compute_client", lambda: object())
    seen = _record_compile(monkeypatch)
    await vsj.handle_ingest_verified_solution(
        FakePool(), _payload(license_raw=None, license_spdx=None)
    )
    assert "spdx_id" in seen["artifact"].license_metadata
    assert seen["artifact"].license_metadata["spdx_id"] is None


@pytest.mark.asyncio
async def test_no_llm_client_still_calls_the_compiler_so_the_refusal_is_recorded(
    monkeypatch, caplog
):
    """No configured client must degrade to a REFUSED artifact inside the
    compiler, which records the reason -- not to a silent deterministic
    capture (founder directive 2026-09-15)."""
    import app.services.ingestion_jobs as ij

    monkeypatch.setattr(ij, "_general_compute_client", lambda: None)
    seen = _record_compile(monkeypatch)

    with caplog.at_level("WARNING"):
        await vsj.handle_ingest_verified_solution(FakePool(), _payload())

    assert seen["kwargs"]["client"] is None
    assert any("no LLM client configured" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Enqueue side -- gates before spend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_gates_before_it_enqueues(monkeypatch):
    """A row that fails a gate must never reach enqueue, because enqueue is
    the step that makes the work real."""
    from app.services.ingestion_sources import verified_solutions_hf as vs

    rows = [
        {"instance_id": "a__b-1", "repo": "a/b", "base_commit": "c" * 40,
         "problem_statement": "x", "patch": "d", "test_patch": "t",
         "FAIL_TO_PASS": ["t::1"], "license": "mit"},
        {"instance_id": "a__b-2", "repo": "a/b", "base_commit": "d" * 40,
         "problem_statement": "x", "patch": "d", "test_patch": "t",
         "FAIL_TO_PASS": [], "license": "mit"},
        {"instance_id": "django__django-1", "repo": "django/django",
         "base_commit": "e" * 40, "problem_statement": "x", "patch": "d",
         "test_patch": "t", "FAIL_TO_PASS": ["t::1"], "license": "mit"},
    ]
    monkeypatch.setattr(
        vs.VerifiedSolutionSource, "_iter_raw_rows", lambda self: iter(rows)
    )
    pool = FakePool()
    out = await vsj.enqueue_verified_solution_jobs(
        pool, source_key="swe_bench_extra", target=10, design_paths=DESIGN_PATHS
    )
    assert len(pool.calls) == 1
    assert out["enqueued"] == 1
    assert out["rejected_no_fail_to_pass"] == 1
    assert out["rejected_held_out_repo"] == 1


@pytest.mark.asyncio
async def test_unlicensed_corpus_enqueues_nothing_and_costs_nothing(monkeypatch):
    """SWE-Gym's shape: rows present, no license field, zero admissible."""
    from app.services.ingestion_sources import verified_solutions_hf as vs

    rows = [
        {"instance_id": f"a__b-{i}", "repo": "a/b", "base_commit": f"{i:040d}",
         "problem_statement": "x", "patch": "d", "test_patch": "t",
         "FAIL_TO_PASS": ["t::1"]}
        for i in range(25)
    ]
    monkeypatch.setattr(
        vs.VerifiedSolutionSource, "_iter_raw_rows", lambda self: iter(rows)
    )
    pool = FakePool()
    out = await vsj.enqueue_verified_solution_jobs(
        pool, source_key="swe_gym", target=10, design_paths=DESIGN_PATHS
    )
    assert out["enqueued"] == 0
    assert out["rejected_license_unmappable"] == 25
    assert pool.calls == []


@pytest.mark.asyncio
async def test_idempotency_key_is_stable_per_revision(monkeypatch):
    from app.services.ingestion_sources import verified_solutions_hf as vs

    rows = [
        {"instance_id": "a__b-1", "repo": "a/b", "base_commit": "c" * 40,
         "problem_statement": "x", "patch": "d", "test_patch": "t",
         "FAIL_TO_PASS": ["t::1"], "license": "mit"},
    ]
    monkeypatch.setattr(
        vs.VerifiedSolutionSource, "_iter_raw_rows", lambda self: iter(rows)
    )
    pool = FakePool()
    await vsj.enqueue_verified_solution_jobs(
        pool, source_key="swe_bench_extra", target=5, design_paths=DESIGN_PATHS
    )
    key = next(c["args"][2] for c in pool.calls if "idempotency_key" in c["sql"])
    assert key == "swe_bench_extra:a__b-1:" + "c" * 12


@pytest.mark.asyncio
async def test_existing_job_is_not_double_enqueued(monkeypatch):
    from app.services.ingestion_sources import verified_solutions_hf as vs

    rows = [
        {"instance_id": "a__b-1", "repo": "a/b", "base_commit": "c" * 40,
         "problem_statement": "x", "patch": "d", "test_patch": "t",
         "FAIL_TO_PASS": ["t::1"], "license": "mit"},
    ]
    monkeypatch.setattr(
        vs.VerifiedSolutionSource, "_iter_raw_rows", lambda self: iter(rows)
    )
    out = await vsj.enqueue_verified_solution_jobs(
        FakePool(already=True), source_key="swe_bench_extra", target=5,
        design_paths=DESIGN_PATHS,
    )
    assert out["enqueued"] == 0
    assert out["already_present"] == 1


@pytest.mark.asyncio
async def test_unknown_source_key_raises():
    with pytest.raises(KeyError):
        await vsj.enqueue_verified_solution_jobs(
            FakePool(), source_key="nope", target=1, design_paths=DESIGN_PATHS
        )


def test_public_only_scope_is_enforced_for_this_job_type():
    """The gate that stops a private-scoped enqueue of public knowledge."""
    from app.ingestion.queue import validate_scope

    validate_scope(vsj.JOB_TYPE, "global", "public", None)
    with pytest.raises(ScopeError):
        validate_scope(vsj.JOB_TYPE, "project", "private", "owner-1")


# ---------------------------------------------------------------------------
# The gold patch becomes a durable verified_solution artifact
# ---------------------------------------------------------------------------

def _stub_compile_and_preserve(monkeypatch, *, status="captured", row_id="11111111-1111-1111-1111-111111111111"):
    import app.services.ingestion_jobs as ij
    from app.services import skill_ingestion, verified_solutions

    calls: list = []

    async def fake_compile(pool, artifact, **kwargs):
        return type("_Outcome", (), {"status": status, "version_row_id": row_id})()

    async def fake_preserve(pool, **kw):
        calls.append(kw)
        return {"artifact_id": "a", "role": "verified_solution"}

    monkeypatch.setattr(ij, "_general_compute_client", lambda: object())
    monkeypatch.setattr(skill_ingestion, "compile_skill_artifact", fake_compile)
    monkeypatch.setattr(verified_solutions, "preserve", fake_preserve)
    monkeypatch.setattr(vsj, "_trusted_identity_job_id", lambda payload: None, raising=False)
    return calls


@pytest.mark.asyncio
async def test_captured_solution_preserves_the_gold_patch_and_its_check(monkeypatch):
    calls = _stub_compile_and_preserve(monkeypatch)
    await vsj.handle_ingest_verified_solution(FakePool(), _payload(
        gold_patch="diff --git a/x.py b/x.py\n+fix\n", fail_to_pass=["tests/test_x.py::test_fix"],
        problem_statement="widget crashes on empty input"))
    assert len(calls) == 1
    kw = calls[0]
    assert kw["procedure_row_id"] == "11111111-1111-1111-1111-111111111111"
    assert kw["code"].startswith("diff --git")
    assert kw["verified_by"] == "FAIL_TO_PASS: tests/test_x.py::test_fix"
    assert kw["task"] == "widget crashes on empty input"
    assert kw["locator"] is None            # a dataset row is not a fetchable file: store the patch itself


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["duplicate", "unchanged", "rejected"])
async def test_no_new_version_means_nothing_is_preserved(monkeypatch, status):
    calls = _stub_compile_and_preserve(monkeypatch, status=status)
    await vsj.handle_ingest_verified_solution(FakePool(), _payload(gold_patch="diff\n+x\n", fail_to_pass=["t::1"]))
    assert calls == []


@pytest.mark.asyncio
async def test_missing_version_row_or_patch_preserves_nothing(monkeypatch):
    calls = _stub_compile_and_preserve(monkeypatch, row_id=None)
    await vsj.handle_ingest_verified_solution(FakePool(), _payload(gold_patch="diff\n+x\n", fail_to_pass=["t::1"]))
    calls2 = _stub_compile_and_preserve(monkeypatch)
    await vsj.handle_ingest_verified_solution(FakePool(), _payload())          # no gold_patch in payload
    assert calls == [] and calls2 == []


@pytest.mark.asyncio
async def test_enqueued_payload_carries_gold_patch_and_fail_to_pass(monkeypatch):
    from app.services.ingestion_sources import verified_solutions_hf as vs

    rows = [{"instance_id": "a__b-1", "repo": "a/b", "base_commit": "c" * 40,
             "problem_statement": "boom", "patch": "diff --git a/y b/y\n+ok\n", "test_patch": "t",
             "FAIL_TO_PASS": ["t::1"], "license": "mit"}]
    monkeypatch.setattr(vs.VerifiedSolutionSource, "_iter_raw_rows", lambda self: iter(rows))
    pool = FakePool()
    out = await vsj.enqueue_verified_solution_jobs(pool, source_key="swe_bench_extra", target=10,
                                                   design_paths=DESIGN_PATHS)
    assert out["enqueued"] == 1
    blob = str(pool.calls[0]["args"])
    assert "diff --git a/y b/y" in blob and "t::1" in blob and "boom" in blob
