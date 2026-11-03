"""A canvas is never named by an id (app/services/canvas_names.py): new documents get a name read from their text, and documents made earlier under a
UUID or a number keep their stored title but are listed under a readable label."""
from __future__ import annotations

from app.services import canvas_names as cn
from app.services import room_docs as rd
from tests.test_room_docs_offline import ROOM, Pool, run


def test_ids_are_not_names_and_real_titles_are():
    for ugly in ("0e2a3067-4ddd-4fef-831b-0bdb68343104", "0e2a30674ddd4fef831b0bdb68343104", "3", "12-4", "1.html", "x9k2m4p7q1r5"):
        assert cn.is_ugly(ugly), ugly
    for fine in ("Q4 plan review", "Crumb Bakery", "History of the Internet Slides", "Four-Day Work Week Memo", "Rocket engine"):
        assert not cn.is_ugly(fine), fine


def test_a_name_is_read_from_the_text():
    assert cn.label_for("0e2a3067-4ddd-4fef-831b-0bdb68343104", "html", "<!doctype html><title>Crumb Bakery</title>") == "Crumb Bakery"
    assert cn.label_for("3", "document", "# Four Day Week\n\ntext") == "Four Day Week"
    assert cn.label_for("0e2a3067-4ddd-4fef-831b-0bdb68343104", "html", "<section><h2>Why now</h2></section>") == "Why now"
    assert cn.label_for("Launch plan", "document", "# Something else") == "Launch plan"                 # a real title is kept
    assert cn.label_for("3", "code", "") == "Code"                                                         # nothing to read: a plain word, never the id


def test_a_new_document_is_never_saved_under_an_id_and_an_old_one_is_listed_under_a_label():
    pool = Pool()
    made = run(rd.create_doc(pool, "alice", str(ROOM), "0e2a3067-4ddd-4fef-831b-0bdb68343104", "html", "<!doctype html><title>Crumb Bakery</title><p>x</p>"))
    assert made["title"] == "Crumb Bakery"
    assert rd.title_of("Plain name", "document", "# Memo") == "Plain name"
    row = {"id": "x", "title": "0e2a3067-4ddd-4fef-831b-0bdb68343104", "kind": "html", "version": 1, "updated_by": "a", "updated_at": None,
           "body": "<!doctype html><title>Old Deck</title>"}
    listed = rd._row(row)
    assert listed["title"].startswith("0e2a3067") and listed["label"] == "Old Deck"       # the stored title stays; the room shows the name
