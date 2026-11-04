"""The tool gateway: kell calls a recommended third-party MCP server for a user who has not installed it
(docs/tool_gateway_design.md, phase 1; db/163_tool_gateway.sql).

A server qualifies (gateway_allowed) when it is a remote server the probe found live without a sign-in, from a verified
publisher that is not a bulk publisher, with a repository of some use (stars) -- and not on the denylist. A tool on it
qualifies (tool_allowed) unless it is marked destructive or its name says it sends, posts, pays, deletes and the like:
image generation, lookups and searches pass; anything that acts on the world for the user does not, in phase 1.

Before the first call the user allows the server once, through a one-time link (consent_link -> the /tools/consent
page). Every call is checked for keys in its arguments, rate-limited per user and per server, made only to the URL the
registry lists (https, a public address, no redirects, a time and size limit), returned as third-party data, and logged
without its arguments or result.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import time
from collections import deque
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from app.tool_routing import oauth_client as oauth
from app.tool_routing.catalog import BULK_PUBLISHER, ToolCard

MIN_STARS = int(os.environ.get("TOOL_GATEWAY_MIN_STARS", "20"))
TIMEOUT_S = 20.0
MAX_BYTES = 1_000_000
LINK_TTL_S = 15 * 60
TOOLS_TTL_S = 3600.0
USER_PER_HOUR = int(os.environ.get("TOOL_GATEWAY_USER_PER_HOUR", "60"))
SERVER_PER_MIN = int(os.environ.get("TOOL_GATEWAY_SERVER_PER_MIN", "120"))
UA = "kell-tool-gateway/0.1 (+https://www.kell.org.in)"
HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream", "user-agent": UA}

# names of tools that act on the world for the user: never through the gateway in phase 1
WRITE_TOOL = re.compile(
    r"(^|[_\-.])(send|post|publish|tweet|reply|email|mail|message|notify|delete|remove|drop|destroy|purge|pay|charge|"
    r"purchase|buy|checkout|transfer|refund|withdraw|deposit|order|invite|share|update|edit|write|upload|create_user|"
    r"create_order|create_payment|create_invoice|subscribe|unsubscribe|book|schedule|deploy|merge|commit|push)"
    r"($|[_\-.]|s$|ed$)", re.I)


# Servers the probe called "live" that then answered 401/403 to a real call: they need a sign-in kell cannot give. Remembered for this process so the
# next search stops offering them as runnable (and ranks them last) instead of the agent trying the same dead end each time.
NEEDS_SIGNIN: set[str] = set()


# The same fact learned from use and kept in the audit log, so it survives a restart and covers every person: servers whose calls were refused
# for a sign-in in the last thirty days and that have not worked since (refreshed at most every ten minutes by learn_signins).
SIGNIN_SERVERS: set[str] = set()
_LEARNED_AT = 0.0
LEARN_EVERY_S = 600.0


# A server that failed a real call (not a sign-in, which has its own memory above) is left out of search for a while, so the
# agent does not keep offering the same dead end in every turn. It is time-based and clears itself; a successful call clears
# it sooner.
FAILING: dict[str, float] = {}
FAILURE_TTL_S = float(os.environ.get("TOOL_GATEWAY_FAILURE_TTL", "900"))


def note_failure(server: str) -> None:
    if server:
        FAILING[server] = time.monotonic() + FAILURE_TTL_S


def note_ok(server: str) -> None:
    FAILING.pop(server, None)
    from app.tool_routing import liveness

    liveness.DOWN.discard(server)


def failing(server: str) -> bool:
    """True while a server that failed recently is in its cooldown, or whose last real calls all failed (liveness.DOWN)."""
    from app.tool_routing import liveness

    if server in liveness.DOWN:
        return True
    until = FAILING.get(server)
    if until is None:
        return False
    if until <= time.monotonic():
        FAILING.pop(server, None)
        return False
    return True


async def learn_signins(pool: Any, now: Optional[float] = None) -> None:
    """Reads the audit log: a server whose newest outcome is a sign-in refusal is marked, so it is never offered as runnable and never tried
    first. A later successful call (the server changed) clears it."""
    global _LEARNED_AT, SIGNIN_SERVERS
    t = time.monotonic() if now is None else now
    if t - _LEARNED_AT < LEARN_EVERY_S:
        return
    _LEARNED_AT = t
    try:
        rows = await pool.fetch("""SELECT server FROM (
                                     SELECT DISTINCT ON (server) server, status FROM tool_gateway_calls
                                     WHERE at > now() - interval '30 days' AND (status = 'failed:signin' OR status = 'ok' OR status LIKE 'ok:%')
                                     ORDER BY server, at DESC) latest
                                   WHERE status = 'failed:signin'""")
        SIGNIN_SERVERS = {str(r["server"]) for r in rows}
    except Exception:  # noqa: BLE001 -- no audit table or no database: the in-memory list still works
        pass


class GatewayRefused(Exception):
    """A call the gateway will not make; the message says why, in words the agent can pass on."""


class GatewaySignIn(GatewayRefused):
    """The server answered 401/403: the person must sign in to it first. `challenge` is the parsed WWW-Authenticate."""

    def __init__(self, server: str, url: str, challenge: Optional[str] = None):
        super().__init__("the server needs a sign-in")
        self.server, self.url, self.challenge = server, url, oauth.parse_challenge(challenge)


def denylist() -> set[str]:
    return {x.strip() for x in os.environ.get("TOOL_GATEWAY_DENY", "").split(",") if x.strip()}


def own_domain(card: ToolCard) -> bool:
    """A server that runs on the publisher's own, registry-verified domain: com.pdfoven/pdfoven served from pdfoven.com. The company
    that owns the name is the one that answers, so a lack of GitHub stars (many company servers have none) says nothing against it.
    A GitHub namespace (io.github.<user>) proves little and still needs stars."""
    name = card.name.lower()
    ns = name.split("/")[0]
    if ns.startswith("io.github."):
        return False
    labels = ns.split(".")
    if len(labels) < 2:
        return False
    domain = ".".join(reversed(labels[:2]))
    host = (urlsplit(str((card.extra or {}).get("connector_url") or "")).hostname or "").lower()
    return bool(host) and (host == domain or host.endswith("." + domain))


def _base_allowed(card: ToolCard) -> bool:
    """Everything about the server itself: remote, verified publisher, not bulk, not archived, not denied, useful enough."""
    ex = card.extra or {}
    return (card.source == "registry" and bool(ex.get("connector_url"))
            and bool(ex.get("verified")) and int(ex.get("owner_count") or 1) <= BULK_PUBLISHER
            and (int(ex.get("stars") or 0) >= MIN_STARS or own_domain(card)) and not ex.get("archived")
            and card.name not in denylist() and os.environ.get("TOOL_GATEWAY", "on").lower() != "off")


def gateway_allowed(card: ToolCard) -> bool:
    """A remote server, live without a sign-in, verified publisher, not bulk, not archived, not denied, and either used (stars) or served
    from its publisher's own verified domain."""
    ex = card.extra or {}
    return (_base_allowed(card) and ex.get("probe") == "live"
            and str(ex.get("connector_url")) not in NEEDS_SIGNIN and card.name not in SIGNIN_SERVERS)


