"""
`.stealth/exploration.md` + `index/exploration.idx` -- what agents are
currently trying to *discover*, deliberately separate from Claims (what
is currently *known*). Two agents that can both see the open explorations
do not independently re-investigate the same unknown.

State lives in the journal (`exploration_opened` / `exploration_closed`
events) and is folded on read, so `exploration.md` is rebuilt correctly
on every projection regeneration -- it is never the source of truth, the
journal is.

G12 write-back: the journal alone is NOT durable knowledge -- it lives
only in this one workspace's `.stealth/` directory. When an exploration
resolves with a real answer, `close_exploration` (given a `pool`) also
captures it as a private, owner-scoped Claim in global Postgres --
otherwise a locally-resolved unknown is lost the moment the workspace is
gone, and can never become a reviewable global candidate later (the
existing `publication.py::publish_procedure` path is the promotion gate;
this only makes the private candidate durable enough to reach it). The
question/resolution are both already-known separate strings BEFORE this
call -- `subject`/`predicate`/`object` on the Claim are honest structuring
of them, not inferred. `pool` is optional and defaults to None so every
existing offline caller (and this module's own pure journal semantics)
is unaffected.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

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


async def close_exploration(
    workspace_root: str, exploration_id: str, *, status: str = "RESOLVED",
    resolution: str = "", _lock_held: bool = False,
    pool: Optional[Any] = None,
    created_by: Optional[str] = None,
    owner_id: Optional[str] = None,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
) -> Optional[str]:
    """Close (or abandon) an exploration. Returns the durable Claim id if
    one was captured, else None.

    Journal write always happens (the local record). A private Claim is
    ALSO captured when all of: `pool` is given, `status == "RESOLVED"`,
    and `resolution` is non-empty -- an exploration closed as `ABANDONED`
    or with an empty resolution records no claim (there is nothing learned
    to make durable). The claim cites the exploration id in `properties`
    so it is traceable back to this workspace's journal.
    """
    append_event(
        workspace_root, _CLOSE, _lock_held=_lock_held,
        exploration_id=exploration_id, status=status, resolution=resolution,
    )
    if pool is None or status != "RESOLVED" or not resolution.strip():
        return None

    question = "-"
    scope = "-"
    for r in list_explorations(workspace_root):
        if r["id"] == exploration_id:
            question, scope = r["question"], r["scope"]
            break

    from app.services.claims import capture_claim
    from app.services.sources import register_source

    # capture_claim requires a real provenance anchor (B7) -- task_ids=[]
    # alone is a silent no-op. A workspace exploration is a real, distinct
    # provenance origin (an agent's own local investigation, not a document
    # or a prior-library reference), so it gets a real Source row -- not a
    # synthetic ref -- identity-deduped per workspace by register_source's
    # own (source_type, locator, publisher) key, so repeated closes in the
    # same workspace reuse one Source rather than growing a new row each time.
    #
    # visibility='public' is deliberate here, even though the CLAIM it
    # provenances is private: the Source only records THAT an agent
    # investigated something in this workspace (a structural fact, not
    # confidential content) -- `owner_id` still attributes it. Privacy
    # lives on the Claim (its content, its review status), not on the
    # fact-of-investigation; a private Source here would make every
    # exploration-derived claim permanently unpublishable via
    # claim_publication.py's source-lineage check, which is not the
    # intended effect (see that module's docstring).
    src = await register_source(
        pool, source_type="agent_execution", locator=f"stealth-exploration:{workspace_root}",
        provenance="company_ingested", created_by=created_by or owner_id or "stealth_exploration",
        visibility="public", owner_id=owner_id, scope_type=scope_type, scope_entity_id=scope_entity_id,
    )

    return await capture_claim(
        pool,
        statement=f"{question} -> {resolution}",
        task_ids=[],
        source_ref=src["id"],
        subject=question,
        predicate="resolved_as",
        object=resolution,
        properties={"exploration_id": exploration_id, "scope": scope, "source": "stealth_exploration"},
        created_by=created_by or owner_id or "stealth_exploration",
        owner_id=owner_id,
        visibility="private",
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
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
