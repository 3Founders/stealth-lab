"""The canvas history groups saves the way Overleaf does: one person's saves close together are one entry, and an entry that ends where it
started (type a space, delete it) is not shown. Every save is still stored; only the list changes."""
from datetime import datetime, timezone

from app.services.room_docs import GROUP_GAP_S, GROUP_SPAN_S, group_versions


def save(v, at, h, by="Me", note="", src=None, size=10):
    return {"version": v, "by": by, "note": note, "src": src, "at": datetime.fromtimestamp(1_790_000_000 + at, tz=timezone.utc), "size": size, "h": h}


def test_a_character_typed_and_taken_back_leaves_no_entry():
    rows = [save(1, 0, "A"), save(2, 1000, "A+space"), save(3, 1002, "A")]
    out = group_versions(rows)
    assert [e["version"] for e in out] == [1]


def test_one_burst_of_typing_is_one_entry_and_restore_brings_back_its_end():
    rows = [save(1, 0, "A"), save(2, 1000, "B"), save(3, 1010, "C"), save(4, 1020, "D")]
    out = group_versions(rows)
    assert [(e["version"], e["from"], e["edits"]) for e in out] == [(4, 2, 3), (1, 1, 1)]


def test_a_pause_another_person_or_a_long_session_starts_a_new_entry():
    rows = [save(1, 0, "A"), save(2, 1000, "B"), save(3, 1000 + GROUP_GAP_S + 1, "C"),       # a pause
            save(4, 1000 + GROUP_GAP_S + 2, "D", by="pal")]                                   # someone else
    assert [e["version"] for e in group_versions(rows)] == [4, 3, 2, 1]
    long = [save(1, 0, "A")] + [save(i, 1000 + (i - 2) * (GROUP_GAP_S - 1), f"x{i}") for i in range(2, 2 + int(GROUP_SPAN_S // (GROUP_GAP_S - 1)) + 3)]
    assert len(group_versions(long)) >= 3                                                    # capped at an hour per entry


def test_merges_restores_and_agent_versions_are_entries_of_their_own_even_when_nothing_changed():
    rows = [save(1, 0, "A"), save(2, 10, "B"), save(3, 20, "B", note="merged with another edit"), save(4, 30, "A", note="restored an earlier version"),
            save(5, 40, "A", by="@kell", src="msg-1")]
    assert [e["version"] for e in group_versions(rows)] == [5, 4, 3, 2, 1]


def test_an_entry_after_a_hidden_one_compares_with_the_text_before_both():
    rows = [save(1, 0, "A"), save(2, 1000, "A1"), save(3, 1001, "A"),                         # hidden: back to A
            save(4, 1000 + GROUP_GAP_S + 5, "A"), save(5, 1000 + GROUP_GAP_S + 6, "B")]
    assert [e["version"] for e in group_versions(rows)] == [5, 1]