def signin_allowed(card: ToolCard) -> bool:
    """Phase 2 (TOOL_GATEWAY_OAUTH=on): the same server, but it may ask for a sign-in, which kell can now do itself. A server
    the registry could not reach at all (probe "down") is still left out; the sign-in flow reports a dead server clearly."""
    ex = card.extra or {}
    return oauth.enabled() and _base_allowed(card) and ex.get("probe") not in ("dead", "down")   # the probe writes "dead"


def gateway_usable(card: ToolCard) -> bool:
    return gateway_allowed(card) or signin_allowed(card)


def tool_allowed(tool: dict[str, Any]) -> tuple[bool, str]:
    name = str(tool.get("name") or "")
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True and ann.get("readOnlyHint") is not True:
        return False, "it is marked destructive"
    if ann.get("readOnlyHint") is True:
        return True, ""
    if WRITE_TOOL.search(name):
        return False, "it acts for you (sends, posts, pays, deletes or changes something), which the gateway does not do yet"
    return True, ""


def scan_arguments(arguments: Any) -> Optional[str]:
    """The name of a key or token pattern found in the arguments, or None."""
    from app.services.trace_redaction import KNOWN_TOKEN_PATTERNS

    text = json.dumps(arguments, default=str)
    for name, pat in KNOWN_TOKEN_PATTERNS:
        if pat.search(text):
            return name
    return None


