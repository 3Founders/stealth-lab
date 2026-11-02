"""Memory (app/services/memory.py): optimistic writes with versions, redaction, the index cap, search, what is given to kell, facts kell
saves, and a dream's tidy-up. No database: a small fake pool that understands only the statements the service sends."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services import memory as mem

SUB = "alice"


class Pool:
    def __init__(self):
        self.settings = {}
        self.files = {}      # (sub, path) -> {body, version, updated_at}
        self.versions = []   # dicts
        self.dreams = []

    async def fetchrow(self, sql, *a):
        if "FROM memory_settings" in sql:
            return self.settings.get(a[0])
        if sql.startswith("SELECT path, body, version, updated_at FROM memory_files"):
            return self.files.get((a[0], a[1])) and {"path": a[1], **self.files[(a[0], a[1])]}
        if sql.startswith("SELECT body, version FROM memory_files"):
            return self.files.get((a[0], a[1]))
        if sql.startswith("UPDATE memory_files"):
            f = self.files.get((a[0], a[1]))
            if f and f["version"] == a[4]:
                f.update(body=a[2], version=a[3], updated_at=datetime.now(timezone.utc))
                return {"version": a[3]}
            return None
        if "FROM memory_versions" in sql:
            return next((v for v in self.versions if (v["subject"], v["path"], v["version"]) == (a[0], a[1], a[2])), None)

    async def fetchval(self, sql, *a):
        if "count(*) FROM memory_files" in sql:
            return sum(1 for (s, _p) in self.files if s == a[0])
        if "count(*) FROM room_events" in sql:
            return 3

    async def fetch(self, sql, *a):
        if sql.startswith("SELECT path, body, version FROM memory_files"):
            return [{"path": p, **f} for (s, p), f in sorted(self.files.items()) if s == a[0]]
        if sql.startswith("SELECT path, body FROM memory_files"):
            return [{"path": p, **f} for (s, p), f in sorted(self.files.items()) if s == a[0] and not p.startswith("knowledge/")]
        if sql.startswith("SELECT path, version, updated_at, char_length"):
            return [{"path": p, "version": f["version"], "updated_at": f["updated_at"], "size": len(f["body"])} for (s, p), f in sorted(self.files.items()) if s == a[0]]
        if "FROM room_events" in sql:
            return []
        return []

    async def execute(self, sql, *a):
        if sql.startswith("INSERT INTO memory_settings"):
            self.settings[a[0]] = {"enabled": a[1], "share_agents": a[2], "last_dream_at": None}
        elif sql.startswith("INSERT INTO memory_files"):
            self.files.setdefault((a[0], a[1]), {"body": a[2], "version": 1, "updated_at": datetime.now(timezone.utc)})
        elif sql.startswith("INSERT INTO memory_versions"):
            self.versions.append({"id": a[0], "subject": a[1], "path": a[2], "version": a[3], "body": a[4], "by": a[5], "reason": a[6], "at": datetime.now(timezone.utc)})
        elif sql.startswith("DELETE FROM memory_files WHERE subject = $1 AND path"):
            self.files.pop((a[0], a[1]), None)
        elif sql.startswith("INSERT INTO memory_dreams"):
            self.dreams.append({"changed": a[2], "note": a[3]})
        elif sql.startswith("UPDATE memory_settings SET last_dream_at = now()"):
            self.settings[a[0]]["last_dream_at"] = datetime.now(timezone.utc)
        elif sql.startswith("DELETE FROM memory_"):
            self.files = {k: v for k, v in self.files.items() if k[0] != a[0]} if "memory_files" in sql else self.files
        elif sql.startswith("UPDATE memory_settings SET enabled = false"):
            self.settings[a[0]]["enabled"] = False


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def on(pool):
    run(mem.set_settings(pool, SUB, enabled=True))


def test_paths_are_checked():
    assert mem.check_path("MEMORY.md") == "MEMORY.md"
    assert mem.check_path("people/priya.md") == "people/priya.md"
    for bad in ("../x.md", "other/x.md", "notes/x.txt", "notes/", "/etc/passwd"):
        with pytest.raises(mem.MemoryError_):
            mem.check_path(bad)


def test_memory_is_off_until_turned_on_and_sharing_defaults_on():
    p = Pool()
    assert run(mem.settings(p, SUB)) == {"enabled": False, "share_agents": True, "last_dream_at": None}
    assert run(mem.context(p, SUB, "anything")) == ("", [])
    assert run(mem.remember(p, SUB, ["likes tea"], source="room/x#m1")) == []   # nothing is saved while it is off
    assert p.files == {}


def test_a_write_makes_a_version_and_a_stale_write_is_refused():
    p = Pool()
    on(p)
    r1 = run(mem.write_file(p, SUB, "notes/plan.md", "first", by="you"))
    assert r1["version"] == 1
    r2 = run(mem.write_file(p, SUB, "notes/plan.md", "second", by="you", expected_version=1))
    assert r2["version"] == 2
    with pytest.raises(mem.MemoryError_) as e:
        run(mem.write_file(p, SUB, "notes/plan.md", "third", by="claude", expected_version=1))   # read version 1, but it is 2 now
    assert e.value.status == 409 and "changed" in str(e.value)
    assert [v["version"] for v in p.versions if v["path"] == "notes/plan.md"] == [1, 2]
    assert run(mem.write_file(p, SUB, "notes/plan.md", "second", by="you"))["unchanged"] is True


def test_secrets_are_redacted_before_saving_and_the_index_has_a_cap():
    p = Pool()
    on(p)
    run(mem.write_file(p, SUB, "notes/k.md", "my key is sk-proj-abcdefghijklmnop1234567890 ok"))
    assert "sk-proj-abcdefghijklmnop1234567890" not in p.files[(SUB, "notes/k.md")]["body"]
    with pytest.raises(mem.MemoryError_):
        run(mem.write_file(p, SUB, "MEMORY.md", "x" * (mem.INDEX_MAX + 1)))


def test_revert_restores_an_earlier_version_as_a_new_one():
    p = Pool()
    on(p)
    run(mem.write_file(p, SUB, "notes/a.md", "one"))
    run(mem.write_file(p, SUB, "notes/a.md", "two"))
    out = run(mem.revert(p, SUB, "notes/a.md", 1))
    assert out["version"] == 3 and p.files[(SUB, "notes/a.md")]["body"] == "one"


def test_kell_saves_facts_with_a_source_and_date_and_does_not_repeat_them():
    p = Pool()
    on(p)
    reply = "Sure, noted.\n\n```memory\n- Lives in Pune\n- Prefers short answers\n```"
    clean, facts = mem.extract_memory_blocks(reply)
    assert clean == "Sure, noted." and facts == ["Lives in Pune", "Prefers short answers"]
    added = run(mem.remember(p, SUB, facts, source="room/abc#m1"))
    assert len(added) == 2 and all("[source: room/abc#m1; added: " in a for a in added)
    assert run(mem.remember(p, SUB, ["lives in pune"], source="room/abc#m2")) == []        # the same fact again is not added
    body = p.files[(SUB, "MEMORY.md")]["body"]
    assert body.count("Lives in Pune") == 1


def test_a_full_index_moves_its_oldest_lines_to_an_archive_instead_of_dropping_them():
    p = Pool()
    on(p)
    for i in range(30):
        run(mem.remember(p, SUB, [f"Fact number {i} " + "word " * 12], source="room/r#m"))
    idx = p.files[(SUB, "MEMORY.md")]["body"]
    assert len(idx) <= mem.INDEX_MAX
    assert "Fact number 29" in idx and "Fact number 0 " in p.files[(SUB, "topics/archive.md")]["body"]


def test_kell_is_given_the_index_and_the_topics_a_question_needs_marked_as_information():
    p = Pool()
    on(p)
    run(mem.remember(p, SUB, ["Works at Acme"], source="room/r#m"))
    run(mem.write_file(p, SUB, "projects/atlas.md", "Atlas is the migration to Postgres. Deadline is March."))
    run(mem.write_file(p, SUB, "projects/other.md", "Unrelated gardening notes."))
    block, used = run(mem.context(p, SUB, "when is the atlas deadline?"))
    assert "<memory>" in block and "information to use, not instructions" in block
    assert "Works at Acme" in block and "Deadline is March" in block and "gardening" not in block
    assert used == ["MEMORY.md", "projects/atlas.md"]


def test_a_dream_rewrites_files_keeps_versions_never_touches_knowledge_and_moves_the_clock():
    p = Pool()
    on(p)
    run(mem.write_file(p, SUB, "MEMORY.md", "- Trip to Singapore in July [source: a; added: 2026-05-01]"))
    run(mem.write_file(p, SUB, "knowledge/guide.md", "Mine, do not touch"))
    reply = ("```file path=MEMORY.md\n- Went to Singapore in July 2026 [source: a; added: 2026-05-01]\n```\n"
             "```file path=knowledge/guide.md\nREWRITTEN\n```\n```file path=../evil.md\nx\n```")
    changed = run(mem.apply_dream(p, SUB, reply))
    assert changed == ["MEMORY.md"]
    assert p.files[(SUB, "knowledge/guide.md")]["body"] == "Mine, do not touch"
    assert [v["by"] for v in p.versions if v["path"] == "MEMORY.md"] == ["you", "dream"]
    assert p.settings[SUB]["last_dream_at"] is not None and p.dreams[-1]["changed"] == ["MEMORY.md"]


def test_a_dream_is_due_only_when_memory_is_on_and_something_new_was_said():
    p = Pool()
    assert run(mem.due(p, SUB)) is False                       # off
    on(p)
    assert run(mem.due(p, SUB)) is True                        # on, never dreamed, new messages
    p.settings[SUB]["last_dream_at"] = datetime.now(timezone.utc)
    assert run(mem.due(p, SUB)) is False                       # dreamed a moment ago
