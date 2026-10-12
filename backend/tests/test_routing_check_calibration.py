"""The 'tests' check's false-accept rate (a wrong answer passes): measured (~0.08), not the bundled prior's ~0.19, and
then each kind's own, from audits of its accepted answers (report_audit). Offline, on the bundled prior."""
import asyncio
import dataclasses
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "local_eval"))

from app.routing import prior_bundle, service, store
from app.routing.config import DEFAULTS
from app.services.access import AccessScope

UNITS = ["gemma-4-31b-it|stealth", "gpt-oss-120b|stealth", "minimax-m2-7|stealth", "claude-opus-5-5|claude-code"]
OWN = "claude-opus-5-5|claude-code"
# the record of the MultiPL-E JS kind when the router skipped 9 of 20 tasks (2026-10-12): cheap answers mostly passed
JS_RECORD = [{"unit": "gemma-4-31b-it|stealth", "n": 9, "ok": 3}, {"unit": "gpt-oss-120b|stealth", "n": 10, "ok": 6},
             {"unit": "minimax-m2-7|stealth", "n": 7, "ok": 5}, {"unit": OWN, "n": 3, "ok": 3}]


def _g():
    return prior_bundle.load_globals()


def test_the_prior_uses_the_measured_rate_and_only_for_tests():
    g = _g()
    a, _ = service.calibrated_check_rates(g, "tests")
    assert abs(float(a.mean()) - DEFAULTS.tests_false_accept) < 0.01
    raw, _ = service.predict.check_rates(g, "tests")
    assert float(raw.mean()) > 0.15, "the bundled prior's own rate (what the setting corrects)"
    off = dataclasses.replace(DEFAULTS, tests_false_accept=0.0)
    assert np.allclose(service.calibrated_check_rates(g, "tests", off)[0], raw), "0 = the prior's own rate"
    for kind in ("judge", "procedure_check", "self_report"):
        assert np.allclose(service.calibrated_check_rates(g, kind)[0], service.predict.check_rates(g, kind)[0])


def test_a_kinds_audits_move_its_rate():
    g = _g()
    base = float(service.calibrated_check_rates(g, "tests")[0].mean())
    bad = {"accepted": 20, "rejected": 10, "audited": 10, "audited_wrong": 5}   # half the accepted were wrong
    good = {"accepted": 20, "rejected": 30, "audited": 10, "audited_wrong": 0}
    assert float(service.calibrated_check_rates(g, "tests", audits=bad)[0].mean()) > 2 * base
    assert float(service.calibrated_check_rates(g, "tests", audits=good)[0].mean()) < base
    none = {"accepted": 20, "rejected": 30, "audited": 0, "audited_wrong": 0}
    assert abs(float(service.calibrated_check_rates(g, "tests", audits=none)[0].mean()) - base) < 1e-9


def _opus_first_share(monkeypatch, record, audits=None, n=30, cfg=DEFAULTS):
    from route_run import _no_database

    async def counts(*a, **k):
        return record

    async def audit_counts(*a, **k):
        if audits is None:
            raise RuntimeError("no audits")
        return audits

    async def run():
        firsts = []
        with _no_database():
            monkeypatch.setattr(store, "goal_outcome_counts", counts)
            monkeypatch.setattr(store, "goal_audit_counts", audit_counts)
            for i in range(n):
                r = await service._recommend(None, goal_id=service.virtual_goal_id("code", "c21"), candidates=UNITS,
                    access_scope=AccessScope(), instance_key=f"k{i}", check_kind="tests", previous_attempts=[],
                    local_obs=[], record=False, cfg=cfg, virtual={"kind": "code", "code": "c21.2"},
                    constraints={"reliability_baseline": OWN})
                firsts.append(r["recommended"]["ladder"][0])
        return sum(f == OWN for f in firsts) / len(firsts)

    return asyncio.run(run())


def test_a_kind_whose_cheap_answers_pass_is_no_longer_skipped(monkeypatch):
    off = dataclasses.replace(DEFAULTS, tests_false_accept=0.0)
    before = _opus_first_share(monkeypatch, JS_RECORD, cfg=off)
    after = _opus_first_share(monkeypatch, JS_RECORD)
    assert after <= 0.2 and after < before


def test_audits_that_find_wrong_answers_send_a_kind_back_to_the_session_model(monkeypatch):
    bad = {"accepted": 14, "rejected": 12, "audited": 8, "audited_wrong": 5}
    assert _opus_first_share(monkeypatch, JS_RECORD, audits=bad) >= 0.6
