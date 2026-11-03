"""A long agent reply is posted in full as several room messages instead of being refused (app/services/rooms.py split_body)."""
from app.services import rooms as rm


def test_short_reply_is_one_piece():
    assert rm.split_body("hello") == ["hello"]


def test_long_reply_is_split_at_paragraphs_within_the_limit_and_loses_nothing():
    para = ("word " * 300).strip()                     # about 1,500 characters
    text = (rm.PARA).join([para] * 12)                 # about 18,000 characters
    parts = rm.split_body(text)
    assert len(parts) >= 3
    assert all(0 < len(p) <= rm.MAX_BODY for p in parts)
    assert " ".join(parts).split() == text.split()     # every word, in order
    assert parts[0].endswith("word")                   # cut at a paragraph, not in the middle of a word


def test_unbroken_text_is_still_cut_at_the_limit():
    parts = rm.split_body("x" * (rm.MAX_BODY * 2 + 5))
    assert [len(p) for p in parts] == [rm.MAX_BODY, rm.MAX_BODY, 5]


def test_agent_events_makes_one_event_per_piece():
    evs = rm.agent_events("@kell", "a" * (rm.MAX_BODY + 10))
    assert len(evs) == 2 and all(e["sender_handle"] == "@kell" and e["kind"] == "msg" for e in evs)


def _block(title, n, kind="html"):
    return f'```canvas title="{title}" kind={kind}\n' + ("<p>x</p>\n" * n) + "```"


def test_a_canvas_block_is_never_cut_by_the_split():
    from app.services import rooms as rm

    big = _block("Deck", 700)                                  # about 6,300 characters
    text = ("intro " * 700) + "\n\n" + big + "\n\n" + ("outro " * 700)
    pieces = rm.split_body(text, 8000)
    assert all(len(p) <= 8000 for p in pieces)
    holders = [p for p in pieces if "canvas title=" in p]
    assert len(holders) == 1 and holders[0].startswith("```canvas") and holders[0].rstrip().endswith("```")   # whole, opening to closing fence


def test_a_canvas_too_big_for_a_message_is_saved_as_the_rooms_canvas_and_leaves_a_card(monkeypatch):
    import asyncio

    from app.services import room_docs as rd
    from app.services import rooms as rm

    seen = {}

    async def fake_capture(pool, room_id, sub, handle, message_id, text):
        seen["text"] = text
        return 1

    monkeypatch.setattr(rd, "capture", fake_capture)
    huge = _block("Big deck", 1500)                            # about 13,500 characters: more than a message may hold
    out = asyncio.run(rm.externalize_canvases(object(), "u1", "room", "@kell", "Made it.\n\n" + huge + "\n\nDone."))
    assert "<p>x</p>" not in out and 'canvas title="Big deck" kind=html' in out        # the message keeps an empty card
    assert seen["text"].startswith("```canvas") and seen["text"].count("<p>x</p>") == 1500
    small = _block("Small", 10)
    assert asyncio.run(rm.externalize_canvases(object(), "u1", "room", "@kell", small)) == small     # a block that fits stays as it is


def test_an_empty_block_never_blanks_a_canvas():
    from app.services import room_docs as rd

    assert rd.canvas_blocks('```canvas title="X" kind=html\n\n```')[0][2].strip() == ""
