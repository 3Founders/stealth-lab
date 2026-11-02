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
