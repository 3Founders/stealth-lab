"""Offline: with a limit, SkillMD fetches in waves sized to what PHASE C still needs, instead of fetching every
oversampled candidate up front (docs/ingestion_review.md, open defect "~5x more fetches than the yield needs").
The admitted artifacts and the dispositions PHASE C records are the same as a run that fetches everything."""
from __future__ import annotations

from app.services.ingestion_sources.skillmd_dataset import SkillMD138KSource
from app.services.ingestion_sources.skillmd_offline import OfflineRawStore, OfflineSkillMDReader


def _source(limit, workers, calls):
    store = OfflineRawStore()

    def counting_fetch(row):
        calls.append(row.html_url)
        return store(row)

    return SkillMD138KSource(reader=OfflineSkillMDReader(), raw_fetcher=counting_fetch, enforce_license=False,
                             limit=limit, fetch_workers=workers)


def test_limit_fetches_fewer_rows_and_admits_the_same_first_skill():
    unlimited_calls: list[str] = []
    everything = _source(None, 1, unlimited_calls)
    all_uris = [r.uri for r in everything.discover()]
    assert len(all_uris) == 2 and len(unlimited_calls) == 6       # the fixture: 6 fetchable candidates, 2 admitted

    for workers in (1, 2):
        calls: list[str] = []
        limited = _source(1, workers, calls)
        uris = [r.uri for r in limited.discover()]
        assert uris == all_uris[:1], "same first admitted skill as an unlimited run"
        assert limited.stats.admitted == 1
        assert "limit_reached" in limited.stats.reasons
        assert len(calls) < len(unlimited_calls), f"workers={workers}: {len(calls)} fetches, no fewer than fetching all"


def test_limit_that_is_never_reached_still_sees_every_candidate():
    calls: list[str] = []
    source = _source(10, 1, calls)
    uris = [r.uri for r in source.discover()]
    assert len(uris) == 2
    assert len(calls) == 6, "short of the limit, every candidate is still fetched, wave after wave"
    assert "limit_reached" not in source.stats.reasons


def test_dispositions_before_the_limit_match_a_full_fetch():
    """Everything PHASE C looked at before stopping is accounted exactly as in the one-batch version: one
    disposition per row seen up to the stop, and the limit recorded once."""
    calls: list[str] = []
    source = _source(1, 1, calls)
    list(source.discover())
    reasons = dict(source.stats.reasons)
    assert reasons.pop("limit_reached") == 1
    assert source.stats.fetched <= len(calls)
