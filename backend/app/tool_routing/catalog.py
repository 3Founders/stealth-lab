"""Tool cards: one shape for every tool the router can rank (docs/tool_routing_plan.md).

Three sources, one card each:
  * kell's own deferred MCP tools (what discover_tools listed before), source "kell";
  * the user's installed MCP servers and their tools, sent by the client with the call, source "installed";
  * the official MCP Registry snapshot (scripts/fetch_mcp_registry.py), one card per server, source "registry".

A card's goals are tagged from its own words (app/tool_routing/goals.py marker words). Registry and installed text is
third-party data: it is matched and shown, never followed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from app.tool_routing.goals import TOOL_GOALS

DATA = Path(__file__).parent / "data"
REGISTRY_FILE = DATA / "mcp_registry.json"
EXPANSIONS_FILE = DATA / "registry_expansions.json"   # scripts/expand_tool_cards.py
PROBE_FILE = DATA / "registry_probe.json"             # scripts/probe_mcp_servers.py
BULK_PUBLISHER = 30                                  # servers under one namespace beyond which a publisher counts as bulk
WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ToolCard:
    id: str                      # "kell:call_model", "installed:<server>/<tool>", "registry:<server name>"
    source: str                  # kell | installed | registry
    name: str                    # the tool's own name (or the server's, for a registry card)
    title: str
    description: str
    server: str = ""             # the MCP server it belongs to ("" for kell's own)
    goals: tuple[str, ...] = ()
    how: str = ""                # how to reach it: "use_tool", "installed", "remote: streamable-http", "npm: @x/y"
    url: str = ""                # repository or website, for a card the user would install
    expansion: str = ""          # requests it answers and synonyms, written by a model (scripts/expand_tool_cards.py):
                                 # searched, never shown as the card's description
    tools: tuple[str, ...] = ()  # a live server's own tool names (scripts/probe_mcp_servers.py), shown with the card
    tools_text: str = ""         # those tools' names and descriptions: searched
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def text(self) -> str:
        """What retrieval matches: the name in words, the title, the server, the description."""
        return " ".join(p for p in (self.name.replace("_", " ").replace("-", " ").replace("/", " "), self.title,
                                     self.server.replace("/", " "), self.description, self.expansion, self.tools_text) if p)


def words(text: str) -> list[str]:
    """Lowercase words, plurals folded ("pages" -> "page", "queries" -> "query"): a request and a card rarely agree on
    the number."""
    return [_singular(w) for w in WORD.findall(str(text or "").lower())]


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def tag_goals(text: str, limit: int = 3) -> tuple[str, ...]:
    """The goals whose marker words a card's text uses most (at least one), at most `limit`."""
    ws = set(words(text))
    hits = sorted(((sum(1 for m in {_singular(m) for m in markers} if m in ws), gid)
                   for gid, (_d, markers) in TOOL_GOALS.items()), reverse=True)
    return tuple(gid for n, gid in hits if n > 0)[:limit]


def card_from_registry(s: dict, expansion: str = "", probe: Optional[dict] = None, owner_count: int = 1) -> ToolCard:
    how = ", ".join([f"remote: {t}" for t in s.get("remotes") or []]
                    + [f"{p.get('registry')}: {p.get('id')}" for p in s.get("packages") or [] if p.get("id")])
    name = str(s.get("name") or "")
    c = ToolCard(id=f"registry:{name}", source="registry", name=name, title=str(s.get("title") or ""),
                 description=str(s.get("description") or ""), server=name, how=how,
                 url=str(s.get("repository") or s.get("website") or ""), expansion=str(expansion or "")[:900],
                 tools=tuple(t["name"] for t in (probe or {}).get("tools") or [])[:40],
                 tools_text=" ".join(f"{t['name'].replace('_', ' ')}: {t.get('description', '')[:160]}"
                                     for t in ((probe or {}).get("tools") or [])[:40])[:3000],
                 extra={"version": s.get("version") or "", "updated": s.get("updated") or "",
                        "probe": (probe or {}).get("status") or ("none" if not s.get("remote_urls") else "unprobed"),
                        "tool_count": (probe or {}).get("tool_count"), "owner_count": owner_count,
                        "verified": verified_publisher(name, str(s.get("repository") or ""), str(s.get("website") or ""))})
    return _with_goals(c)


