"""
`.stealth/exploration.md` + `index/exploration.idx` -- what agents are
currently trying to *discover*, deliberately separate from Claims (what
is currently *known*). Two agents that can both see the open explorations
do not independently re-investigate the same unknown.

State lives in the journal (`exploration_opened` / `exploration_closed`
events) and is folded on read, so `exploration.md` is rebuilt correctly
on every projection regeneration -- it is never the source of truth, the
journal is.
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.stealth.format import IdxRow, MdBlock, kv, render_md_page
from app.stealth.journal import append_event, read_events

_OPEN = "exploration_opened"
_CLOSE = "exploration_closed"


def exploration_id(question: str, scope: str = "") -> str:
    raw = f"{question}|{scope}".strip().lower()
    return "E-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def open_exploration(
    workspace_root: str, *, owner: str, question: str, scope: str = "-",
    status: str = "ACTIVE", _lock_held: bool = False,
) -> str:
    """Record (or re-open) an exploration. Returns its stable id."""
    eid = exploration_id(question, scope)
    append_event(
        workspace_root, _OPEN, _lock_held=_lock_held,
        exploration_id=eid, owner=owner, question=question, scope=scope, status=status,
    )
    return eid


def close_exploration(
    workspace_root: str, exploration_id: str, *, status: str = "RESOLVED",
    resolution: str = "", _lock_held: bool = False,
) -> None:
    append_event(
        workspace_root, _CLOSE, _lock_held=_lock_held,
        exploration_id=exploration_id, status=status, resolution=resolution,
    )


def list_explorations(workspace_root: str, *, include_closed: bool = True) -> list[dict]:
    """Fold the journal into current exploration state, newest-opened last."""
    state: dict[str, dict] = {}
    order: list[str] = []
    for evt in read_events(workspace_root):
        etype = evt.get("type")
        eid = evt.get("exploration_id")
        if not eid:
            continue
        if etype == _OPEN:
            if eid not in state:
                order.append(eid)
            state[eid] = {
                "id": eid, "owner": evt.get("owner", "-"), "question": evt.get("question", ""),
                "scope": evt.get("scope", "-"), "status": evt.get("status", "ACTIVE"),
                "opened_seq": evt.get("seq"),
            }
        elif etype == _CLOSE and eid in state:
            state[eid]["status"] = evt.get("status", "RESOLVED")
            state[eid]["resolution"] = evt.get("resolution", "")
            state[eid]["closed_seq"] = evt.get("seq")
    rows = [state[eid] for eid in order]
    if not include_closed:
        rows = [r for r in rows if r["status"] in ("ACTIVE", "OPEN")]
    return rows


def render_exploration_page(workspace_root: str) -> tuple[str, list[IdxRow]]:
    """`exploration.md` text + its index rows. Empty (honest) when there
    are no explorations on record."""
    rows = list_explorations(workspace_root)
    if not rows:
        page = ("# exploration.md -- GENERATED, not canonical. Do not hand-edit.\n\n"
                "(no open explorations -- agents have not recorded any active unknowns)\n")
        return page, []
    blocks: list[MdBlock] = []
    for r in rows:
        body = [
            kv("owner", r["owner"]),
            kv("question", r["question"]),
            kv("status", r["status"]),
            kv("scope", r["scope"]),
        ]
        if r.get("resolution"):
            body.append(kv("resolution", r["resolution"]))
        blocks.append(MdBlock(
            obj_id=r["id"], heading=f"EXPLORATION {r['id']}", body=body,
            status=r["status"], scope=r["scope"], tags=("exploration",),
            summary=r["question"][:100],
        ))
    rendered = render_md_page("exploration.md", blocks)
    idx_rows = [
        IdxRow(
            obj_id=b.obj_id, version="-", scope=b.scope, status=b.status, tags=b.tags,
            file="exploration.md", start=rendered.ranges[b.obj_id][0],
            end=rendered.ranges[b.obj_id][1], summary=b.summary,
        )
        for b in blocks
    ]
    return rendered.text, idx_rows
