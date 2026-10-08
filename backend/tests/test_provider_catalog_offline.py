"""Phase 1 of serving routed open models: connection compliance metadata, per-connection request extras, and the
model catalog. Offline: HTTP goes to an httpx MockTransport and nothing touches a database or the network."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.providers import adapters, model_catalog, registry
from app.providers.types import CallRequest, Connection, UnitSpec

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "docs" / "providers"


def _record(**kw):
    base = {"connection_id": "c1", "kind": "openai_compatible", "base_url": "https://api.example.com/v1",
            "units": [{"model": "deepseek-v3.2"}]}
    base.update(kw)
    return base


# ------------------------------------------------------------------ compliance metadata

def test_unknown_stays_unknown_and_never_reads_as_true():
    conn = registry.connection_from_dict(_record())
    assert conn.zdr is None and conn.no_training is None and conn.dpa_signed is False
    assert conn.compliance == {"zdr": None, "no_training": None, "region": "unknown", "dpa_signed": False}


def test_declared_metadata_is_carried_on_the_read_model():
    conn = registry.connection_from_dict(_record(zdr=False, no_training=True, region="in", dpa_signed=True))
    assert conn.compliance == {"zdr": False, "no_training": True, "region": "in", "dpa_signed": True}


@pytest.mark.parametrize("bad", [{"zdr": "yes"}, {"no_training": 1}, {"region": "US"}, {"region": "United States"},
                                 {"region": 5}, {"dpa_signed": "true"}])
def test_bad_metadata_is_refused_with_the_connection_named(bad):
    with pytest.raises(ValueError, match="c1"):
        registry.connection_from_dict(_record(**bad))


# ------------------------------------------------------------------ request extras

OPENROUTER = {"base_url": "https://openrouter.ai/api/v1"}


def test_zdr_on_openrouter_must_be_backed_by_the_routing_option():
    with pytest.raises(ValueError, match="request_extras.provider.zdr"):
        registry.connection_from_dict(_record(zdr=True, **OPENROUTER))
    with pytest.raises(ValueError, match="request_extras.provider.zdr"):
        registry.connection_from_dict(_record(zdr=True, request_extras={"provider": "zdr"}, **OPENROUTER))
    ok = registry.connection_from_dict(_record(zdr=True, request_extras={"provider": {"zdr": True}}, **OPENROUTER))
    assert ok.zdr is True and ok.request_extras == {"provider": {"zdr": True}}


def test_a_lookalike_host_is_not_openrouter():
    registry.connection_from_dict(_record(zdr=True, base_url="https://openrouter.ai.evil.example/v1"))


@pytest.mark.parametrize("key", sorted(registry.PROTECTED_BODY_KEYS))
def test_request_extras_cannot_set_fields_the_adapter_owns(key):
    with pytest.raises(ValueError, match=key):
        registry.connection_from_dict(_record(request_extras={key: "x"}))


def test_request_extras_must_be_plain_json_on_an_openai_compatible_connection():
    with pytest.raises(ValueError, match="object"):
        registry.connection_from_dict(_record(request_extras=["a"]))
    with pytest.raises(ValueError, match="only applies"):
        registry.connection_from_dict(_record(kind="a2a", request_extras={"x": 1}))


def _post_body(conn: Connection) -> dict:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 1}})

    transport = httpx.MockTransport(handler)
    original = adapters.http_client
    adapters.http_client = lambda **kw: httpx.AsyncClient(transport=transport, **kw)
    try:
        asyncio.run(adapters.OpenAICompatAdapter().call(conn, conn.units[0], CallRequest(prompt="hi", max_tokens=50), "k"))
    finally:
        adapters.http_client = original
    return seen


def test_extras_are_added_to_the_request_body_and_never_override_ours():
    conn = registry.connection_from_dict(_record(request_extras={"provider": {"zdr": True}, "top_k": 20}))
    body = _post_body(conn)
    assert body["provider"] == {"zdr": True} and body["top_k"] == 20
    assert body["model"] == "deepseek-v3.2" and body["max_tokens"] == 50 and body["messages"][-1]["content"] == "hi"
    # a Connection built directly (bypassing the loader) still cannot displace the fields the adapter set
    forced = Connection(connection_id="c2", kind="openai_compatible", base_url="https://api.example.com/v1",
                        units=(UnitSpec(model="m"),), request_extras={"model": "evil", "max_tokens": 10**6})
    body = _post_body(forced)
    assert body["model"] == "m" and body["max_tokens"] == 50


# ------------------------------------------------------------------ catalog

def test_seed_entries_are_metadata_only_and_flag_what_is_unverified():
    entries = model_catalog.load_catalog()
    assert {e.model for e in entries} >= {"glm-5.3", "deepseek-v3.2", "qwen3-coder", "gpt-oss-120b"}
    assert all(e.licence and not e.licence_verified and not e.tool_calling_verified and not e.offerings for e in entries)
    assert next(e for e in entries if e.model == "glm-5.3").licence == "unverified"
    assert model_catalog.default_models_value(entries) == ""


def test_an_entry_without_a_licence_is_refused():
    with pytest.raises(ValueError, match="licence is required"):
        model_catalog.entry_from_dict({"model": "m", "origin": "US"})
    with pytest.raises(ValueError, match="origin"):
        model_catalog.entry_from_dict({"model": "m", "licence": "MIT", "origin": "MARS"})


@pytest.mark.parametrize("offering,message", [
    ({"connection_id": "c1"}, "provider_model"),
    ({"connection_id": "c1", "provider_model": "x", "input_per_mtok": 1.0}, "both"),
    ({"connection_id": "c1", "provider_model": "x", "input_per_mtok": -1, "output_per_mtok": 1}, "non-negative"),
    ({"connection_id": "c1", "provider_model": "x", "input_per_mtok": True, "output_per_mtok": 1}, "non-negative")])
def test_bad_offerings_are_refused(offering, message):
    with pytest.raises(ValueError, match=message):
        model_catalog.entry_from_dict({"model": "m", "licence": "MIT", "offerings": [offering]})


def _catalog_with_offerings():
    return [model_catalog.entry_from_dict({
        "model": "deepseek-v3.2", "licence": "MIT (reported)", "origin": "CN", "tier": "standard",
        "offerings": [
            {"connection_id": "deepinfra", "provider_model": "ds/v3.2", "input_per_mtok": 0.3, "output_per_mtok": 0.9,
             "max_output_tokens": 8192},
            {"connection_id": "novita", "provider_model": "deepseek/v3.2"}]})]


def test_units_come_only_from_priced_offerings_of_the_named_connection():
    entries = _catalog_with_offerings()
    units, skipped = model_catalog.units_for("deepinfra", entries)
    assert units == [{"model": "deepseek-v3.2", "scaffold": "direct", "provider_model": "ds/v3.2",
                      "input_per_mtok": 0.3, "output_per_mtok": 0.9, "tier": "standard", "max_output_tokens": 8192}]
    assert skipped == []
    assert model_catalog.units_for("novita", entries) == ([], ["deepseek-v3.2"])        # unpriced: no unit
    assert model_catalog.units_for("openrouter", entries) == ([], [])
    assert model_catalog.default_models_value(entries) == "deepseek-v3.2|direct"
    # what units_for produces is accepted by the connection loader
    registry.connection_from_dict(_record(connection_id="deepinfra", units=units))


def test_a_catalog_file_replaces_the_seed_entry_of_the_same_name(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"models": [{"model": "glm-5.3", "licence": "see model card", "origin": "CN",
                                            "licence_verified": True}]}), encoding="utf-8")
    entries = {e.model: e for e in model_catalog.load_catalog(str(path))}
    assert entries["glm-5.3"].licence == "see model card" and entries["glm-5.3"].licence_verified
    assert "deepseek-v3.2" in entries


# ------------------------------------------------------------------ the shipped examples

def test_the_example_connections_load_and_hold_no_secret():
    path = EXAMPLES / "connections.example.json"
    conns = registry.load_connections_file(str(path))
    assert {c.connection_id for c in conns} == {"openrouter", "generalcompute", "deepinfra", "novita"}
    assert all(c.credential_ref and c.credential_ref.startswith("env:") for c in conns)
    openrouter = next(c for c in conns if c.connection_id == "openrouter")
    assert openrouter.zdr is True and openrouter.request_extras["provider"]["data_collection"] == "deny"
    assert "sk-" not in path.read_text(encoding="utf-8")


def test_the_example_catalog_loads():
    entries = model_catalog.load_catalog(str(EXAMPLES / "model_catalog.example.json"))
    ds = next(e for e in entries if e.model == "deepseek-v3.2")
    assert ds.offerings and not ds.offerings[0].priced