def verified_publisher(name: str, repository: str, website: str) -> bool:
    """The registry name's namespace is the publisher's own: io.github.<org>/... with the repository under that org, or
    a reverse domain (com.cloudflare/...) whose domain is the repository's or website's host. The registry checks the
    namespace by DNS or GitHub at publish time; this checks the card points where the namespace says."""
    ns = name.split("/")[0].lower()
    repo, site = repository.lower(), website.lower()
    if ns.startswith("io.github."):
        org = ns[len("io.github."):]
        return bool(org) and f"github.com/{org}/" in repo + "/"
    domain = ".".join(reversed(ns.split(".")))
    return bool(domain) and (f"//{domain}" in site or f".{domain}" in site or f"//{domain}" in repo or f".{domain}/" in repo)


def card_from_installed(server: str, tool: dict) -> ToolCard:
    name = str(tool.get("name") or "")
    c = ToolCard(id=f"installed:{server}/{name}", source="installed", name=name, title=str(tool.get("title") or ""),
                 description=" ".join(str(tool.get("description") or "").split())[:800], server=server, how="installed")
    return _with_goals(c)


def card_from_kell(name: str, title: str, description: str) -> ToolCard:
    return ToolCard(id=f"kell:{name}", source="kell", name=name, title=title, description=" ".join(description.split()),
                    how="use_tool")


def _with_goals(c: ToolCard) -> ToolCard:
    return ToolCard(**{**c.__dict__, "goals": tag_goals(c.text)})


JUNK_NAME = re.compile(r"\d{5,}|(^|[/._-])(test|testing|demo|example|hello|template|sample|my|foo|tmp)([/._-]|$)", re.I)


LIVENESS = {"live": 1.0, "auth": 0.9, "unprobed": 0.7, "none": 0.7, "dead": 0.05}


def quality(c: ToolCard) -> float:
    """0..1. Installed and kell tools are 1: the user chose them. A registry card: what it shows (a repository or
    website, a way to reach it, a name that is not a test or a template, a description that says something), times
    whether it answered the liveness probe (live > needs a sign-in > not probed > dead), lower for a bulk publisher
    (dozens of near-identical servers under one namespace), higher for a verified publisher."""
    if c.source != "registry":
        return 1.0
    q = 0.0
    q += 0.3 if c.url else 0.0
    q += 0.2 if c.how else 0.0
    q += 0.3 if not JUNK_NAME.search(c.name) else 0.0
    q += 0.2 if len(c.description) >= 40 else 0.0
    q *= LIVENESS.get(str(c.extra.get("probe", "none")), 0.7)
    if int(c.extra.get("owner_count") or 1) > BULK_PUBLISHER:
        q *= 0.6
    if c.extra.get("verified"):
        q = min(1.0, q + 0.15)
    return q


def read_json(path: Path) -> dict:
    """A data file as written by its script, or the committed `<name>.gz` beside it ({}: neither)."""
    import gzip
    for p, opener in ((path, open), (path.with_name(path.name + ".gz"), gzip.open)):
        try:
            with opener(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


@lru_cache(maxsize=1)
def registry_cards() -> tuple[ToolCard, ...]:
    """The bundled registry snapshot's cards ((): no snapshot)."""
    data = read_json(REGISTRY_FILE)
    if not data:
        return ()
    exp, probe = read_json(EXPANSIONS_FILE), read_json(PROBE_FILE)
    servers = [s for s in data.get("servers") or [] if s.get("name")]
    owners: dict[str, int] = {}
    for s in servers:
        ns = s["name"].split("/")[0]
        owners[ns] = owners.get(ns, 0) + 1
    # a server the probe found dead is not recommended (it stays in the snapshot)
    return tuple(card_from_registry(s, exp.get(f"registry:{s['name']}", ""), probe.get(s["name"]), owners[s["name"].split("/")[0]])
                 for s in servers if (probe.get(s["name"]) or {}).get("status") != "dead")


def installed_cards(installed: Optional[Iterable[dict]]) -> list[ToolCard]:
    """`installed`: [{"server": name, "tools": [{"name", "title"?, "description"?}]}] as the client sends it."""
    out: list[ToolCard] = []
    for srv in installed or []:
        if not isinstance(srv, dict):
            continue
        server = str(srv.get("server") or srv.get("name") or "")[:120]
        for t in (srv.get("tools") or [])[:200]:
            if isinstance(t, dict) and t.get("name"):
                out.append(card_from_installed(server, t))
    return out[:2000]