# --- network ---------------------------------------------------------------------------------------------------------

async def guard_url(url: str) -> str:
    """The URL if it is https on a host that resolves only to public addresses; GatewayRefused otherwise."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise GatewayRefused("the server's address is not https")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise GatewayRefused(f"the server's address does not resolve ({type(exc).__name__})") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise GatewayRefused("the server's address is not public")
    return url


async def _post(client: httpx.AsyncClient, url: str, body: dict, headers: dict) -> tuple[httpx.Response, bytes]:
    async with client.stream("POST", url, json=body, headers=headers) as r:
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > MAX_BYTES:
                raise GatewayRefused("the server's answer is too large")
        return r, bytes(buf)


def _parse(r: httpx.Response, raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in r.headers.get("content-type", ""):
        for line in reversed(text.splitlines()):
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                    return obj
        return {}
    try:
        obj = json.loads(text)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


async def _session(url: str, method: str, params: dict, *, token: Optional[str] = None,
                   server: Optional[str] = None) -> tuple[dict, int, int]:
    """initialize, initialized, then one request -> (its JSON-RPC body, bytes sent, bytes received)."""
    await guard_url(url)
    sent = received = 0
    headers = {**HEADERS, **({"authorization": f"Bearer {token}"} if token else {})}
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as client:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "kell-tool-gateway", "version": "0.1"}}}
        r, raw = await _post(client, url, init, headers)
        sent, received = sent + len(json.dumps(init)), received + len(raw)
        if r.status_code in (401, 403):
            NEEDS_SIGNIN.add(url)
            if oauth.enabled():
                raise GatewaySignIn(server or url, url, r.headers.get("www-authenticate"))
            raise GatewayRefused("the server needs a sign-in, which the gateway does not do yet")
        if r.status_code >= 300 or "result" not in _parse(r, raw):
            raise GatewayRefused(f"the server did not start an MCP session (HTTP {r.status_code})")
        sid = r.headers.get("mcp-session-id")
        h = {**headers, **({"mcp-session-id": sid} if sid else {})}
        await client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h)
        req = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
        r, raw = await _post(client, url, req, h)
        sent, received = sent + len(json.dumps(req)), received + len(raw)
        if r.status_code in (401, 403):
            NEEDS_SIGNIN.add(url)
            if oauth.enabled():
                raise GatewaySignIn(server or url, url, r.headers.get("www-authenticate"))
            raise GatewayRefused("the server needs a sign-in, which the gateway does not do yet")
        body = _parse(r, raw)
        if sid:
            try:
                await client.delete(url, headers=h)
            except httpx.HTTPError:
                pass
        return body, sent, received


_tools_cache: dict[str, tuple[float, list[dict]]] = {}


async def list_tools(url: str, *, token: Optional[str] = None, server: Optional[str] = None) -> list[dict]:
    hit = _tools_cache.get(url)
    if hit and time.monotonic() - hit[0] < TOOLS_TTL_S:
        return hit[1]
    body, _s, _r = await _session(url, "tools/list", {}, token=token, server=server)
    tools = [t for t in ((body.get("result") or {}).get("tools") or []) if isinstance(t, dict) and t.get("name")]
    _tools_cache[url] = (time.monotonic(), tools[:100])
    return tools[:100]


async def call_tool(url: str, name: str, arguments: dict, *, token: Optional[str] = None,
                    server: Optional[str] = None) -> tuple[dict, int, int]:
    body, sent, received = await _session(url, "tools/call", {"name": name, "arguments": arguments}, token=token, server=server)
    if "error" in body:
        raise GatewayRefused(f"the server refused the call: {str((body.get('error') or {}).get('message') or '')[:300]}")
    return (body.get("result") or {}), sent, received


def content_parts(result: dict) -> tuple[str, list[tuple[bytes, str]]]:
    """A tools/call result -> (its text, joined; its images as (bytes, format)). Links and resources become text."""
    texts, images = [], []
    for c in result.get("content") or []:
        if not isinstance(c, dict):
            continue
        kind = c.get("type")
        if kind == "text":
            texts.append(str(c.get("text") or ""))
        elif kind == "image" and c.get("data"):
            try:
                images.append((base64.b64decode(c["data"]), str(c.get("mimeType") or "image/png").split("/")[-1]))
            except ValueError:
                continue
        elif kind == "resource_link":
            texts.append(f"{c.get('name') or 'link'}: {c.get('uri')}")
        elif kind == "resource" and isinstance(c.get("resource"), dict):
            res = c["resource"]
            texts.append(str(res.get("text") or res.get("uri") or ""))
    if result.get("structuredContent") and not texts:
        texts.append(json.dumps(result["structuredContent"], default=str)[:20000])
    return "\n".join(t for t in texts if t)[:50000], images[:4]


# --- consent and grants (db/163) ---------------------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def has_grant(pool: Any, owner: str, server: str) -> bool:
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM tool_grants WHERE owner_id = $1 AND server = $2 AND revoked_at IS NULL", owner, server))


async def consent_link(pool: Any, owner: str, server: str, base_url: str) -> str:
    token = secrets.token_urlsafe(24)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tool_consent_links WHERE expires_at < now() OR used_at IS NOT NULL")
        await conn.execute("""INSERT INTO tool_consent_links (token_hash, owner_id, server, expires_at)
                              VALUES ($1, $2, $3, now() + make_interval(secs => $4))""",
                           _hash(token), owner, server, float(LINK_TTL_S))
    return f"{base_url.rstrip('/')}/tools/consent/{token}"


async def link_target(pool: Any, token: str) -> Optional[tuple[str, str]]:
    """(owner, server) of an unused, unexpired link, or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""SELECT owner_id, server FROM tool_consent_links
                                     WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now()""", _hash(token))
    return (row["owner_id"], row["server"]) if row else None


async def use_link(pool: Any, token: str, allow: bool) -> Optional[tuple[str, str]]:
    """Spend the link: grant (allow) or not. -> (owner, server), or None for a bad, used or expired link."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""UPDATE tool_consent_links SET used_at = now()
                                         WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now()
                                         RETURNING owner_id, server""", _hash(token))
            if not row:
                return None
            if allow:
                await conn.execute("""INSERT INTO tool_grants (owner_id, server) VALUES ($1, $2)
                                      ON CONFLICT (owner_id, server) DO UPDATE SET granted_at = now(), revoked_at = NULL""",
                                   row["owner_id"], row["server"])
    return row["owner_id"], row["server"]


async def revoke(pool: Any, owner: str, server: str) -> bool:
    async with pool.acquire() as conn:
        status = await conn.execute("""UPDATE tool_grants SET revoked_at = now()
                                       WHERE owner_id = $1 AND server = $2 AND revoked_at IS NULL""", owner, server)
    return status.endswith(" 1")


async def audit(pool: Any, owner: Optional[str], server: str, tool: str, status: str,
                latency_ms: Optional[int] = None, bytes_in: Optional[int] = None, bytes_out: Optional[int] = None) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute("""INSERT INTO tool_gateway_calls (owner_id, server, tool, status, latency_ms, bytes_in, bytes_out)
                                  VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                               owner, server, tool[:120], status[:200], latency_ms, bytes_in, bytes_out)
    except Exception:  # noqa: BLE001 -- the audit never breaks the call
        pass


