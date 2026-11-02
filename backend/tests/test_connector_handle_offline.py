"""How a connected agent is named in a room: @username/claude, @username/chatgpt, @username/claude-code."""
import asyncio

from app.services import rooms as rm


class Pool:
    def __init__(self, username=None, display=None):
        self.username, self.display = username, display

    async def fetchrow(self, sql, *a):
        return {"username": self.username} if "contributor_profiles" in sql and self.username else None

    async def fetch(self, sql, *a):
        return [{"external_subject": a[0][0], "display_name": self.display}] if self.display else []


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_the_tool_is_read_from_the_client_id():
    assert rm.connector_tool("claude-ai") == "claude"
    assert rm.connector_tool("claude-code") == "claude-code"
    assert rm.connector_tool("chatgpt-mcp") == "chatgpt"
    assert rm.connector_tool("550e8400-e29b-41d4-a716-446655440000") == "agent"


def test_the_handle_is_the_persons_username_slash_the_tool():
    assert run(rm.connector_handle(Pool(username="Asha_R"), "u1", "claude-ai")) == "@asha_r/claude"
    assert run(rm.connector_handle(Pool(username="asha"), "u1", "claude-code")) == "@asha/claude-code"
    assert run(rm.connector_handle(Pool(display="Asha Rao"), "u1", "chatgpt")) == "@asha-rao/chatgpt"
    assert run(rm.connector_handle(Pool(), "u1", None)) == "@agent/agent"


def test_such_a_handle_is_a_valid_agent_sender():
    assert rm.HANDLE_RE.match("@asha/claude-code")
    assert rm.agent_event("@asha/claude", "hi")["sender_handle"] == "@asha/claude"
