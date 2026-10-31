"""The tool gateway's rules (app/tool_routing/gateway.py, docs/tool_gateway_design.md): which servers and tools qualify,
what is refused before any network call, and the consent -> call flow (with the database and the remote server faked)."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from app.tool_routing import gateway as gw
from app.tool_routing.catalog import card_from_registry


def run(coro):
    return asyncio.run(coro)


def card(**over):
    base = {"name": "app.glif/glif", "description": "Generate images",
            "repository": "https://github.com/glifxyz/glif-mcp", "website": "https://glif.app",
            "remote_urls": [{"type": "streamable-http", "url": "https://glif.app/api/mcp"}]}
    probe = over.pop("probe", {"status": "live", "tools": [{"name": "run_glif"}]})
    return card_from_registry({**base, **over}, probe=probe, repo={"stars": over.pop("stars", 200)} if "stars" in over else {"stars": 200})


def test_only_live_remote_verified_used_servers_qualify(monkeypatch):
    monkeypatch.delenv("TOOL_GATEWAY_DENY", raising=False)
    assert gw.gateway_allowed(card())
    assert not gw.gateway_allowed(card(probe={"status": "auth"}))                      # needs a sign-in
    assert not gw.gateway_allowed(card(remote_urls=[]))                                # a package, not remote
    assert not gw.gateway_allowed(card(name="io.github.someone/glif"))                 # publisher not verified
    monkeypatch.setenv("TOOL_GATEWAY_DENY", "app.glif/glif")
    assert not gw.gateway_allowed(card())


def test_tools_that_act_for_the_user_are_refused():
    assert gw.tool_allowed({"name": "generate_image"})[0]
    assert gw.tool_allowed({"name": "search_docs"})[0]
    for name in ("send_email", "post_tweet", "delete_file", "create_payment", "publish", "merge_pr", "update_record"):
        assert not gw.tool_allowed({"name": name})[0], name
    assert not gw.tool_allowed({"name": "do_it", "annotations": {"destructiveHint": True}})[0]
    assert gw.tool_allowed({"name": "send_preview", "annotations": {"readOnlyHint": True}})[0]   # the server says read-only


def test_keys_in_arguments_and_private_addresses_are_refused():
    assert gw.scan_arguments({"prompt": "use key sk-" + "a" * 30}) == "openai_key"
    assert gw.scan_arguments({"prompt": "a fox logo"}) is None
    with pytest.raises(gw.GatewayRefused):
        run(gw.guard_url("http://glif.app/api/mcp"))                                    # not https
    with pytest.raises(gw.GatewayRefused):
        run(gw.guard_url("https://127.0.0.1/mcp"))                                      # not public
    with pytest.raises(gw.GatewayRefused):
        run(gw.guard_url("https://10.0.0.5/mcp"))


def test_images_come_back_as_images_and_links_as_text():
    png = base64.b64encode(b"\x89PNG fake").decode()
    text, images = gw.content_parts({"content": [{"type": "text", "text": "here"}, {"type": "image", "data": png, "mimeType": "image/png"},
                                                 {"type": "resource_link", "name": "full size", "uri": "https://x/y.png"}]})
    assert "here" in text and "https://x/y.png" in text
    assert images == [(b"\x89PNG fake", "png")]


class FakePool:
    """Just enough of asyncpg for the gateway's consent and grant queries."""

    def __init__(self):
        self.links, self.grants, self.calls = {}, set(), []

    def acquire(self):
        pool = self

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def transaction(self):
                return self

            async def execute(self, sql, *args):
                if "INSERT INTO tool_consent_links" in sql:
                    pool.links[args[0]] = {"owner": args[1], "server": args[2], "used": False}
                elif "INSERT INTO tool_grants" in sql:
                    pool.grants.add((args[0], args[1]))
                elif "INSERT INTO tool_gateway_calls" in sql:
                    pool.calls.append(args[3])
                elif "UPDATE tool_grants SET revoked_at" in sql:
                    hit = (args[0], args[1]) in pool.grants
                    pool.grants.discard((args[0], args[1]))
                    return f"UPDATE {int(hit)}"
                return "OK"

            async def fetchval(self, sql, *args):
                return 1 if (args[0], args[1]) in pool.grants else None

            async def fetchrow(self, sql, *args):
                link = pool.links.get(args[0])
                if not link or link["used"]:
                    return None
                if sql.lstrip().startswith("UPDATE"):
                    link["used"] = True
                return {"owner_id": link["owner"], "server": link["server"]}

        return Conn()


