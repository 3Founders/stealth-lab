"""Offline tests: draft making/approving, the evidence page reader, and the DNS check. Nothing here touches the network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_check as ac  # noqa: E402
import contacts_db as cd  # noqa: E402
import drafts as dr  # noqa: E402
import evidence as ev  # noqa: E402
import send_mail as sm  # noqa: E402
from test_send_mail_offline import S  # noqa: E402

FACTS = {"ann@corp.com": {"email": "ann@corp.com", "evidence_url": "https://corp.com/blog/agents",
                          "evidence_fact": "Our engineers now review more agent-written code than they write."}}


def store():
    c = cd.Contacts(":memory:")
    c.add("ann@corp.com", name="Ann Lee", company="Corp", country="United States")
    c.add("bob@corp.com", name="Bob Ray", company="Corp", country="India")          # no facts row
    c.add("cy@corp.com", name="", company="Corp", country="India")                  # no name
    return c


def test_make_drafts_fills_the_template_and_skips_what_is_missing():
    lines = []
    made = dr.make_drafts(store(), dr.DEFAULT_TEMPLATE, {**FACTS, "cy@corp.com": {**FACTS["ann@corp.com"], "email": "cy@corp.com"}}, "Chaitanya Rao", set(), lines.append)
    assert [d["to"] for d in made] == ["ann@corp.com"]
    d = made[0]
    assert d["approved"] is False and d["template_version"] == "v1" and d["country"] == "United States"
    assert d["subject"] == "Quick question about Corp" and d["body"].startswith("Hi Ann,")
    assert FACTS["ann@corp.com"]["evidence_fact"] in d["body"] and d["body"].rstrip().endswith("Chaitanya")
    text = "\n".join(lines)
    assert "skip bob@corp.com: no facts row" in text and "skip cy@corp.com: no name" in text


def test_a_made_draft_passes_the_rule_check_once_approved():
    d = dr.make_drafts(store(), dr.DEFAULT_TEMPLATE, FACTS, "Chaitanya", set())[0]
    assert sm.rule_check({**d, "approved": True, "approved_by": "Chaitanya"}) == []
    assert any("approved_by" in w for w in sm.rule_check(d))


def test_a_template_that_breaks_the_rules_is_reported_not_sent():
    bad = dr.DEFAULT_TEMPLATE.replace("I am not selling anything yet.", "We cut costs by 50% for teams like yours.")
    lines = []
    assert dr.make_drafts(store(), bad, FACTS, "C", set(), lines.append) == []
    assert any("percentage" in l for l in lines)


def test_existing_drafts_are_not_made_again_and_a_facts_subject_wins():
    assert dr.make_drafts(store(), dr.DEFAULT_TEMPLATE, FACTS, "C", {"ann@corp.com"}) == []
    f = {"ann@corp.com": {**FACTS["ann@corp.com"], "subject": "Your agents post"}}
    assert dr.make_drafts(store(), dr.DEFAULT_TEMPLATE, f, "C", set())[0]["subject"] == "Your agents post"


def test_template_parsing_requires_version_subject_and_body():
    assert dr.parse_template(dr.DEFAULT_TEMPLATE)[0] == "v1"
    for bad in ("subject: x\n---\nbody", "version: v1\n---\nbody", "version: v1\nsubject: x\n---\n"):
        try:
            dr.parse_template(bad)
            raise AssertionError("accepted a bad template")
        except ValueError:
            pass


def test_approve_marks_only_the_named_drafts_and_advances_the_contact(tmp_path):
    c = store()
    ds = dr.make_drafts(c, dr.DEFAULT_TEMPLATE, {**FACTS, "bob@corp.com": {**FACTS["ann@corp.com"], "email": "bob@corp.com"}}, "C", set())
    assert len(ds) == 2
    assert dr.approve(ds, "Chaitanya", {"ann@corp.com"}, c) == 1
    assert [d["approved"] for d in ds] == [True, False] and ds[0]["approved_by"] == "Chaitanya"
    assert c.status("ann@corp.com") == "approved" and c.status("bob@corp.com") == "new"
    assert dr.approve(ds, "Chaitanya", {"ann@corp.com"}, c) == 0           # already approved: not counted again
    path = tmp_path / "d.jsonl"
    dr.write_drafts(ds, path)
    assert dr.read_drafts(path) == ds


HTML = ("<html><nav>Home About Pricing and other navigation words that are long enough to be a sentence maybe.</nav><body>"
        "<p>Our engineers now review more agent-written code than they write. That changed our hiring.</p>"
        "<p>Short.</p><script>var agent = 'a very long script string that mentions agent and is more than forty characters.';</script>"
        "<p>We spent a lot of time on cost controls for coding agents last quarter, and it was worth it.</p></body></html>")


def test_evidence_finds_sentences_with_the_words_and_ignores_scripts_and_nav():
    found = ev.sentences_with(HTML, ["agent"])
    assert found == ["Our engineers now review more agent-written code than they write.",
                     "We spent a lot of time on cost controls for coding agents last quarter, and it was worth it."]


def test_evidence_respects_robots_and_never_fetches_when_disallowed():
    out = []
    def must_not_fetch(_):
        raise AssertionError("fetched a disallowed page")
    assert ev.main(["find", "https://corp.com/x", "--word", "agent"], out=out.append, fetch=must_not_fetch, robots=lambda u: False) == 3
    assert ev.main(["find", "https://corp.com/x", "--word", "agent"], out=out.append, fetch=lambda u: HTML, robots=lambda u: True) == 0
    assert any("[1] Our engineers" in l for l in out) and ev.main(["find", "ftp://x", "--word", "a"], out=out.append) == 2
    assert ev.allowed_by_robots("https://corp.com/private/page", fetch=lambda u: "User-agent: *\nDisallow: /private/") is False
    assert ev.allowed_by_robots("https://corp.com/open", fetch=lambda u: "User-agent: *\nDisallow: /private/") is True


def zone(**records):
    return lambda name, kind: records.get(f"{name}|{kind}", [])


GOOD = {"corp.com|MX": ["10 mx.corp.com."], "corp.com|TXT": ["v=spf1 include:_spf.example.com -all", "other"],
        "_dmarc.corp.com|TXT": ["v=DMARC1; p=none"], "sel._domainkey.corp.com|CNAME": ["sel.dkim.example."]}


def test_dns_check_passes_a_complete_domain_and_reports_each_problem():
    assert ac.check_domain("corp.com", "sel", zone(**GOOD))["ok"]
    r = ac.check_domain("corp.com", "", zone(**GOOD))
    assert r["ok"] and any("DKIM not checked" in n for n in r["notes"])
    assert not ac.check_domain("corp.com", "sel", zone())["ok"] and len(ac.check_domain("corp.com", "sel", zone())["problems"]) == 4
    two = {**GOOD, "corp.com|TXT": ["v=spf1 a -all", "v=spf1 mx -all"]}
    assert any("2 SPF records" in p for p in ac.check_domain("corp.com", "sel", zone(**two))["problems"])
    no_dkim = {k: v for k, v in GOOD.items() if "domainkey" not in k}
    assert any("no DKIM record at sel._domainkey.corp.com" in p for p in ac.check_domain("corp.com", "sel", zone(**no_dkim))["problems"])


def test_run_auth_check_uses_the_sender_domain_and_returns_the_exit_code():
    out = []
    assert sm.run_auth_check({**S, "SMTP_FROM": "me@corp.com"}, out.append, lookup=zone(**GOOD)) == 0
    assert sm.run_auth_check({**S, "SMTP_FROM": "me@corp.com"}, out.append, lookup=zone()) == 1
    assert sm.run_auth_check({}, out.append) == 1
