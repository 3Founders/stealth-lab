"""call_model defaults and hints; find_ways keeps going with an open model when no stored way matches."""
import asyncio
import json

from app.mcp_server import server as srv


def test_the_default_model_is_glm_flash_and_can_be_set(monkeypatch):
    monkeypatch.delenv("KELL_DEFAULT_MODEL", raising=False)
    assert srv._default_call_model() == "glm-5.3-flash"
    monkeypatch.setenv("KELL_DEFAULT_MODEL", "gemma-4-31b-it")
    assert srv._default_call_model() == "gemma-4-31b-it"


def test_no_match_carries_the_next_step_and_a_real_match_does_not():
    out = json.loads(srv._no_way_next(json.dumps({"outcome": "no_match"})))
    assert "glm-5.3-flash" in out["no_stored_way_next"] and "PUBLIC_SOURCE" in out["no_stored_way_next"]
    hit = json.loads(srv._no_way_next(json.dumps({"outcome": "resolved", "procedures": [{"goal_id": "g"}]})))
    assert "no_stored_way_next" not in hit
    assert srv._no_way_next("REFUSED: x") == "REFUSED: x"


def test_a_private_default_refusal_says_how_to_retry():
    hint = asyncio.run(srv._call_model_hint(None, RuntimeError("blocked by policy"), "USER_PRIVATE"))
    assert "PUBLIC_SOURCE" in hint
    assert asyncio.run(srv._call_model_hint(None, RuntimeError("blocked"), "PUBLIC_SOURCE")) == ""