def test_consent_then_call_through_the_server_tool(monkeypatch):
    from app.mcp_server import server as srv
    from app.tool_routing import store as _store

    pool = FakePool()
    monkeypatch.setitem(srv._LIFESPAN_STATE, "pool", pool)
    monkeypatch.setattr(srv, "_gateway_owner", lambda: "user-1")
    the_card = card()

    async def by_name(p, name):
        return the_card if name == "app.glif/glif" else None
    monkeypatch.setattr(_store, "card_by_name", by_name)
    monkeypatch.setattr(_store, "record_outcome", lambda *a, **k: asyncio.sleep(0, 0))

    async def tools(url):
        return [{"name": "run_glif", "inputSchema": {}}, {"name": "send_email"}]

    async def call(url, name, args):
        png = base64.b64encode(b"\x89PNG fake").decode()
        return {"content": [{"type": "text", "text": "a fox"}, {"type": "image", "data": png, "mimeType": "image/png"}]}, 10, 20
    monkeypatch.setattr(gw, "list_tools", tools)
    monkeypatch.setattr(gw, "call_tool", call)

    first = json.loads(run(srv._gateway_call(None, "app.glif/glif", "run_glif", {"prompt": "a fox logo"}, "PUBLIC_SOURCE")))
    assert first["consent_required"] and "/tools/consent/" in first["link"]
    token = first["link"].rsplit("/", 1)[1]
    assert run(gw.use_link(pool, token, True)) == ("user-1", "app.glif/glif")
    assert run(gw.use_link(pool, token, True)) is None                                  # one use only

    out = run(srv._gateway_call(None, "app.glif/glif", "run_glif", {"prompt": "a fox logo"}, "PUBLIC_SOURCE"))
    assert isinstance(out, list) and json.loads(out[0])["result"] == "a fox" and len(out) == 2   # text, then the image
    assert run(srv._gateway_call(None, "app.glif/glif", "send_email", {}, "PUBLIC_SOURCE")).startswith("REFUSED")
    assert run(srv._gateway_call(None, "app.glif/glif", "run_glif", {}, "USER_PRIVATE")).startswith("REFUSED")
    assert run(srv._gateway_call(None, "app.glif/glif", "run_glif", {"k": "ghp_" + "a" * 36}, "PUBLIC_SOURCE")).startswith("REFUSED")
    assert run(srv._gateway_call(None, "nope/nope", "x", {}, "PUBLIC_SOURCE")).startswith("REFUSED")
    assert json.loads(run(srv._gateway_call(None, "app.glif/glif", "revoke", {}, "PUBLIC_SOURCE")))["revoked"] is True
    assert "ok" in pool.calls and "refused:tool" in pool.calls and "refused:consent" in pool.calls


def test_kells_own_image_server_needs_no_consent_and_keeps_the_rules(monkeypatch):
    from app.mcp_server import server as srv
    from app.tool_routing import store as _store

    pool = FakePool()
    monkeypatch.setitem(srv._LIFESPAN_STATE, "pool", pool)
    monkeypatch.setattr(srv, "_gateway_owner", lambda: "user-2")
    monkeypatch.setattr(gw, "builtin_available", lambda name: name == "kell/images")
    monkeypatch.setattr(_store, "record_outcome", lambda *a, **k: asyncio.sleep(0, 0))
    calls = []

    async def fake_generate(prompt, size="1024x1024"):
        calls.append((prompt, size))
        return b"\x89PNG fox", 1.5
    monkeypatch.setattr(gw, "generate_image", fake_generate)
    monkeypatch.setattr(gw, "IMAGES_PER_DAY", 2)

    out = run(srv._gateway_call(None, "kell/images", "generate_image", {"prompt": "a minimal fox logo"}, "PUBLIC_SOURCE"))
    assert isinstance(out, list) and json.loads(out[0])["images"] == 1 and calls == [("a minimal fox logo", "1024x1024")]
    assert not pool.links                                                         # no consent link for kell's own
    assert run(srv._gateway_call(None, "kell/images", "generate_image", {"prompt": "x"}, "USER_PRIVATE")).startswith("REFUSED")
    assert run(srv._gateway_call(None, "kell/images", "generate_image", {"prompt": "sk-" + "b" * 30}, "PUBLIC_SOURCE")).startswith("REFUSED")
    assert run(srv._gateway_call(None, "kell/images", "other", {"prompt": "x"}, "PUBLIC_SOURCE")).startswith("REFUSED")
    run(srv._gateway_call(None, "kell/images", "generate_image", {"prompt": "second"}, "PUBLIC_SOURCE"))
    assert "daily limit" in run(srv._gateway_call(None, "kell/images", "generate_image", {"prompt": "third"}, "PUBLIC_SOURCE"))
    listed = json.loads(run(srv._gateway_tools("kell/images")))
    assert listed["tools"][0]["name"] == "generate_image"
    entry = gw.builtin_entry("kell/images")
    assert entry["via_kell"] and entry["source"] == "kell" and "generate" in entry["goals"]