# --- rate limits (in memory, per process) -------------------------------------------------------------------------------

_windows: dict[str, deque] = {}


def allow_rate(key: str, limit: int, window_s: float) -> bool:
    now = time.monotonic()
    q = _windows.setdefault(key, deque())
    while q and q[0] <= now - window_s:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


# --- kell's own servers: capabilities kell runs itself, reached the same way (no consent link: nothing third-party is
# installed or signed in to; the provider -- DeepInfra -- is kell's own, under its own key) ---------------------------------

IMAGE_MODEL = os.environ.get("TOOL_IMAGE_MODEL", "black-forest-labs/FLUX-1-schnell")
IMAGES_PER_DAY = int(os.environ.get("TOOL_IMAGES_PER_DAY", "30"))
IMAGE_SIZES = ("1024x1024", "1024x768", "768x1024", "1280x720", "720x1280", "512x512")
BUILTIN: dict[str, dict[str, Any]] = {
    "kell/images": {
        "title": "Image generation by kell",
        "summary": "Generate an image from a description -- logos, banners, illustrations, icons, mockup art -- with an "
                   "open image model (FLUX), run by kell: no install, no sign-in. Images only, not video or audio.",
        "tools": [{"name": "generate_image",
                   "description": "Generate one image from a text description. Describe subject, style, colours and "
                                  "composition; say 'flat vector' or 'photo' for the look. Returns the image.",
                   "inputSchema": {"type": "object", "required": ["prompt"], "properties": {
                       "prompt": {"type": "string", "description": "what to draw, in detail"},
                       "size": {"type": "string", "enum": list(IMAGE_SIZES), "default": "1024x1024"}}}}],
    },
}


