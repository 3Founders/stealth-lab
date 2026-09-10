"""
The `.stealth/` file formats: the `.idx` routing tables and the
line-range-addressable `.md` pages.

Design rules (spec A11-A12 / B35, ratified local-architecture decision):

  * An index row carries only: stable id, version, scope, status, tags,
    the file it points into, an exact 1-based inclusive line range, and a
    one-line summary. Nothing an agent would need to parse further.
  * `|` is the field separator. Field values never contain `|` or a
    newline -- `_clean` strips them. This keeps the parser a `str.split`.
  * Line numbers are DISPOSABLE optimisation metadata. Stable ids/anchors
    are authoritative; ranges MUST be regenerated after any rewrite (the
    generator does this every run -- it never edits in place).
  * The root router (`index/root.idx`) has its own shape:
    `name|target|hint` -- and MUST stay within `ROOT_IDX_MAX_BYTES`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

IDX_SEP = "|"
SUMMARY_MAX = 120

# T11: "root context.md router stays within configured byte/token budget".
# The router an agent greps first is `index/root.idx`; this cap is
# enforced by the generator (it raises rather than emit an over-budget
# router) and asserted by the budget test.
ROOT_IDX_MAX_BYTES = 4096
# Each per-type `.idx` is also bounded -- it lists only the working set,
# never the global corpus. A working set that would blow this is itself a
# signal (projection pulled in too much) and the generator raises.
TYPE_IDX_MAX_BYTES = 65536


def _clean(value: object, *, limit: int | None = None) -> str:
    """One-line, separator-safe rendering of a field value."""
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ").replace(IDX_SEP, "/")
    s = " ".join(s.split())
    if limit is not None and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def _tags(tags: Iterable[object] | None) -> str:
    if not tags:
        return "-"
    seen: list[str] = []
    for t in tags:
        c = _clean(t).replace(",", " ").strip()
        if c and c not in seen:
            seen.append(c)
    return ",".join(seen) if seen else "-"


# --------------------------------------------------------------------------
# object index rows  (claims.idx / procedures.idx / implementations.idx)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class IdxRow:
    obj_id: str
    version: str
    scope: str
    status: str
    tags: tuple[str, ...]
    file: str
    start: int
    end: int
    summary: str

    def render(self) -> str:
        return IDX_SEP.join((
            _clean(self.obj_id) or "-",
            _clean(self.version) or "-",
            _clean(self.scope) or "-",
            _clean(self.status) or "-",
            _tags(self.tags),
            _clean(self.file) or "-",
            str(self.start),
            str(self.end),
            _clean(self.summary, limit=SUMMARY_MAX) or "-",
        ))


@dataclass(frozen=True)
class RunIdxRow:
    """`run.idx` -- an agent greps this to see current work at a glance.

    `grep -E 'RUNNING|BLOCKED' .stealth/index/run.idx`
    """
    node_id: str
    status: str
    owner: str
    deps: tuple[str, ...]
    write_globs: tuple[str, ...]
    file: str
    start: int
    end: int
    summary: str

    def render(self) -> str:
        return IDX_SEP.join((
            _clean(self.node_id) or "-",
            _clean(self.status) or "-",
            _clean(self.owner) or "-",
            ",".join(_clean(d) for d in self.deps) or "-",
            ",".join(_clean(g) for g in self.write_globs) or "-",
            _clean(self.file) or "-",
            str(self.start),
            str(self.end),
            _clean(self.summary, limit=SUMMARY_MAX) or "-",
        ))


def render_idx(rows: Iterable[IdxRow | RunIdxRow], *, header: str) -> str:
    """Render a `.idx` file: a `#` comment header line then one row each."""
    out = [f"# {header}"]
    out.extend(r.render() for r in rows)
    return "\n".join(out) + "\n"


def parse_idx(text: str) -> list[list[str]]:
    """Parse a `.idx` back to a list of field lists (comments/blank
    lines skipped). Used by tests and by the P3 page-fault reader."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(line.split(IDX_SEP))
    return rows


# --------------------------------------------------------------------------
# root router  (index/root.idx)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RootRow:
    name: str
    target: str      # e.g. "claims.idx" or "../exploration.md"
    hint: str

    def render(self) -> str:
        return IDX_SEP.join((_clean(self.name), _clean(self.target), _clean(self.hint, limit=SUMMARY_MAX)))


def render_root_idx(rows: Iterable[RootRow]) -> str:
    body = "\n".join(r.render() for r in rows)
    text = "# .stealth router -- grep an entry's idx, get an id + line range, read only that range\n" + body + "\n"
    return text


# --------------------------------------------------------------------------
# markdown pages  (claims.md / procedures.md / implementations.md / run.md)
# --------------------------------------------------------------------------
@dataclass
class MdBlock:
    """One addressable object block. `heading` becomes the `## ...` line;
    `body` is the already-rendered key/value lines beneath it."""
    obj_id: str
    heading: str
    body: list[str] = field(default_factory=list)
    # carried through onto the matching index row
    version: str = "-"
    scope: str = "-"
    status: str = "-"
    tags: tuple[str, ...] = ()
    summary: str = ""


@dataclass
class RenderedPage:
    text: str
    # obj_id -> (start_line, end_line)  1-based inclusive, into `text`
    ranges: dict[str, tuple[int, int]]


def render_md_page(title: str, blocks: list[MdBlock]) -> RenderedPage:
    """
    Render a page and record the exact line range of every block so the
    caller can build the matching `.idx`. Layout:

        # <title> -- GENERATED, not canonical. Do not hand-edit.
        <blank>
        ## <heading>
        <body...>
        <blank>
        ## <heading>
        ...
    """
    lines: list[str] = [f"# {title} -- GENERATED, not canonical. Do not hand-edit.", ""]
    ranges: dict[str, tuple[int, int]] = {}
    for block in blocks:
        start = len(lines) + 1  # the "## ..." line, 1-based
        lines.append(f"## {block.heading}")
        for bl in block.body:
            lines.append(bl.rstrip())
        end = len(lines)
        ranges[block.obj_id] = (start, end)
        lines.append("")  # separator
    text = "\n".join(lines).rstrip() + "\n"
    return RenderedPage(text=text, ranges=ranges)


def kv(key: str, value: object) -> str:
    return f"{key}: {_clean(value)}"
