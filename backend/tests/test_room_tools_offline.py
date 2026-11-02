"""The MCP room tools (P5) without a database: the @handle a connector posts under, the event it builds, and the refusals.
The store itself (append, order, membership) is covered by the rooms tests."""
import uuid

import pytest

from app.services import rooms


def test_the_gateway_owner_is_the_signed_in_person_not_a_stale_name():
    """_gateway_owner is what attributes (and audits) every gateway call; it must resolve the current actor."""
    from app.services.authn import Actor, reset_current_actor, set_current_actor

    import app.mcp_server.server as srv

    tok = set_current_actor(Actor(subject="person-1"))
    try:
        assert srv._gateway_owner() == "person-1"
    finally:
        reset_current_actor(tok)


def test_a_connector_posts_under_a_handle_from_its_client_id():
    assert rooms.agent_handle("claude-ai") == "@claude-ai"
    assert rooms.agent_handle("Claude Web") == "@claude-web"
    assert rooms.agent_handle("service:web-chat") == "@web-chat"
    assert rooms.agent_handle(None) == "@agent"
    assert rooms.agent_handle("3f0e8c1d-1111-4222-8333-444455556666") == "@agent"      # a subject id, not a name
    assert rooms.agent_handle("https://example.com/x").startswith("@") and len(rooms.agent_handle("https://example.com/x")) <= 39


def test_a_handle_is_always_one_the_rooms_service_accepts():
    for raw in ("claude-ai", "Claude Web", "service:web-chat", None, "", "x" * 200, "@@@", "3f0e8c1d-1111-4222-8333-444455556666"):
        handle = rooms.agent_handle(raw)
        assert rooms.HANDLE_RE.match(handle), (raw, handle)


def test_an_agent_event_is_one_message_at_depth_zero_and_mentions_are_parsed():
    ev = rooms.agent_event("@claude", "  @assistant please look at this  ")
    assert ev["kind"] == "msg" and ev["sender_kind"] == "agent" and ev["sender_handle"] == "@claude"
    assert ev["depth"] == 0 and ev["lamport"] == 0 and ev["body"] == "@assistant please look at this"
    uuid.UUID(str(ev["id"]))
    cleaned, why = rooms._validate(ev)
    assert why is None and cleaned is not None
    assert cleaned["mentions"] == ["assistant"]                       # who a room event may wake, from the text
    assert cleaned["depth"] == 0 and cleaned["sender_kind"] == "agent"


def test_an_agent_event_can_answer_another_event():
    target = str(uuid.uuid4())
    assert rooms.agent_event("@claude", "yes", target)["reply_to"] == target


@pytest.mark.parametrize("text", ["", "   ", "x" * (rooms.MAX_BODY + 1)])
def test_an_empty_or_oversized_room_post_is_refused_with_a_sentence(text):
    with pytest.raises(rooms.RoomError) as exc:
        rooms.agent_event("@claude", text)
    assert str(exc.value)
