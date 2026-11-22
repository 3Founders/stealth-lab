"""Which sandbox is active, and that the choice follows the documented defaults (docs/pricing_and_sandbox_per_way.md §4.1).

The counter-intuitive rule these pin: **the cheaper provider is not the fallback.** Agent37 is ~36× cheaper, but
its default instance is a container with open egress, while Modal's default is gVisor with the network blocked.
Script steps run code written by strangers, so isolation wins.
"""
from __future__ import annotations

import pytest

from app.submissions import sandbox_choice


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for n in ("AGENT37_API_KEY", "AGENT37_KEY", "SUBMISSION_SANDBOX", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET",
              "AGENT37_NETWORK"):
        monkeypatch.delenv(n, raising=False)
    yield


def test_nothing_configured_says_so_and_names_the_variables_to_set():
    d = sandbox_choice.describe()
    assert d["active"] is None
    assert d["configured"] is False
    assert "AGENT37_API_KEY" in d["unchosen_reason"]
    assert "MODAL_TOKEN_ID" in d["unchosen_reason"]


def test_modal_is_the_fallback_when_both_are_available(monkeypatch):
    monkeypatch.setenv("AGENT37_API_KEY", "k")
    monkeypatch.setenv("MODAL_TOKEN_ID", "a")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "b")
    assert sandbox_choice.describe()["active"] == "modal"


def test_agent37_is_used_when_it_is_the_only_configured_option(monkeypatch):
    monkeypatch.setenv("AGENT37_API_KEY", "k")
    assert sandbox_choice.describe()["active"] == "agent37"


def test_agent37_is_asked_for_deny_all_unless_told_otherwise(monkeypatch):
    monkeypatch.setenv("AGENT37_API_KEY", "k")
    assert sandbox_choice.describe()["deny_egress_on_agent37"] is True
    monkeypatch.setenv("AGENT37_NETWORK", "open")
    assert sandbox_choice.describe()["deny_egress_on_agent37"] is False


def test_every_option_states_its_isolation_and_cost():
    """A choice a person cannot compare is not a choice."""
    for o in sandbox_choice.describe()["options"]:
        assert o["isolation"], o["id"]
        assert o["network"], o["id"]
        assert o["cost_per_2min_run_usd"] >= 0, o["id"]
        assert o["cost_note"], o["id"]


def test_the_cheaper_option_is_named_but_not_defaulted(monkeypatch):
    monkeypatch.setenv("AGENT37_API_KEY", "k")
    monkeypatch.setenv("MODAL_TOKEN_ID", "a")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "b")
    d = sandbox_choice.describe()
    cheap = min(d["options"], key=lambda o: o["cost_per_2min_run_usd"])
    assert cheap["id"] == "agent37"
    assert d["active"] == "modal", "cost must not decide the default; isolation does"
