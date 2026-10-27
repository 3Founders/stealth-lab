"""Offline tests for send_mail.py: a fake SMTP object stands in for the network; nothing here connects to anything."""
import smtplib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send_mail as sm  # noqa: E402

S = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "me", "SMTP_PASSWORD": "hunter2-secret", "SMTP_FROM": "me@example.com",
     "SMTP_FROM_NAME": "Me", "OUTREACH_POSTAL_ADDRESS": "1 Main St, Mumbai"}


def draft(**kw):
    d = {"to": "a@corp.com", "subject": "Your repo", "body": "Saw your post about AI coding. Worth a call?", "approved": True,
         "evidence_url": "https://corp.com/blog", "evidence_fact": "a post on AI coding costs",
         "approved_by": "founder", "template_version": "v1"}
    d.update(kw)
    return d


class Fake:
    def __init__(self, fail_login=False, refuse=()):
        self.sent, self.fail_login, self.refuse, self.quit_called = [], fail_login, set(refuse), False

    def send_message(self, msg):
        if msg["To"] in self.refuse:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no")})
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


def run(drafts, tmp_path, really=True, fake=None, **kw):
    fake = fake or Fake()
    lines = []
    c = sm.send_drafts(drafts, S, really=really, out=lines.append, connect_fn=lambda s: fake, sleep=lambda s: None,
                       log_path=tmp_path / "log.csv", suppress_path=tmp_path / "sup.txt", **kw)
    return c, fake, lines


def test_dry_run_never_connects(tmp_path):
    def boom(_):
        raise AssertionError("connected")
    c = sm.send_drafts([draft()], S, really=False, out=lambda *_: None, connect_fn=boom, log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt")
    assert c == {"to_send": 1, "sent": 0, "skipped": 0, "failed": 0}
    assert not (tmp_path / "l.csv").exists()


def test_sends_approved_with_footer_and_unsubscribe(tmp_path):
    c, fake, _ = run([draft()], tmp_path)
    assert c["sent"] == 1 and fake.quit_called
    m = fake.sent[0]
    body = m.get_content()
    assert "1 Main St, Mumbai" in body and "unsubscribe" in body.lower()
    assert m["List-Unsubscribe"].startswith("<mailto:me@example.com")
    assert (tmp_path / "log.csv").read_text().count("sent") >= 1


def test_unapproved_suppressed_and_duplicates_are_skipped(tmp_path):
    (tmp_path / "sup.txt").write_text("blocked@corp.com\n")
    ds = [draft(approved=False), draft(to="blocked@corp.com"), draft(to="ok@corp.com")]
    c, fake, lines = run(ds, tmp_path)
    assert c["sent"] == 1 and c["skipped"] == 2
    c2, fake2, _ = run([draft(to="ok@corp.com")], tmp_path)          # second run: already in the log
    assert c2["sent"] == 0 and c2["skipped"] == 1 and not fake2.sent


def test_rule_check_blocks_prices_percentages_soc2_and_missing_evidence():
    assert sm.rule_check(draft()) == []
    assert any("price" in w for w in sm.rule_check(draft(body="Only $800 per developer")))
    assert any("percentage" in w for w in sm.rule_check(draft(body="We cut cost by saving 40% on your bill")))
    assert any("SOC 2" in w for w in sm.rule_check(draft(body="We are SOC 2 compliant")))
    assert any("egress" in w for w in sm.rule_check(draft(body="zero-egress design")))
    assert any("evidence_url" in w for w in sm.rule_check(draft(evidence_url="")))
    assert any("Re:" in w for w in sm.rule_check(draft(subject="Re: our call")))
    assert any("address" in w for w in sm.rule_check(draft(to="not-an-address")))


def test_per_run_limit(tmp_path):
    ds = [draft(to=f"p{i}@corp.com") for i in range(5)]
    c, fake, _ = run(ds, tmp_path, limit=2)
    assert c["sent"] == 2 and c["skipped"] == 3


def test_refused_recipient_is_logged_and_the_rest_continue(tmp_path):
    ds = [draft(to="bad@corp.com"), draft(to="good@corp.com")]
    c, fake, _ = run(ds, tmp_path, fake=Fake(refuse={"bad@corp.com"}))
    assert c["sent"] == 1 and c["failed"] == 1
    assert "refused" in (tmp_path / "log.csv").read_text()


def test_login_failure_stops_and_hides_the_password(tmp_path):
    def bad(_):
        raise smtplib.SMTPAuthenticationError(535, b"bad login hunter2-secret")
    lines = []
    c = sm.send_drafts([draft()], S, really=True, out=lines.append, connect_fn=bad, log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt")
    assert c["sent"] == 0 and c["failed"] == 1
    assert "hunter2-secret" not in "\n".join(lines)


def test_settings_file_wins_and_check_never_prints_values(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "ENV_FILE", tmp_path / "absent.env")   # the real backend/.env must not change this test
    f = tmp_path / ".env"
    f.write_text("SMTP_HOST=from-file\nSMTP_PASSWORD='filepass'\n")
    s = sm.load_settings({"SMTP_HOST": "from-shell", "SMTP_USER": "u", "OTHER": "x"}, env_file=f)
    assert s["SMTP_HOST"] == "from-file" and s["SMTP_USER"] == "u" and "OTHER" not in s
    said = []
    sm.main(["check"], environ={"SMTP_PASSWORD": "topsecret"}, out=said.append)
    assert "topsecret" not in "\n".join(said)
    assert any("missing" in l for l in said)


def test_rule_check_requires_the_record_fields_and_forbids_tracking_and_many_links():
    for field in ("evidence_fact", "approved_by", "template_version"):
        assert any(field in w for w in sm.rule_check(draft(**{field: ""}))), field
    assert any("more than one link" in w for w in sm.rule_check(draft(body="See https://a.com and https://b.com")))
    assert any("plain text" in w for w in sm.rule_check(draft(body="<img src=x> hello")))
    assert any("plain text" in w for w in sm.rule_check(draft(body="see https://a.com/?utm_source=x")))
    assert sm.rule_check(draft(body="One link https://a.com is fine")) == []


def test_the_log_records_who_approved_and_which_wording(tmp_path):
    run_log = tmp_path / "log.csv"
    sm.send_drafts([draft()], S, really=True, out=lambda *_: None, connect_fn=lambda s: Fake(), sleep=lambda s: None,
                   log_path=run_log, suppress_path=tmp_path / "s.txt")
    text = run_log.read_text()
    assert "founder" in text and "v1" in text
