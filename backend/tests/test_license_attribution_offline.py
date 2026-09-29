"""Offline: CC-BY-4.0 content carries its credit wherever it is served, and only paths that record the credit may
admit it (BLOCKERS I7)."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services import license_attribution as la
from app.services.repo_license_policy import classify_spdx

P1, P2, P3 = (str(uuid.uuid4()) for _ in range(3))
NOTICE = '"skill" by acme (https://x) licensed under CC-BY-4.0, modified: converted into structured procedures'


class _Pool:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, []

    async def fetch(self, sql, ids):
        self.calls.append((sql, ids))
        if self.fail:
            raise RuntimeError('column "attribution" does not exist')
        return [r for r in self.rows if r[0] in ids]


@pytest.fixture
def shards(monkeypatch):
    from app.services import shards as sh

    pools: list = []

    async def all_pools(pool, strict=False):
        return pools

    monkeypatch.setattr(sh, "all_pools", all_pools)
    return pools


def _run(coro):
    return asyncio.run(coro)


def test_every_procedure_in_the_reply_gets_its_notice(shards):
    shards.extend([("K000", _Pool([(P1, NOTICE)])), ("K002", _Pool([(P3, NOTICE)]))])
    body = {"outcome": "ambiguous",
            "procedures": [{"procedure_id": P1}],
            "candidates": [{"ways": [{"procedure_id": P2}]}],
            "suggested": {"procedure_id": P1},
            "related_examples": [{"procedure_id": P3, "verified_solution": {"code": "x"}}]}
    _run(la.attach_attribution(object(), body))
    assert body["procedures"][0]["attribution"] == NOTICE
    assert body["suggested"]["attribution"] == NOTICE
    assert body["related_examples"][0]["attribution"] == NOTICE, "found on another shard"
    assert "attribution" not in body["candidates"][0]["ways"][0], "no notice recorded -> nothing added"


def test_no_procedures_means_no_query(shards):
    pool = _Pool()
    shards.append(("K000", pool))
    body = {"outcome": "no_match"}
    _run(la.attach_attribution(object(), body))
    assert pool.calls == [] and body == {"outcome": "no_match"}


def test_a_shard_without_migration_126_never_breaks_the_reply(shards):
    shards.extend([("K000", _Pool(fail=True)), ("K002", _Pool([(P1, NOTICE)]))])
    body = {"procedures": [{"procedure_id": P1}, {"procedure_id": "not-a-uuid"}]}
    _run(la.attach_attribution(object(), body))
    assert body["procedures"][0]["attribution"] == NOTICE
    assert "attribution" not in body["procedures"][1]


def test_gate_admits_cc_by_only_where_the_credit_is_recorded():
    assert classify_spdx("CC-BY-4.0").decision == "QUARANTINE"
    assert "does not record" in classify_spdx("CC-BY-4.0").reason
    assert classify_spdx("CC-BY-4.0", records_attribution=True).decision == "ALLOW"
    assert classify_spdx("MIT").decision == "ALLOW", "non-attribution licenses are unaffected"
    assert classify_spdx("CC-BY-SA-4.0", records_attribution=True).decision == "QUARANTINE"
    assert classify_spdx("GPL-3.0-only", records_attribution=True).decision == "REJECT"


def test_step2_admits_cc_by_because_it_records_the_credit():
    from app.services.ingestion_sources.verified_solutions_jobs import _classify_recording_attribution

    assert _classify_recording_attribution("CC-BY-4.0").decision == "ALLOW"


def test_skillmd_resolver_admits_cc_by_and_the_artifact_carries_the_id(monkeypatch):
    from app.services.ingestion_sources import skillmd_dataset as sd

    resolver = sd.GitHubLicenseResolver() if hasattr(sd, "GitHubLicenseResolver") else None
    if resolver is None:
        cls = next(v for v in vars(sd).values()
                   if isinstance(v, type) and hasattr(v, "spdx_for") and hasattr(v, "_decide"))
        resolver = cls()
    monkeypatch.setattr(resolver, "_fetch_spdx", lambda repo: "CC-BY-4.0")
    assert resolver("acme/skills") == "ALLOW"
    assert resolver.spdx_for("acme/skills") == "CC-BY-4.0"
