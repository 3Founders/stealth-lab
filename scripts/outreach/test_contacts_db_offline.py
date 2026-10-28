"""Offline tests for contacts_db.py and its use by the sender. IMAP is a fake; nothing connects anywhere."""
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contacts_db as cd  # noqa: E402
import send_mail as sm  # noqa: E402
from test_send_mail_offline import Fake, S, draft  # noqa: E402


def mail(frm, body, subject="Re: Your repo"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, "me@example.com", subject
    m.set_content(body)
    return m.as_bytes()


class FakeImap:
    def __init__(self, *messages):
        self.messages = list(messages)

    def select(self, *a, **k):
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, num, _):
        return "OK", [(b"1 (RFC822)", self.messages[int(num) - 1])]


def test_final_statuses_cannot_be_reversed():
    c = cd.Contacts(":memory:")
    assert c.add("A@Corp.com", company="Corp")
    assert not c.add("a@corp.com")                      # known: not reset
    c.advance("a@corp.com", "sent")
    c.finalize("a@corp.com", "unsubscribed", "asked")
    c.advance("a@corp.com", "replied")                  # cannot move out of a final status
    assert c.status("a@corp.com") == "unsubscribed" and c.is_blocked("A@corp.com")
    c.finalize("a@corp.com", "bounced")                 # an unsubscribe is not downgraded
    assert c.status("a@corp.com") == "unsubscribed"


def test_status_never_moves_back_and_unknown_addresses_are_recorded_when_finalized():
    c = cd.Contacts(":memory:")
    c.add("b@corp.com")
    c.advance("b@corp.com", "sent")
    c.advance("b@corp.com", "approved")
    assert c.status("b@corp.com") == "sent"
    c.finalize("never-added@corp.com", "do_not_contact")
    assert not c.add("never-added@corp.com") and c.is_blocked("never-added@corp.com")


def test_import_csv(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("email,name,company,country,source,basis\nx@a.com,X,A,US,apollo,legit\nbad,Y,B,US,apollo,\nx@a.com,X,A,US,apollo,\n")
    c = cd.Contacts(":memory:")
    assert c.import_csv(f) == {"added": 1, "skipped": 2}


def test_sync_inbox_unsubscribe_bounce_reply_and_ignores_quoted_footer():
    c = cd.Contacts(":memory:")
    for e in ("stop@corp.com", "quoted@corp.com", "talk@corp.com", "gone@corp.com"):
        c.add(e)
        c.advance(e, "sent")
    quoted = "Sounds interesting, tell me more.\n\n> If you would rather not hear from me, reply unsubscribe\n"
    bounce = "Final-Recipient: rfc822; gone@corp.com" + chr(10) + "Status: 5.1.1" + chr(10)
    imap = FakeImap(mail("stop@corp.com", "Please unsubscribe me."), mail("quoted@corp.com", quoted),
                    mail("talk@corp.com", "Can we speak Tuesday?"), mail("MAILER-DAEMON@x", bounce, "Undelivered"),
                    mail("stranger@elsewhere.com", "unsubscribe"))
    counts = cd.sync_inbox(c, imap, out=lambda *_: None)
    assert c.status("stop@corp.com") == "unsubscribed"
    assert c.status("quoted@corp.com") == "replied"       # the word was only in the quoted footer
    assert c.status("talk@corp.com") == "replied"
    assert c.status("gone@corp.com") == "bounced"
    assert c.status("stranger@elsewhere.com") is None and counts["unsubscribed"] == 1


def test_sender_skips_blocked_and_records_sends(tmp_path):
    c = cd.Contacts(":memory:")
    c.finalize("no@corp.com", "unsubscribed")
    fake, lines = Fake(), []
    out = sm.send_drafts([draft(to="no@corp.com"), draft(to="yes@corp.com")], S, really=True, out=lines.append, connect_fn=lambda s: fake,
                         sleep=lambda s: None, log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", contacts=c)
    assert out["sent"] == 1 and out["skipped"] == 1
    assert c.status("yes@corp.com") == "sent" and c.status("no@corp.com") == "unsubscribed"


def test_sender_marks_refused_addresses_bounced(tmp_path):
    c = cd.Contacts(":memory:")
    sm.send_drafts([draft(to="bad@corp.com")], S, really=True, out=lambda *_: None, connect_fn=lambda s: Fake(refuse={"bad@corp.com"}),
                   sleep=lambda s: None, log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", contacts=c)
    assert c.status("bad@corp.com") == "bounced"


def test_show_prints_the_full_message_in_a_dry_run(tmp_path):
    lines = []
    sm.send_drafts([draft()], S, really=False, out=lines.append, log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", show=True)
    text = "\n".join(lines)
    assert "List-Unsubscribe" in text and "1 Main St, Mumbai" in text and "hunter2-secret" not in text


def test_out_of_office_replies_are_counted_not_treated_as_replies_or_unsubscribes():
    c = cd.Contacts(":memory:")
    for e in ("ooo@corp.com", "hdr@corp.com"):
        c.add(e)
        c.advance(e, "sent")
    hdr = EmailMessage()
    hdr["From"], hdr["To"], hdr["Subject"], hdr["Auto-Submitted"] = "hdr@corp.com", "me@example.com", "Hello", "auto-replied"
    hdr.set_content("I am away. To unsubscribe from our newsletter click here.")
    imap = FakeImap(mail("ooo@corp.com", "I am away until Monday.", "Out of office: Your note"), hdr.as_bytes())
    counts = cd.sync_inbox(c, imap, out=lambda *_: None)
    assert counts["auto_replies"] == 2 and counts["replied"] == 0 and counts["unsubscribed"] == 0
    assert c.status("ooo@corp.com") == "sent" and c.status("hdr@corp.com") == "sent"
    assert [e["kind"] for e in c.db.execute("SELECT kind FROM events WHERE kind='auto_reply'")] == ["auto_reply", "auto_reply"]


def _batch(c, n_sent, n_bounced):
    for i in range(n_sent):
        c.add(f"s{i}@corp.com")
        c.advance(f"s{i}@corp.com", "sent")
    for i in range(n_bounced):
        c.finalize(f"b{i}@corp.com", "bounced", "test")


def test_a_high_bounce_rate_pauses_sending_until_the_runbook_is_followed(tmp_path):
    c = cd.Contacts(":memory:")
    _batch(c, 18, 2)                                    # 2 of 20 = 10%, over the 2% limit with the minimum sample reached
    kw = dict(really=True, out=lambda *_: None, connect_fn=lambda s: Fake(), sleep=lambda s: None, log_path=tmp_path / "l.csv",
              suppress_path=tmp_path / "s.txt", contacts=c)
    lines = []
    r = sm.send_drafts([draft(to="new@corp.com")], S, **{**kw, "out": lines.append})
    assert r["sent"] == 0 and r["failed"] == 1 and any("paused" in l for l in lines)
    ok = sm.send_drafts([draft(to="new@corp.com")], S, bounce_reviewed=True, **kw)
    assert ok["sent"] == 1


def test_no_pause_when_bounces_are_within_the_limit_or_absent(tmp_path):
    c = cd.Contacts(":memory:")
    _batch(c, 60, 1)                                    # 1 of 61 = 1.6%, and one bounce is below the minimum of two
    r = sm.send_drafts([draft(to="new@corp.com")], S, really=True, out=lambda *_: None, connect_fn=lambda s: Fake(), sleep=lambda s: None,
                       log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", contacts=c)
    assert r["sent"] == 1
    assert sm.bounce_counts({}) == (0, 0)
