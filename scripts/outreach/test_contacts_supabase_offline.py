"""Offline tests for the Supabase-backed contact list: a fake PostgREST stands in for the network; nothing here connects anywhere."""
import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contacts_db as cd  # noqa: E402
import contacts_supabase as cs  # noqa: E402
import send_mail as sm  # noqa: E402
from test_contacts_db_offline import FakeImap, mail  # noqa: E402
from test_send_mail_offline import Fake, S, draft  # noqa: E402

KEY = "service-key-SECRET-123"


class FakeRest:
    """Just enough of PostgREST: eq./in. filters, insert (ignore-duplicates), patch, and the events table."""

    def __init__(self):
        self.contacts, self.events, self.down, self.fail_with = {}, [], False, None

    def __call__(self, method, url, headers, body):
        if self.down:
            raise OSError("connection refused")
        assert headers["apikey"] == KEY and headers["Authorization"] == "Bearer " + KEY
        if self.fail_with:
            return self.fail_with, ("boom " + KEY).encode()
        u = urlparse(url)
        table, q = u.path.rsplit("/", 1)[1], dict(parse_qsl(u.query))
        data = json.loads(body) if body else None
        if table == "outreach_events":
            self.events.append(data)
            return 201, b""
        def pick():
            rows = list(self.contacts.values())
            if "email" in q:
                rows = [r for r in rows if r["email"] == q["email"].removeprefix("eq.")]
            if "status" in q:
                v = q["status"]
                allowed = v[4:-1].split(",") if v.startswith("in.(") else [v.removeprefix("eq.")]
                rows = [r for r in rows if r["status"] in allowed]
            return rows
        if method == "POST":
            if data["email"] in self.contacts:
                return 201, b"[]"
            self.contacts[data["email"]] = {"status": "new", **data}
            return 201, json.dumps([self.contacts[data["email"]]]).encode()
        if method == "GET":
            return 200, json.dumps(pick()).encode()
        if method == "PATCH":
            for r in pick():
                r.update(data)
            return 204, b""
        raise AssertionError(method)


def store():
    rest = FakeRest()
    return cs.SupabaseContacts("https://x.supabase.co", KEY, http=rest), rest


def test_add_status_and_final_statuses_behave_like_the_local_list():
    c, rest = store()
    assert c.add("A@Corp.com", company="Corp") and not c.add("a@corp.com")
    c.advance("a@corp.com", "sent")
    c.advance("a@corp.com", "approved")                  # never moves back
    assert c.status("a@corp.com") == "sent"
    c.finalize("a@corp.com", "unsubscribed", "asked")
    c.advance("a@corp.com", "replied")
    c.finalize("a@corp.com", "bounced")
    assert c.status("a@corp.com") == "unsubscribed" and c.is_blocked("a@corp.com")
    assert [e["kind"] for e in rest.events] == ["added", "sent", "unsubscribed"]
    assert c.summary() == {"unsubscribed": 1}


def test_blocked_and_contacted_sets():
    c, _ = store()
    for e in ("a@x.com", "b@x.com", "c@x.com"):
        c.add(e)
    c.advance("a@x.com", "sent")
    c.finalize("b@x.com", "do_not_contact")
    assert c.blocked() == {"b@x.com"} and c.contacted() == {"a@x.com"}
    c.finalize("never-added@x.com", "unsubscribed")
    assert not c.add("never-added@x.com")


def test_sender_uses_it_and_records_sends(tmp_path):
    c, _ = store()
    c.finalize("no@corp.com", "unsubscribed")
    fake = Fake()
    out = sm.send_drafts([draft(to="no@corp.com"), draft(to="yes@corp.com")], S, really=True, out=lambda *_: None, connect_fn=lambda s: fake,
                         sleep=lambda s: None, log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", contacts=c)
    assert out["sent"] == 1 and out["skipped"] == 1 and c.status("yes@corp.com") == "sent"
    again = sm.send_drafts([draft(to="yes@corp.com")], S, really=True, out=lambda *_: None, connect_fn=lambda s: Fake(), sleep=lambda s: None,
                           log_path=tmp_path / "other.csv", suppress_path=tmp_path / "s.txt", contacts=c)
    assert again["sent"] == 0 and again["skipped"] == 1       # the shared list knows it was sent, even with a fresh local log


def test_unreachable_list_means_nothing_is_sent_and_a_dry_run_warns(tmp_path):
    c, rest = store()
    rest.down = True
    lines = []

    def must_not_connect(_):
        raise AssertionError("connected to the mail server without a readable list")
    out = sm.send_drafts([draft()], S, really=True, out=lines.append, connect_fn=must_not_connect, sleep=lambda s: None,
                         log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", contacts=c)
    assert out["sent"] == 0 and out["failed"] == 1 and "nothing was sent" in "\n".join(lines)
    lines.clear()
    sm.send_drafts([draft()], S, really=False, out=lines.append, log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", contacts=c)
    assert any("a real send would be refused" in l for l in lines)


def test_errors_never_contain_the_key():
    c, rest = store()
    rest.fail_with = 500
    with pytest.raises(cs.StoreUnavailable) as e:
        c.status("a@x.com")
    assert KEY not in str(e.value) and "500" in str(e.value)


def test_open_store_never_falls_back_to_the_local_file():
    with pytest.raises(SystemExit):
        cd.open_store({"OUTREACH_STORE": "supabase"})
    assert isinstance(cd.open_store({"OUTREACH_STORE": "supabase", "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": KEY}), cs.SupabaseContacts)
    with pytest.raises(cs.StoreUnavailable):
        cs.SupabaseContacts("http://insecure.example.com", KEY)


def test_inbox_sync_works_on_the_shared_list():
    c, _ = store()
    c.add("stop@corp.com")
    c.advance("stop@corp.com", "sent")
    cd.sync_inbox(c, FakeImap(mail("stop@corp.com", "Please unsubscribe me.")), out=lambda *_: None)
    assert c.status("stop@corp.com") == "unsubscribed"
