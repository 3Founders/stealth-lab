"""Offline tests for the Apollo CSV import: strict by default, and the column check says what it matched."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apollo_csv as ac  # noqa: E402
import contacts_db as cd  # noqa: E402

HEAD = "First Name,Last Name,Title,Company,Email,Email Status,Primary Email Catch-all Status,Country"


def write(tmp_path, *rows, head=HEAD, bom=False):
    f = tmp_path / "apollo.csv"
    f.write_text(("﻿" if bom else "") + "\n".join([head, *rows]) + "\n", encoding="utf-8")
    return f


def run(tmp_path, *rows, **kw):
    lines, store = [], cd.Contacts(":memory:")
    head = kw.pop("head", HEAD)
    r = ac.import_apollo(store, write(tmp_path, *rows, head=head, bom=kw.pop("bom", False)), out=lines.append, **kw)
    return r, store, "\n".join(lines)


def test_imports_verified_only_and_skips_catch_all_invalid_and_unverified(tmp_path):
    r, store, _ = run(tmp_path,
                      "Ann,Lee,CTO,Corp,ann@corp.com,Verified,,United States",
                      "Bob,Ray,VP Eng,Corp,bob@corp.com,Unverified,,India",
                      "Cy,Day,CTO,Corp2,cy@corp2.com,Verified,Catch-all,United Kingdom",
                      "Di,Fox,CTO,Corp3,not-an-email,Verified,,United States",
                      ",,,,,,,")
    assert r["ok"] and r["added"] == 1 and store.status("ann@corp.com") == "new"
    assert store.status("bob@corp.com") is None and store.status("cy@corp2.com") is None
    row = store.rows()[0]
    assert (row["name"], row["company"], row["country"], row["source"]) == ("Ann Lee", "Corp", "United States", "apollo-csv")
    assert r["skipped"]["catch-all domain"] == 1 and sum(r["skipped"].values()) == 4


def test_check_mode_writes_nothing_and_reports_the_match(tmp_path):
    r, store, text = run(tmp_path, "Ann,Lee,CTO,Corp,ann@corp.com,Verified,,US", check_only=True)
    assert r["would_add"] == 1 and r["added"] == 0 and store.rows() == []
    assert "email    <- Email" in text and "status   <- Email Status" in text


def test_headers_match_loosely_and_a_bom_is_fine(tmp_path):
    r, store, _ = run(tmp_path, "Ann,Lee,Corp,ann@corp.com,verified", head="first_name,LAST NAME,Company Name,E-mail Address,EMAIL STATUS", bom=True)
    assert not r["ok"] or store.status("ann@corp.com") == "new"   # "E-mail Address" normalises to "emailaddress"
    assert store.status("ann@corp.com") == "new"


def test_refuses_a_file_without_an_email_column_or_without_a_status_column(tmp_path):
    r, store, text = run(tmp_path, "Ann,Corp", head="First Name,Company")
    assert not r["ok"] and "no email column" in text and store.rows() == []
    r2, store2, text2 = run(tmp_path, "ann@corp.com,Ann", head="Email,First Name")
    assert not r2["ok"] and "Email Status" in text2 and store2.rows() == []
    r3, store3, _ = run(tmp_path, "ann@corp.com,Ann", head="Email,First Name", allow_status=())
    assert r3["ok"] and store3.status("ann@corp.com") == "new"


def test_known_and_final_addresses_are_never_changed(tmp_path):
    lines, store = [], cd.Contacts(":memory:")
    store.finalize("ann@corp.com", "unsubscribed", "asked")
    r = ac.import_apollo(store, write(tmp_path, "Ann,Lee,CTO,Corp,ann@corp.com,Verified,,US"), out=lines.append)
    assert r["added"] == 0 and store.status("ann@corp.com") == "unsubscribed"


class _Mem(cd.Contacts):
    def __init__(self):
        super().__init__(":memory:")


def test_cli_import_apollo_check(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "Contacts", _Mem)
    monkeypatch.setattr("send_mail.ENV_FILE", tmp_path / "absent.env")
    f = write(tmp_path, "Ann,Lee,CTO,Corp,ann@corp.com,Verified,,US")
    out = []
    assert cd.main(["import-apollo", str(f), "--check"], out=out.append, environ={}) == 0
    assert any("would add: 1" in l for l in out)
    assert cd.main(["import-apollo", str(write(tmp_path, "x", head="Foo"))], out=out.append, environ={}) == 2
