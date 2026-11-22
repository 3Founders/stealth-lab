"""Agent37 as a second sandbox backend, and the two defaults that must not drift.

The reason these tests exist is not "the code runs". It is that Agent37's *defaults are wrong for our threat
model* and the fix is a configuration choice, so a later edit can silently undo it:

  - Agent37's default instance is a **container**; gVisor is an option (their own vs-Modal page says so).
  - Agent37 allows **public egress by default**; our Modal path sets `block_network=True`.

Both are safe today only because we ask for them explicitly. Anything that drops `deny-all`, or makes Agent37
the fallback provider, is a security regression that would otherwise look like a tidy-up.
"""
from __future__ import annotations

import pytest

from app.submissions import sandbox


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("AGENT37_API_KEY", "AGENT37_KEY", "AGENT37_NETWORK", "SUBMISSION_SANDBOX",
                 "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        monkeypatch.delenv(name, raising=False)
    yield


def test_modal_wins_when_both_are_configured(monkeypatch):
    """Modal's default IS gVisor with blocked network; Agent37's is not. So Modal is the fallback."""
    monkeypatch.setenv("AGENT37_API_KEY", "sk_live_test")
    monkeypatch.setenv("MODAL_TOKEN_ID", "x")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "y")
    assert sandbox.provider() == "modal"


def test_agent37_is_used_only_when_it_is_the_only_option(monkeypatch):
    monkeypatch.setenv("AGENT37_API_KEY", "sk_live_test")
    assert sandbox.provider() == "agent37"


def test_explicit_choice_wins(monkeypatch):
    monkeypatch.setenv("AGENT37_API_KEY", "sk_live_test")
    monkeypatch.setenv("MODAL_TOKEN_ID", "x")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "y")
    monkeypatch.setenv("SUBMISSION_SANDBOX", "agent37")
    assert sandbox.provider() == "agent37"


def test_nothing_configured_reports_none_rather_than_guessing():
    assert sandbox.provider() == ""


def test_asking_for_agent37_without_a_key_is_not_a_silent_fallback_to_modal(monkeypatch):
    """If an operator asked for agent37 and gave no key, run nothing — never quietly use a different backend."""
    monkeypatch.setenv("MODAL_TOKEN_ID", "x")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "y")
    monkeypatch.setenv("SUBMISSION_SANDBOX", "agent37")
    assert sandbox.provider() == ""


def test_a_script_never_runs_when_no_sandbox_is_configured():
    """The failure must be loud and named — never a local subprocess standing in for a sandbox."""
    with pytest.raises(sandbox.SandboxUnavailable) as exc:
        import asyncio
        asyncio.run(sandbox.run_script("print(1)", "x.py"))
    assert "AGENT37_API_KEY" in str(exc.value) or "MODAL" in str(exc.value)


def test_unknown_language_is_refused_before_any_provider_call():
    with pytest.raises(sandbox.SandboxUnavailable):
        import asyncio
        asyncio.run(sandbox.run_script("puts 1", "x.rb"))


def test_agent37_request_denies_egress_by_default(monkeypatch):
    """The create body must ask for deny-all. This is the whole reason Agent37 is opt-in."""
    monkeypatch.setenv("AGENT37_API_KEY", "sk_live_test")
    seen: dict = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            seen.setdefault("posts", []).append((url, kw.get("json")))

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"id": "inst1", "exit_code": 0, "stdout": "", "stderr": ""}

            return R()

        async def delete(self, *a, **kw):
            return None

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeClient())
    import asyncio
    asyncio.run(sandbox._run_on_agent37(("python3", "-c"), "print(1)", ""))

    create = seen["posts"][0][1]
    assert create.get("network") == {"mode": "deny-all"}, "Agent37 must be asked for deny-all egress"
