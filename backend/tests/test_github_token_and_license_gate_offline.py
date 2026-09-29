"""Offline: a GitHub token that lives only in backend/.env must reach the ingestion sources, and a license gate that
was rate-limited must never be mistaken for a low admission rate.

The incident: a production run read 3,369 rows and admitted 0. The token was in .env (PERSONAL_GITHUB_TOKEN) but the
sources read only os.environ, so every license lookup ran unauthenticated, hit GitHub's 60/hour wall, and the rest of
the corpus was quarantined as "license unknown" -- with no trace of it in the summary."""
from __future__ import annotations

import asyncio

import pytest

from app.services import ingest_budget
from app.services.ingestion_sources import bot_dependency_prs, skillmd_dataset, skillmd_pilot
from app.services.ingestion_sources.skillmd_offline import build_offline_reader


@pytest.fixture()
def dotenv_only_token(monkeypatch):
    """The token is in settings (i.e. .env) but in NO environment variable."""
    for name in ("GITHUB_TOKEN", "PERSONAL_GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    import app.config as config

    monkeypatch.setattr(config.settings, "personal_github_token", "dotenv-token", raising=False)
    monkeypatch.setattr(config.settings, "github_token", None, raising=False)


def test_skillmd_license_lookups_use_a_token_that_lives_only_in_dotenv(dotenv_only_token):
    assert skillmd_dataset._github_headers()["Authorization"] == "Bearer dotenv-token"


def test_bot_pr_source_uses_a_token_that_lives_only_in_dotenv(dotenv_only_token):
    assert bot_dependency_prs._token_from_env() == "dotenv-token"


def test_an_exported_token_still_wins_over_dotenv(dotenv_only_token, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "exported-token")
    assert skillmd_dataset._github_headers()["Authorization"] == "Bearer exported-token"


def test_no_token_anywhere_is_unauthenticated_not_an_error(monkeypatch):
    for name in ("GITHUB_TOKEN", "PERSONAL_GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    import app.config as config

    monkeypatch.setattr(config.settings, "personal_github_token", None, raising=False)
    monkeypatch.setattr(config.settings, "github_token", None, raising=False)
    assert skillmd_dataset._github_headers() == {}
    assert bot_dependency_prs._token_from_env() is None


class _RateLimitedResolver:
    """Every lookup answers 'unknown', and says it was because GitHub refused it."""

    lookups, rate_limited, api_errors = 60, 1200, 0

    def __call__(self, owner_repo):
        return "QUARANTINE"

    def spdx_for(self, owner_repo):
        return None

    def reason_for(self, owner_repo):
        return "license_unknown"

    def stats(self):
        return {"lookups": self.lookups, "rate_limited": self.rate_limited, "api_errors": self.api_errors}


@pytest.fixture()
def compile_spy(monkeypatch):
    calls = []

    async def run_skill_ingestion(pool, source, **kw):
        calls.append(1)
        return {"metrics": {"accepted": 0}, "outcomes": []}

    import app.services.skill_ingestion as si

    monkeypatch.setattr(si, "run_skill_ingestion", run_skill_ingestion)
    monkeypatch.setattr(skillmd_pilot, "build_extraction_client", lambda: (None, "fake-model"))
    ingest_budget.uninstall()
    yield calls
    ingest_budget.uninstall()


def _pilot(resolver, **kw):
    return asyncio.run(skillmd_pilot.run_skillmd_pilot(
        None, reader=build_offline_reader(), license_resolver=resolver, enforce_license=True,
        fetch_workers=1, limit=5, **kw))


def test_a_rate_limited_gate_is_reported_and_a_real_run_refuses_to_compile_on_it(compile_spy):
    summary = _pilot(_RateLimitedResolver())
    assert summary["gate"]["license_api"]["rate_limited"] == 1200      # visible, not hidden
    assert "rate_limited=1200" in summary["warning"]
    assert summary["ingestion"] is None and summary["note"].startswith("refused to compile")
    assert compile_spy == [], "nothing may be written on an unfinished license gate"


def test_a_dry_run_reports_the_same_warning_and_still_writes_nothing(compile_spy):
    summary = _pilot(_RateLimitedResolver(), dry_run=True)
    assert "warning" in summary and compile_spy == []


def test_the_pilot_reports_the_stats_of_the_resolver_it_builds_itself(monkeypatch, compile_spy):
    """The CLI passes no resolver; the pilot builds one. Its stats used to be dropped because the check looked at the
    (None) argument instead of the resolver actually in use."""
    monkeypatch.setattr(skillmd_pilot, "GitHubLicenseResolver", _RateLimitedResolver)
    summary = asyncio.run(skillmd_pilot.run_skillmd_pilot(
        None, reader=build_offline_reader(), enforce_license=True, fetch_workers=1, limit=5, dry_run=True))
    assert summary["gate"]["license_api"]["rate_limited"] == 1200


def test_ordinary_api_errors_do_not_block_a_real_run(compile_spy):
    """A deleted or moved repo answers 404 -- an ordinary per-row outcome, not an unfinished gate."""
    class _Errors(_RateLimitedResolver):
        rate_limited, api_errors = 0, 22

    summary = _pilot(_Errors())
    assert "warning" not in summary
    assert not str(summary.get("note", "")).startswith("refused"), "only rate limiting makes the gate untrustworthy"