def builtin_available(name: str) -> bool:
    if name != "kell/images" or os.environ.get("TOOL_GATEWAY", "on").lower() == "off":
        return False
    from app.tool_routing.providers import deepinfra_key
    return bool(deepinfra_key())


def builtin_entry(name: str) -> dict[str, Any]:
    b = BUILTIN[name]
    return {"name": name, "server": name, "source": "kell", "title": b["title"], "summary": b["summary"],
            "goals": ["generate"], "tools": [t["name"] for t in b["tools"]], "via_kell": True,
            "call": f'use_tool(server="{name}", name="{b["tools"][0]["name"]}", arguments={{"prompt": "..."}})',
            "available": "runs through kell now: no install, no sign-in"}


def image_size(arguments: dict[str, Any]) -> str:
    """The supported size closest to what was asked: "size", or width/height (claude.ai sent width 2048, height 896)."""
    size = str(arguments.get("size") or "")
    if size in IMAGE_SIZES:
        return size
    try:
        w, h = int(arguments.get("width") or 0), int(arguments.get("height") or 0)
    except (TypeError, ValueError):
        w = h = 0
    if not (w > 0 and h > 0):
        m = re.match(r"^\s*(\d+)\s*[x:*]\s*(\d+)\s*$", size)
        w, h = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
    want = w / h
    return min(IMAGE_SIZES, key=lambda s: abs(int(s.split("x")[0]) / int(s.split("x")[1]) - want))


async def generate_image(prompt: str, size: str = "1024x1024") -> tuple[bytes, float]:
    """One image from DeepInfra's OpenAI-compatible images endpoint -> (png bytes, seconds)."""
    from app.tool_routing.providers import BASE, deepinfra_key

    if size not in IMAGE_SIZES:
        size = "1024x1024"
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{BASE}/openai/images/generations", headers={"authorization": f"Bearer {deepinfra_key()}"},
                              json={"model": IMAGE_MODEL, "prompt": prompt[:2000], "n": 1, "size": size,
                                    "response_format": "b64_json"})
    if r.status_code >= 300:
        raise GatewayRefused(f"the image model refused the request (HTTP {r.status_code})")
    data = (r.json().get("data") or [{}])[0]
    if not data.get("b64_json"):
        raise GatewayRefused("the image model returned no image")
    return base64.b64decode(data["b64_json"]), time.monotonic() - t0
