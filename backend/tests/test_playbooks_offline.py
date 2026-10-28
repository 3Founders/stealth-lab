"""Research playbooks: shape, rendering, and that they are served as MCP resources."""
import asyncio

from app.mcp_server import playbooks as pb
from app.mcp_server import resources


def test_ten_playbooks_each_with_steps_and_a_check():
    assert len(pb.PLAYBOOKS) == 10 and len(pb.BY_SLUG) == 10
    for p in pb.PLAYBOOKS:
        assert 5 <= len(p.steps) <= 8 and p.check and p.goal and p.use_when


def test_a_playbook_renders_its_steps_check_and_the_untrusted_data_rule():
    text = asyncio.run(pb.playbook_resource("market-sizing"))
    assert "## Steps" in text and "1. " in text and "Check before you report" in text
    assert "untrusted data" in text and "no recorded runs" in text
    assert "No playbook named" in asyncio.run(pb.playbook_resource("nope"))


def test_the_index_lists_every_playbook():
    idx = asyncio.run(pb.playbooks_index_resource())
    assert all(f"stealth://playbooks/{p.slug}" in idx for p in pb.PLAYBOOKS)


def test_playbooks_are_registered_as_resources():
    uris = [r[0] for r in resources._V1_RESOURCES]
    assert "stealth://playbooks" in uris and "stealth://playbooks/{slug}" in uris


def test_no_playbook_claims_a_result_it_has_not_got():
    blob = " ".join(pb.render(p) for p in pb.PLAYBOOKS).lower()
    for banned in ("proven", "guaranteed", "saves ", "% cheaper"):
        assert banned not in blob
