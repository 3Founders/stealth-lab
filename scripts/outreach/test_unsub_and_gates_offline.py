"""Offline tests: the unsubscribe token and headers, the country gate, the footer by country."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contacts_db as cd  # noqa: E402
import send_mail as sm  # noqa: E402
import unsub  # noqa: E402
from test_send_mail_offline import Fake, S, draft  # noqa: E402

SECRET = "unsub-secret-xyz"
URL = "https://abc.supabase.co/functions/v1/unsubscribe"
WITH_LINK = {**S, "UNSUBSCRIBE_URL": URL, "UNSUBSCRIBE_SECRET": SECRET}
TOKEN_MJS = Path(__file__).resolve().parent / "supabase_functions" / "unsubscribe" / "token.mjs"


def test_token_round_trip_and_tampering():
    t = unsub.sign(SECRET, "Ann@Corp.com")
    assert unsub.verify(SECRET, t) == "ann@corp.com"
    assert unsub.verify("another-secret", t) is None
    left, right = t.split(".")
    assert unsub.verify(SECRET, unsub.sign(SECRET, "bob@corp.com").split(".")[0] + "." + right) is None
    assert unsub.verify(SECRET, "garbage") is None and unsub.verify(SECRET, "") is None
    with pytest.raises(ValueError):
        unsub.link("http://insecure.example.com", SECRET, "a@b.com")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_edge_function_token_code_agrees_with_python():
    script = (
        f"import {{sign, verify}} from {TOKEN_MJS.as_uri()!r};"
        f"const t = process.argv[1];"
        f"console.log(await verify({SECRET!r}, t));"
        f"console.log(await sign({SECRET!r}, 'Bob@Corp.com'));"
        f"console.log(await verify('wrong', t));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script, unsub.sign(SECRET, "ann@corp.com")],
                         capture_output=True, text=True, timeout=30, check=True).stdout.split()
    assert out[0] == "ann@corp.com" and out[2] == "null"
    assert out[1] == unsub.sign(SECRET, "bob@corp.com") and unsub.verify(SECRET, out[1]) == "bob@corp.com"


def test_one_click_headers_and_footer_link_when_configured():
    msg = sm.build_message(draft(), WITH_LINK, "US")
    lh = msg["List-Unsubscribe"]
    assert lh.startswith("<" + URL + "?t=") and "mailto:" in lh
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    token = lh.split("?t=")[1].split(">")[0]
    assert unsub.verify(SECRET, token) == "a@corp.com"
    assert "Unsubscribe in one click: " + URL in msg.get_content()


def test_without_the_link_settings_only_the_mailto_header_is_used():
    msg = sm.build_message(draft(), S, "US")
    assert msg["List-Unsubscribe-Post"] is None and msg["List-Unsubscribe"].startswith("<mailto:")


def test_footer_by_country():
    us = sm.build_message(draft(), S, "US").get_content()
    uk = sm.build_message(draft(), S, "UK").get_content()
    assert "commercial message" in us and "Apollo" in us and "legitimate interests" not in us
    assert "legitimate interests" in uk and "object at any time" in uk and "https://corp.com/blog" in uk


def test_country_names_are_normalised():
    assert [sm.norm_country(x) for x in ("United States", "usa", "US", "England", "GB", "India", "in")] == ["US", "US", "US", "UK", "UK", "IN", "IN"]
    assert sm.norm_country("Germany") == "GERMANY" and sm.norm_country("") == ""


def run(tmp_path, drafts, settings=S, contacts=None):
    lines = []
    out = sm.send_drafts(drafts, settings, really=True, out=lines.append, connect_fn=lambda s: Fake(), sleep=lambda s: None,
                         log_path=tmp_path / "l.csv", suppress_path=tmp_path / "s.txt", contacts=contacts)
    return out, "\n".join(lines)


def test_countries_off_the_allow_list_or_unknown_are_not_sent(tmp_path):
    out, text = run(tmp_path, [draft(to="a@x.com", country="Germany"), draft(to="b@x.com", country=""), draft(to="c@x.com", country="United Kingdom")])
    assert out["sent"] == 1 and out["skipped"] == 2
    assert "country GERMANY is not on the allow-list" in text and "no country on record" in text


def test_the_country_comes_from_the_contact_when_the_draft_has_none(tmp_path):
    c = cd.Contacts(":memory:")
    c.add("a@x.com", country="India")
    c.add("b@x.com", country="France")
    out, _ = run(tmp_path, [draft(to="a@x.com", country=""), draft(to="b@x.com", country="")], contacts=c)
    assert out["sent"] == 1 and out["skipped"] == 1


def test_the_allow_list_can_be_widened_in_settings(tmp_path):
    out, _ = run(tmp_path, [draft(to="a@x.com", country="France")], settings={**S, "OUTREACH_COUNTRIES": "US,UK,IN,FR"})
    assert out["sent"] == 1
    assert sm.allowed_countries({**S, "OUTREACH_COUNTRIES": "US, france"}) == {"US", "FR"}
