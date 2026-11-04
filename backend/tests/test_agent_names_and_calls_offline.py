"""Agents are named exactly as they joined (@owner/tool, @owner/tool/chat), @name calls your own and @owner/name calls someone else's, and the
answer of a called agent goes back to the room (app/services/delegate.py, app/services/rooms.py)."""
from __future__ import annotations

import asyncio

from app.services import delegate as dg
from app.services import rooms as rm


def run(coro):
    return asyncio.run(coro)


def test_mentions_keep_who_is_meant():
    assert dg.mention_pairs("hi @claude and @alice/claude/Q4 plan and @bob/chatgpt, @claude again") == [(None, "claude"), ("alice", "claude"), ("bob", "chatgpt")]
    assert dg.mentions("@alice/web-chat and @web-chat") == ["web-chat"]


def test_a_handle_is_owner_tool_and_optionally_a_chat_title():
    ok = ["@kell", "@alice", "@alice/claude", "@alice/claude-code", "@alice/claude-code/Q4 plan review", "@alice/chatgpt/My chat: v2"]
    bad = ["@alice/Chat name with space", "@alice/claude/", "@alice//x", "alice/claude", "@alice/claude/<b>"]
    assert all(rm.HANDLE_RE.match(h) for h in ok), [h for h in ok if not rm.HANDLE_RE.match(h)]
    assert not any(rm.HANDLE_RE.match(h) for h in bad), [h for h in bad if rm.HANDLE_RE.match(h)]


class Pool:
    def __init__(self, username=None, display=None):
        self.username, self.display = username, display

    async def fetchrow(self, sql, *a):
        if "contributor_profiles" in sql:
            return {"username": self.username} if self.username else None
        return None

    async def fetch(self, sql, *a):
        if "FROM users" in sql and self.display:
            return [{"external_subject": "u1", "display_name": self.display}]
        return []


def test_a_connected_agent_is_named_as_it_joined():
    p = Pool(username="Chaitanya")
    assert run(rm.connector_handle(p, "u1", "claude-code")) == "@chaitanya/claude-code"
    assert run(rm.connector_handle(p, "u1", "Anthropic/ClaudeAI")) == "@chaitanya/claude"
    assert run(rm.connector_handle(p, "u1", "claude-code", chat="Q4  plan review")) == "@chaitanya/claude-code/Q4 plan review"
    assert rm.HANDLE_RE.match(run(rm.connector_handle(p, "u1", "claude", chat="a/b@c<d>")))              # a chat title can never break the handle


def test_an_agent_listing_says_what_to_type_and_whether_it_is_connected():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    row = {"id": "x", "name": "claude", "kind": "agent", "created_at": now, "last_seen": now - timedelta(seconds=20), "revoked_at": None}
    mine = dg._agent_json(row, now, "chaitanya", True)
    theirs = dg._agent_json(row, now, "alice", False)
    assert mine["handle"] == "@claude" and mine["connected"] is True
    assert theirs["handle"] == "@alice/claude" and theirs["full_handle"] == "@alice/claude" and theirs["mine"] is False
    stale = dg._agent_json({**row, "last_seen": now - timedelta(minutes=5)}, now, "alice", False)
    assert stale["connected"] is False


def test_someone_elses_agent_carries_its_owners_display_name_for_a_label():
    """The panel's badge says "Anuj's" rather than "theirs": the listing carries the owner's display name beside the username
    the handle uses. It may be empty (no display name on the account), and your own agents may leave it empty."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    row = {"id": "a1", "name": "claude", "kind": "agent", "last_seen": now, "revoked_at": None, "created_at": now}
    theirs = dg._agent_json(row, now, "slatesummit", False, "Anuj Bhatt")
    assert theirs["owner"] == "slatesummit" and theirs["owner_name"] == "Anuj Bhatt"
    assert theirs["handle"] == "@slatesummit/claude"                      # the handle still uses the username
    assert dg._agent_json(row, now, "slatesummit", False)["owner_name"] == ""   # older callers: empty, not an error
