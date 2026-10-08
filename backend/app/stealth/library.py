"""
`.stealth/library.md` and `.stealth/routing.md`: this repository's own solved problems and the
model routes chosen for them (docs/plan_2026-10_priors_library_survey.md §3).

library.md is the chapter of "problems solved HERE" -- each entry is a Goal, the Procedure that
solved it, its steps, and a pointer to the diff that did it (`library/solutions/<id>.diff`).
Evidence for why this is the core (plan §0): same-repo past fixes with their real diffs lifted
"right cause" from 27 to 42 of 96; steps-only knowledge did nothing.

    GOAL|L-7f3a1c|<title>|unit=<path or .>|g=<global goal id or ->|outcome=pass|status=current|verified_at=2026-10-07|route=R-7f3a1c|tags=<csv or ->
    PROC|L-7f3a1c.p1|<name>|p=<global procedure id or ->|solution=solutions/L-7f3a1c.diff|touches=src/x.py#sha=1a2b3c4,...
    STEP|L-7f3a1c.p1:1|<action|instruction|subgoal>|<do>|check=<command or ->

Above the entries sits the reusable KNOWLEDGE layer (2026-10-08): Goals with parents, the Ways (procedures) that
achieve them, and their Steps -- deduplicated, with content-hash ids, so the same problem solved twice shares one
Goal and the same procedure one Way. Entries link to it (goal= on GOAL, way= on PROC).

    G|G-1a2b3c4d|<title>|parent=<G-id or ->|g=<global goal id or ->|unit=<path or .>|tags=<csv or ->
    W|W-5e6f7a8b|<name>|goal=<G-id>|p=<global procedure id or ->|v=<version>
    S|W-5e6f7a8b:1|<action|instruction|subgoal>|<do>|check=<command or ->

routing.md is generated from find_ways' `model_plan` plus this machine's own outcomes:

    ROUTE|R-7f3a1c|goal=<L-id or ->|g=<global goal id>|fit=<fit id or ->|basis=<prior|posterior|->|as_of=<date>|step=*|ladder=<model>::<scaffold>:p=0.81[0.70,0.89]:$0.04 > ...|whole=p=0.86[0.75,0.93]:$0.06
    OBS|R-7f3a1c|<model>|<scaffold>|n=<attempts>|ok=<accepted>|last=<date>

Design rules:
  * Ids are random (`L-`/`R-` + 6 hex) and authoritative. A line belongs to its entry by its id
    prefix, never by its position, so a `merge=union` git merge (both sides' lines kept, in any
    order) still parses to the right entries. Line ranges in `index/library.idx` are disposable.
  * Free-text fields keep their exact text: `|`, newlines and `%XX` are percent-escaped
    (`%7C`, `%0A`, `%25XX`) instead of `format._clean`'s lossy `|` -> `/`, because a `check=`
    command with a shell pipe must survive the round trip.
  * Pure: parse and render only. The local tooling that reads git and writes files is the npm
    client (packaging/npm/lib/library.mjs), which implements this same grammar; the shared
    fixtures in packaging/npm/test/fixtures/library keep the two in step.

The server only ever sees `library.idx` rows and routing lines the client chooses to send
(find_ways' `library_rows` / `route_obs`), for one request; it never stores them.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

SEP = "|"
LIBRARY_MAX_BYTES = 65536          # library.md itself; older entries move to library/archive-*.md
LIBRARY_ROWS_MAX_BYTES = 65536     # what find_ways accepts as `library_rows`
ROUTE_OBS_MAX_BYTES = 16384        # what find_ways accepts as `route_obs`

ENTRY_ID = re.compile(r"^L-[0-9a-f]{4,16}$")
ROUTE_ID = re.compile(r"^R-[0-9a-f]{4,16}$")
_PROC_ID = re.compile(r"^(L-[0-9a-f]{4,16})\.p([0-9]+)$")
_STEP_ID = re.compile(r"^(L-[0-9a-f]{4,16})\.p([0-9]+):([0-9]+)$")
KGOAL_ID = re.compile(r"^G-[0-9a-f]{6,16}$")
WAY_ID = re.compile(r"^W-[0-9a-f]{6,16}$")
_WAY_STEP_ID = re.compile(r"^(W-[0-9a-f]{6,16}):([0-9]+)$")
_ESC_PCT = re.compile(r"%(?=[0-9A-Fa-f]{2})")
_PCT = re.compile(r"%([0-9A-Fa-f]{2})")

OUTCOMES = ("pass", "historical", "fail")
STATUSES = ("current", "stale")
STEP_KINDS = ("action", "instruction", "subgoal")

LIBRARY_HEADER = (
    "# library.md -- problems solved in THIS repository, with the diffs that solved them.\n"
    "# Committed (team knowledge). One entry per id; lines may be in any order (merge=union safe).\n"
    "# GOAL|<L-id>|<title>|unit=<path or .>|g=<global goal id or ->|outcome=<pass|historical|fail>|"
    "status=<current|stale>|verified_at=<date>|route=<R-id or ->|tags=<csv or ->\n"
    "# PROC|<L-id>.p<n>|<name>|p=<global procedure id or ->|solution=<solutions/<L-id>.diff or ->|"
    "touches=<path#sha=<sha>,...>\n"
    "# STEP|<L-id>.p<n>:<k>|<action|instruction|subgoal>|<do>|check=<command or ->\n"
    "# Knowledge (reusable; content-hash ids): G|<G-id>|<title>|parent=|g=|unit=|tags=   "
    "W|<W-id>|<name>|goal=<G-id>|p=|v=   S|<W-id>:<k>|<kind>|<do>|check=\n"
    "# An entry links to it with goal=<G-id> on GOAL and way=<W-id> on PROC.\n"
)
ROUTING_HEADER = (
    "# routing.md -- GENERATED. ROUTE lines come from find_ways' model_plan (replaced on each plan);\n"
    "# OBS lines are this machine's own attempt counts per route (sent back as find_ways' route_obs).\n"
    "# ROUTE|<R-id>|goal=<L-id or ->|g=<global goal id>|fit=<fit id or ->|basis=<prior|posterior|->|"
    "as_of=<date>|step=<*|order>|ladder=<model>::<scaffold>:p=<mean>[<q05>,<q95>]:$<cost> > ...|"
    "whole=p=<mean>[<q05>,<q95>]:$<cost>\n"
    "# OBS|<R-id>|<model>|<scaffold>|n=<attempts>|ok=<accepted>|last=<date>\n"
)
IDX_HEADER = (
    "# library.idx -- GENERATED from library.md (rebuilt whenever source_sha differs). Do not hand-edit.\n"
    "# id|status|outcome|unit|g|verified_at|start|end|block_sha|title\n"
)


# ---------------------------------------------------------------- escaping

def esc(value: object) -> str:
    """A field value that can never split a line or a field, and decodes back exactly."""
    s = "" if value is None else str(value)
    s = _ESC_PCT.sub("%25", s)
    return s.replace("|", "%7C").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "%0A")


def unesc(value: str) -> str:
    return _PCT.sub(lambda m: chr(int(m.group(1), 16)), value)


def _esc_list_item(value: str) -> str:
    """A csv item (touches path, tag): also `,` and `#` (the touches `path#sha=` separator)."""
    return esc(value).replace(",", "%2C").replace("#", "%23")


def _kv(key: str, value: object) -> str:
    return f"{key}={esc('-' if value in (None, '') else value)}"


# values whose parts are escaped one by one (csv items, ladder rungs): decoded after they are split
_RAW_KEYS = ("touches", "tags", "ladder")
_COUNT = re.compile(r"^[0-9]+$")


def _split_kv(fields: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in fields:
        k, sep, v = f.partition("=")
        if sep and k and k not in out:
            out[k] = v if k in _RAW_KEYS else unesc(v)
    return out


def _dash(v: Optional[str]) -> Optional[str]:
    return None if v in (None, "", "-") else v


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- library.md model

@dataclass
class Touch:
    path: str
    sha: str


@dataclass
class LibStep:
    order: int
    kind: str
    do: str
    check: Optional[str] = None
    extra: list[str] = field(default_factory=list)       # fields this grammar does not know, kept verbatim


@dataclass
class LibProc:
    index: int
    name: str
    p: Optional[str] = None
    solution: Optional[str] = None
    touches: list[Touch] = field(default_factory=list)
    steps: list[LibStep] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    way: Optional[str] = None                             # the knowledge Way this procedure is an instance of


@dataclass
class KGoal:
    id: str
    title: str
    parent: Optional[str] = None
    g: Optional[str] = None
    unit: str = "."
    tags: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


@dataclass
class Way:
    id: str
    name: str
    goal: Optional[str] = None
    p: Optional[str] = None
    v: int = 1
    steps: list[LibStep] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


@dataclass
class LibEntry:
    id: str
    title: str
    unit: str = "."
    g: Optional[str] = None
    outcome: str = "pass"
    status: str = "current"
    verified_at: Optional[str] = None
    route: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    procs: list[LibProc] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)       # e.g. the survey's commit=<sha>; kept, never dropped
    goal: Optional[str] = None                            # the knowledge Goal this problem is an instance of


@dataclass
class Library:
    entries: list[LibEntry]
    problems: list[str] = field(default_factory=list)     # lines skipped or conflicts resolved, for the reader
    goals: list[KGoal] = field(default_factory=list)
    ways: list[Way] = field(default_factory=list)

    def by_id(self) -> dict[str, LibEntry]:
        return {e.id: e for e in self.entries}


# ---------------------------------------------------------------- render

def render_goal_line(e: LibEntry) -> str:
    return SEP.join([
        "GOAL", e.id, esc(e.title.strip()), _kv("unit", e.unit or "."), _kv("g", e.g),
        _kv("outcome", e.outcome), _kv("status", e.status), _kv("verified_at", e.verified_at),
        _kv("route", e.route), "tags=" + (",".join(_esc_list_item(t) for t in e.tags) if e.tags else "-"),
        *([_kv("goal", e.goal)] if e.goal else []), *e.extra,
    ])


def render_proc_line(entry_id: str, p: LibProc) -> str:
    touches = ",".join(f"{_esc_list_item(t.path)}#sha={t.sha}" for t in p.touches) or "-"
    return SEP.join(["PROC", f"{entry_id}.p{p.index}", esc(p.name.strip()), _kv("p", p.p),
                     _kv("solution", p.solution), "touches=" + touches, *([_kv("way", p.way)] if p.way else []),
                     *p.extra])


def render_step_line(entry_id: str, proc_index: int, s: LibStep) -> str:
    return SEP.join(["STEP", f"{entry_id}.p{proc_index}:{s.order}", esc(s.kind), esc(s.do.strip()),
                     _kv("check", s.check), *s.extra])


def render_block(e: LibEntry) -> list[str]:
    lines = [render_goal_line(e)]
    for p in sorted(e.procs, key=lambda p: p.index):
        lines.append(render_proc_line(e.id, p))
        lines.extend(render_step_line(e.id, p.index, s) for s in sorted(p.steps, key=lambda s: s.order))
    return lines


def render_kgoal_line(g: KGoal) -> str:
    return SEP.join(["G", g.id, esc(g.title.strip()), _kv("parent", g.parent), _kv("g", g.g), _kv("unit", g.unit or "."),
                     "tags=" + (",".join(_esc_list_item(t) for t in g.tags) if g.tags else "-"), *g.extra])


def render_way_line(w: Way) -> str:
    return SEP.join(["W", w.id, esc(w.name.strip()), _kv("goal", w.goal), _kv("p", w.p), _kv("v", w.v or 1), *w.extra])


def render_way_step_line(way_id: str, s: LibStep) -> str:
    return SEP.join(["S", f"{way_id}:{s.order}", esc(s.kind), esc(s.do.strip()), _kv("check", s.check), *s.extra])


def render_knowledge(goals: Sequence[KGoal], ways: Sequence[Way]) -> list[str]:
    """Each Goal, then the Ways that achieve it with their Steps; Ways whose Goal is not declared come last."""
    def way_block(w: Way) -> list[str]:
        return [render_way_line(w), *(render_way_step_line(w.id, s) for s in sorted(w.steps, key=lambda s: s.order))]

    known = {g.id for g in goals}
    blocks = []
    for g in sorted(goals, key=lambda g: g.id):
        lines = [render_kgoal_line(g)]
        for w in sorted((w for w in ways if w.goal == g.id), key=lambda w: w.id):
            lines.extend(way_block(w))
        blocks.append("\n".join(lines))
    blocks.extend("\n".join(way_block(w)) for w in sorted((w for w in ways if w.goal not in known), key=lambda w: w.id))
    return blocks


def render_library(lib: Library | Iterable[LibEntry]) -> str:
    """Canonical text: header, the knowledge blocks, then one block per entry sorted by id, blank line between."""
    entries = lib.entries if isinstance(lib, Library) else list(lib)
    knowledge = render_knowledge(lib.goals, lib.ways) if isinstance(lib, Library) else []
    blocks = knowledge + ["\n".join(render_block(e)) for e in sorted(entries, key=lambda e: e.id)]
    return LIBRARY_HEADER + ("\n" + "\n\n".join(blocks) + "\n" if blocks else "")


# ---------------------------------------------------------------- parse

def _parse_touches(raw: str) -> list[Touch]:
    out: list[Touch] = []
    if raw in ("", "-"):
        return out
    for item in raw.split(","):
        path, sep, sha = item.rpartition("#sha=")
        if sep and path and sha:
            out.append(Touch(unesc(path), sha.strip()))
    return out


_GOAL_KEYS = frozenset(("unit", "g", "outcome", "status", "verified_at", "route", "tags", "goal"))
_PROC_KEYS = frozenset(("p", "solution", "touches", "way"))
_KGOAL_KEYS = frozenset(("parent", "g", "unit", "tags"))
_WAY_KEYS = frozenset(("goal", "p", "v"))
_STEP_KEYS = frozenset(("check",))


def _extra_fields(fields: Sequence[str], known: frozenset) -> list[str]:
    """Fields another writer added (the survey's commit=<sha>, diff=truncated, ...): kept verbatim and in order,
    so canonicalising library.md never loses them. A repeated known key is not extra (the first one counts)."""
    out = []
    for f in fields:
        k, sep, _ = f.partition("=")
        if not (sep and k in known) and f:
            out.append(f)
    return out


def _parse_tags(raw: str) -> list[str]:
    return [] if raw in ("", "-") else [unesc(t) for t in raw.split(",") if t]


def parse_library(text: str) -> Library:
    """Lines grouped by their id prefix (never by position). Exact duplicate lines collapse (a
    union merge of the same edit on two branches). Two different lines for the same id are a
    conflict: the GOAL with the later verified_at wins (ties: the larger line, so every reader
    picks the same one); PROC/STEP keep the larger line. Every conflict and every line that
    does not fit the grammar is listed in `problems`, never silently dropped or invented."""
    problems: list[str] = []
    goals: dict[str, tuple[str, list[str]]] = {}
    procs: dict[tuple[str, int], tuple[str, list[str]]] = {}
    steps: dict[tuple[str, int, int], tuple[str, list[str]]] = {}
    kgoals: dict[str, tuple[str, list[str]]] = {}
    ways: dict[str, tuple[str, list[str]]] = {}
    wsteps: dict[tuple[str, int], tuple[str, list[str]]] = {}

    def keep(store: dict, key: Any, line: str, fields: list[str], *, rank: Any) -> None:
        old = store.get(key)
        if old is None or old[0] == line:
            store[key] = (line, fields)
            return
        problems.append(f"conflict: two versions of {key if isinstance(key, str) else key[0]}; kept one")
        if rank(fields, line) > rank(old[1], old[0]):
            store[key] = (line, fields)

    goal_rank = lambda f, line: (_split_kv(f[3:]).get("verified_at") or "", line)   # noqa: E731
    line_rank = lambda f, line: line                                                  # noqa: E731

    for n, raw in enumerate(text.split("\n"), 1):     # "\n" only: str.splitlines also splits on U+2028 etc.
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        f = line.split(SEP)
        kind = f[0]
        if kind == "GOAL" and len(f) >= 3 and ENTRY_ID.match(f[1]):
            keep(goals, f[1], line, f, rank=goal_rank)
        elif kind == "PROC" and len(f) >= 3 and _PROC_ID.match(f[1]):
            m = _PROC_ID.match(f[1])
            keep(procs, (m.group(1), int(m.group(2))), line, f, rank=line_rank)
        elif kind == "STEP" and len(f) >= 4 and _STEP_ID.match(f[1]):
            m = _STEP_ID.match(f[1])
            keep(steps, (m.group(1), int(m.group(2)), int(m.group(3))), line, f, rank=line_rank)
        elif kind == "G" and len(f) >= 3 and KGOAL_ID.match(f[1]):
            keep(kgoals, f[1], line, f, rank=line_rank)
        elif kind == "W" and len(f) >= 3 and WAY_ID.match(f[1]):
            keep(ways, f[1], line, f, rank=line_rank)
        elif kind == "S" and len(f) >= 4 and _WAY_STEP_ID.match(f[1]):
            m = _WAY_STEP_ID.match(f[1])
            keep(wsteps, (m.group(1), int(m.group(2))), line, f, rank=line_rank)
        else:
            problems.append(f"line {n}: not a library line, skipped")

    entries: dict[str, LibEntry] = {}
    for gid, (_line, f) in goals.items():
        kv = _split_kv(f[3:])
        entries[gid] = LibEntry(
            id=gid, title=unesc(f[2]), unit=_dash(kv.get("unit")) or ".", g=_dash(kv.get("g")),
            outcome=kv.get("outcome") if kv.get("outcome") in OUTCOMES else "pass",
            status=kv.get("status") if kv.get("status") in STATUSES else "current",
            verified_at=_dash(kv.get("verified_at")), route=_dash(kv.get("route")),
            tags=_parse_tags(kv.get("tags", "-")), extra=_extra_fields(f[3:], _GOAL_KEYS),
            goal=kv["goal"] if KGOAL_ID.match(kv.get("goal") or "") else None)
    proc_objs: dict[tuple[str, int], LibProc] = {}
    for (gid, idx), (_line, f) in sorted(procs.items()):
        if gid not in entries:
            problems.append(f"orphan: {gid}.p{idx} has no GOAL line; skipped")
            continue
        kv = _split_kv(f[3:])
        p = LibProc(index=idx, name=unesc(f[2]), p=_dash(kv.get("p")), solution=_dash(kv.get("solution")),
                    touches=_parse_touches(kv.get("touches", "-")), extra=_extra_fields(f[3:], _PROC_KEYS),
                    way=kv["way"] if WAY_ID.match(kv.get("way") or "") else None)
        entries[gid].procs.append(p)
        proc_objs[(gid, idx)] = p
    for (gid, pidx, order), (_line, f) in sorted(steps.items()):
        p = proc_objs.get((gid, pidx))
        if p is None:
            problems.append(f"orphan: {gid}.p{pidx}:{order} has no PROC line; skipped")
            continue
        kv = _split_kv(f[4:])
        p.steps.append(LibStep(order=order, kind=unesc(f[2]), do=unesc(f[3]), check=_dash(kv.get("check")),
                               extra=_extra_fields(f[4:], _STEP_KEYS)))
    kgoal_list = []
    for gid, (_line, f) in sorted(kgoals.items()):
        kv = _split_kv(f[3:])
        kgoal_list.append(KGoal(id=gid, title=unesc(f[2]),
                                parent=kv["parent"] if KGOAL_ID.match(kv.get("parent") or "") else None,
                                g=_dash(kv.get("g")), unit=_dash(kv.get("unit")) or ".",
                                tags=_parse_tags(kv.get("tags", "-")), extra=_extra_fields(f[3:], _KGOAL_KEYS)))
    way_map: dict[str, Way] = {}
    for wid, (_line, f) in sorted(ways.items()):
        kv = _split_kv(f[3:])
        way_map[wid] = Way(id=wid, name=unesc(f[2]), goal=kv["goal"] if KGOAL_ID.match(kv.get("goal") or "") else None,
                           p=_dash(kv.get("p")), v=int(kv["v"]) if _COUNT.match(kv.get("v") or "") else 1,
                           extra=_extra_fields(f[3:], _WAY_KEYS))
    for (wid, order), (_line, f) in sorted(wsteps.items()):
        w = way_map.get(wid)
        if w is None:
            problems.append(f"orphan: {wid}:{order} has no W line; skipped")
            continue
        kv = _split_kv(f[4:])
        w.steps.append(LibStep(order=order, kind=unesc(f[2]), do=unesc(f[3]), check=_dash(kv.get("check")),
                               extra=_extra_fields(f[4:], _STEP_KEYS)))
    return Library(sorted(entries.values(), key=lambda e: e.id), problems, kgoal_list, list(way_map.values()))


# ---------------------------------------------------------------- index/library.idx

@dataclass
class IdxRow:
    id: str
    status: str
    outcome: str
    unit: str
    g: Optional[str]
    verified_at: Optional[str]
    start: int
    end: int
    block_sha: str
    title: str

    def render(self) -> str:
        return SEP.join([self.id, self.status, self.outcome, esc(self.unit), self.g or "-",
                         self.verified_at or "-", str(self.start), str(self.end), self.block_sha, esc(self.title)])


def library_idx_rows(canonical_text: str) -> list[IdxRow]:
    """Rows for a CANONICAL library.md (render_library output): each block's exact line range
    and the hash of its lines, which is what a reader checks before trusting the range."""
    lib = parse_library(canonical_text)
    lines = canonical_text.split("\n")
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for n, line in enumerate(lines, 1):
        head = line.split(SEP, 2)
        if len(head) < 2 or head[0] not in ("GOAL", "PROC", "STEP"):
            continue
        gid = head[1].split(".", 1)[0]
        first.setdefault(gid, n)
        last[gid] = n
    rows = []
    for e in lib.entries:
        s, t = first[e.id], last[e.id]
        rows.append(IdxRow(e.id, e.status, e.outcome, e.unit, e.g, e.verified_at, s, t,
                           short_hash("\n".join(lines[s - 1:t])), e.title))
    return rows


def render_library_idx(canonical_text: str) -> str:
    rows = library_idx_rows(canonical_text)
    return (IDX_HEADER + f"# source_sha={short_hash(canonical_text)}\n"
            + "".join(r.render() + "\n" for r in rows))


def parse_library_rows(text: str, *, max_bytes: int = LIBRARY_ROWS_MAX_BYTES) -> tuple[list[IdxRow], bool]:
    """find_ways' `library_rows`: library.idx rows (comments skipped, malformed rows skipped).
    Returns (rows, truncated)."""
    raw = text.encode("utf-8")
    truncated = len(raw) > max_bytes
    if truncated:
        text = raw[:max_bytes].decode("utf-8", "ignore").rsplit("\n", 1)[0]
    rows: list[IdxRow] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        f = line.split(SEP)
        if len(f) < 10 or not ENTRY_ID.match(f[0]) or f[0] in seen:
            continue
        start, end = (int(f[6]), int(f[7])) if _COUNT.match(f[6]) and _COUNT.match(f[7]) else (0, 0)
        seen.add(f[0])
        rows.append(IdxRow(f[0], f[1] if f[1] in STATUSES else "current", f[2] if f[2] in OUTCOMES else "pass",
                           unesc(f[3]) or ".", _dash(f[4]), _dash(f[5]), start, end, f[8], unesc(SEP.join(f[9:]))))
    return rows, truncated


# ---------------------------------------------------------------- routing.md

@dataclass
class Rung:
    model: str
    scaffold: str
    p_mean: Optional[float] = None
    p_q05: Optional[float] = None
    p_q95: Optional[float] = None
    cost: Optional[float] = None

    @property
    def unit(self) -> str:
        return f"{self.model}|{self.scaffold}"


@dataclass
class Route:
    id: str
    goal: Optional[str]
    g: Optional[str]
    fit: Optional[str] = None
    basis: Optional[str] = None
    as_of: Optional[str] = None
    step: str = "*"
    ladder: list[Rung] = field(default_factory=list)
    whole: Optional[Rung] = None        # the whole ladder's P(ok)/cost when only that is known (model is "")


@dataclass
class Obs:
    route: str
    model: str
    scaffold: str
    n: int
    ok: int
    last: Optional[str] = None

    @property
    def unit(self) -> str:
        return f"{self.model}|{self.scaffold}"


_NUMS = re.compile(r"^p=(?P<p>[-0-9.]+|-)\[(?P<lo>[-0-9.]+|-),(?P<hi>[-0-9.]+|-)\]:\$(?P<c>[-0-9.]+|-)$")
_RUNG = re.compile(r"^(?P<unit>.+?):p=(?P<p>[-0-9.]+|-)\[(?P<lo>[-0-9.]+|-),(?P<hi>[-0-9.]+|-)\]:\$(?P<c>[-0-9.]+|-)$")


def _num(v: Optional[float], digits: int) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def _fnum(v: str) -> Optional[float]:
    try:
        x = None if v == "-" else float(v)
    except ValueError:
        return None
    return x if x is None or math.isfinite(x) else None


def render_rung(r: Rung) -> str:
    unit = f"{esc(r.model)}::{esc(r.scaffold)}"
    if r.p_mean is None and r.p_q05 is None and r.p_q95 is None and r.cost is None:
        return unit
    return f"{unit}:{_render_numbers(r)}"


def _render_numbers(r: Rung) -> str:
    return f"p={_num(r.p_mean, 2)}[{_num(r.p_q05, 2)},{_num(r.p_q95, 2)}]:${_num(r.cost, 4)}"


def render_route_line(r: Route) -> str:
    return SEP.join(["ROUTE", r.id, _kv("goal", r.goal), _kv("g", r.g), _kv("fit", r.fit), _kv("basis", r.basis),
                     _kv("as_of", r.as_of), _kv("step", r.step or "*"),
                     "ladder=" + (" > ".join(render_rung(x) for x in r.ladder) or "-"),
                     "whole=" + (_render_numbers(r.whole) if r.whole else "-")])


def render_obs_line(o: Obs) -> str:
    return SEP.join(["OBS", o.route, esc(o.model), esc(o.scaffold), f"n={int(o.n)}", f"ok={int(o.ok)}",
                     _kv("last", o.last)])


def _parse_rungs(raw: str) -> list[Rung]:
    rungs = []
    if raw in ("", "-"):
        return rungs
    for part in raw.split(" > "):
        m = _RUNG.match(part.strip())
        model, sep, scaffold = (m.group("unit") if m else part.strip()).partition("::")
        if not (sep and model and scaffold):
            continue
        if m:
            rungs.append(Rung(unesc(model), unesc(scaffold), _fnum(m.group("p")), _fnum(m.group("lo")),
                              _fnum(m.group("hi")), _fnum(m.group("c"))))
        else:
            rungs.append(Rung(unesc(model), unesc(scaffold)))
    return rungs


def parse_routing(text: str, *, max_bytes: Optional[int] = None) -> tuple[list[Route], list[Obs]]:
    """ROUTE and OBS lines (anything else skipped). For one route+step the LAST ROUTE line wins
    (a newer plan replaces an older one); OBS lines for the same route+unit are summed, so a
    union merge of two machines' files still counts every attempt once per line."""
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore").rsplit("\n", 1)[0]
    routes: dict[tuple[str, str], Route] = {}
    obs: dict[tuple[str, str, str], Obs] = {}
    for line in text.split("\n"):
        f = line.rstrip("\r").split(SEP)
        if f[0] == "ROUTE" and len(f) >= 3 and ROUTE_ID.match(f[1]):
            kv = _split_kv(f[2:])
            whole = _NUMS.match(kv.get("whole", "-"))
            r = Route(f[1], _dash(kv.get("goal")), _dash(kv.get("g")), _dash(kv.get("fit")), _dash(kv.get("basis")),
                      _dash(kv.get("as_of")), kv.get("step") or "*", _parse_rungs(kv.get("ladder", "-")),
                      Rung("", "", _fnum(whole.group("p")), _fnum(whole.group("lo")), _fnum(whole.group("hi")),
                           _fnum(whole.group("c"))) if whole else None)
            routes[(r.id, r.step)] = r
        elif f[0] == "OBS" and len(f) >= 6 and ROUTE_ID.match(f[1]):
            kv = _split_kv(f[4:])
            if not (_COUNT.match(kv.get("n", "")) and _COUNT.match(kv.get("ok", ""))):
                continue
            n, ok = int(kv["n"]), int(kv["ok"])
            if n < 0 or ok < 0 or ok > n:
                continue
            key = (f[1], unesc(f[2]), unesc(f[3]))
            prev = obs.get(key)
            last = _dash(kv.get("last"))
            if prev is None:
                obs[key] = Obs(f[1], key[1], key[2], n, ok, last)
            else:
                prev.n, prev.ok = prev.n + n, prev.ok + ok
                prev.last = max(filter(None, (prev.last, last)), default=None)
    return list(routes.values()), list(obs.values())


def local_obs_for_goal(routes: Sequence[Route], obs: Sequence[Obs], goal_id: str) -> list[dict[str, Any]]:
    """The attempt counts that bear on one global Goal: OBS of every route whose `g` is it,
    summed per unit. These are the sufficient statistics routing conditions on."""
    route_ids = {r.id for r in routes if r.g == goal_id}
    acc: dict[str, list[int]] = {}
    for o in obs:
        if o.route in route_ids:
            a = acc.setdefault(o.unit, [0, 0])
            a[0] += o.n
            a[1] += o.ok
    return [{"unit": u, "n": n, "ok": ok} for u, (n, ok) in sorted(acc.items()) if n > 0]


def routes_from_model_plan(plan: Mapping[str, Any], *, route_id: str, goal: Optional[str], g: str,
                           as_of: str) -> list[Route]:
    """ROUTE rows for a find_ways `model_plan`. Accepts the plan §5.2 shape
    ({basis, fit_id, as_of, steps: [{step, ladder: [{unit, p_ok_mean, p_ok_q05, p_ok_q95, cost_mean}]}]})
    and the current task-level shape ({status: ok, ladder: [units], recommended: {...}}), where only
    the whole ladder's success and cost are known (per-rung numbers are left `-`, never invented)."""
    routes: list[Route] = []
    fit = plan.get("fit_id") or (str(plan["params_version"]) if plan.get("params_version") is not None else None)
    basis = plan.get("basis")
    if isinstance(plan.get("steps"), list):
        for st in plan["steps"]:
            rungs = []
            for r in st.get("ladder") or []:
                model, _, scaffold = str(r.get("unit") or "").partition("|")
                if model and scaffold:
                    rungs.append(Rung(model, scaffold, r.get("p_ok_mean"), r.get("p_ok_q05"), r.get("p_ok_q95"),
                                      r.get("cost_mean")))
            routes.append(Route(route_id, goal, g, fit, basis, plan.get("as_of") or as_of, str(st.get("step", "*")),
                                rungs))
        return routes
    if plan.get("status") != "ok" or not plan.get("ladder"):
        return routes
    rec = plan.get("recommended") or {}
    rungs = []
    for unit in plan["ladder"]:
        model, _, scaffold = str(unit).partition("|")
        if model and scaffold:
            rungs.append(Rung(model, scaffold))
    whole = Rung("", "", rec.get("p_success"), rec.get("p_success_q05"), rec.get("p_success_q95"),
                 rec.get("expected_cost_usd")) if rec else None
    routes.append(Route(route_id, goal, g, fit, basis, as_of, "*", rungs, whole))
    return routes
