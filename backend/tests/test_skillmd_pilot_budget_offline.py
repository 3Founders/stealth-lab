"""Offline: the SkillMD pilot runs its model calls under the ingestion spend cap (docs/ingestion_review.md: "no
spend cap wired into the pilot path"). No network, no database: the offline adapter, a fake compile step."""
from __future__ import annotations

import asyncio

import pytest

from app.services import ingest_budget
from app.services.ingestion_sources import skillmd_pilot
from app.services.ingestion_sources.skillmd_offline import build_offline_reader


@pytest.fixture()
def fake_compile(monkeypatch):
    seen: dict = {}

    async def run_skill_ingestion(pool, source, **kw):
        active = ingest_budget.active()
        seen["active"] = active
        seen["cap"] = getattr(active, "_cap", None)
        if seen.get("raise"):
            raise RuntimeError("compile blew up")
        return {"metrics": {"accepted": 0}, "outcomes": []}

    import app.services.skill_ingestion as si
    monkeypatch.setattr(si, "run_skill_ingestion", run_skill_ingestion)
    monkeypatch.setattr(skillmd_pilot, "build_extraction_client", lambda: (None, "fake-model"))
    ingest_budget.uninstall()
    yield seen
    ingest_budget.uninstall()


def _pilot(**kw):
    return asyncio.run(skillmd_pilot.run_skillmd_pilot(
        None, reader=build_offline_reader(), enforce_license=False, fetch_workers=1, limit=5, **kw))


def test_compile_runs_under_the_cap_and_the_budget_is_removed_after(fake_compile):
    summary = _pilot(max_usd=1.5)
    assert fake_compile["active"] is not None, "the compile step ran with a budget installed"
    assert fake_compile["cap"] == 1.5
    assert ingest_budget.active() is None, "uninstalled afterwards"
    assert summary["budget"]["daily_cap_usd"] == 1.5
    # no database here, so the ledger is unavailable -- which the budget treats as "pause", never as "no spend"
    assert summary["budget"]["state"] == "unavailable"


def test_default_cap_is_the_configured_daily_budget(fake_compile):
    from app.config import settings

    _pilot()
    assert fake_compile["cap"] == float(settings.daily_llm_budget_usd)


def test_budget_is_removed_even_when_the_compile_fails(fake_compile):
    fake_compile["raise"] = True
    with pytest.raises(RuntimeError):
        _pilot(max_usd=2.0)
    assert ingest_budget.active() is None


def test_an_already_installed_budget_is_kept(fake_compile):
    outer = ingest_budget.install(None, cap_usd=9.0)
    summary = _pilot(max_usd=1.0)
    assert fake_compile["active"] is outer, "inside a worker, the worker's budget stays in charge"
    assert ingest_budget.active() is outer
    assert "budget" not in summary


def test_dry_run_installs_nothing(fake_compile):
    summary = _pilot(dry_run=True, max_usd=1.0)
    assert "active" not in fake_compile
    assert summary["ingestion"] is None
