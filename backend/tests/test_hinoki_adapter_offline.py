"""The Hinoki adapter (app/tool_providers/hinoki, docs/goal_router.md P1-09) against a fake Hinoki and a fake GitHub:
no request leaves the process. Scan -> findings with ids -> explain -> a patch that is returned, never applied; the
refusals; and the gateway's rule that an R3 adapter runs only while the gate enforces."""
from __future__ import annotations

import json

import httpx
import pytest

from app import tool_providers
from app.tool_providers.hinoki import client as hinoki

FINDINGS = {"findings": [{"file": "app/db.py", "line": 3, "severity": "high", "title": "SQL injection",
                          "detail": "the name is formatted into the query", "cwe": "CWE-89"},
                         {"file": "", "line": None, "severity": "bogus", "title": ""}]}
PATCH = "```diff\n--- a/app/db.py\n+++ b/app/db.py\n@@ -3 +3 @@\n-    q = f\"... {name}\"\n+    q = \"... $1\"\n```\nUse a bound parameter."


class Fake:
    def __init__(self):
        self.sent: list[dict] = []
        self.hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        url = str(request.url)
        if url == "https://api.github.com/repos/acme/shop":
            return httpx.Response(200, json={"default_branch": "main"})
        if url.startswith("https://api.github.com/repos/acme/shop/git/trees/main"):
            return httpx.Response(200, json={"tree": [
                {"type": "blob", "path": "app/db.py", "size": 80},
                {"type": "blob", "path": "README.md", "size": 10},
                {"type": "blob", "path": "node_modules/x/index.js", "size": 10},
                {"type": "blob", "path": "web/app.min.js", "size": 10}]})
        if url == "https://raw.githubusercontent.com/acme/shop/main/app/db.py":
            return httpx.Response(200, text="def find(name):\n    db = connect()\n    q = f\"... {name}\"\n")
        if url == "https://api.github.com/repos/acme/private":
            return httpx.Response(404)
        if url.endswith("/chat/completions"):
            body = json.loads(request.content)
            self.sent.append(body)
            assert request.headers["authorization"] == "Bearer test-key"
            system = body["messages"][0]["content"]
            answer = PATCH if "fix one" in system else ("It is bad because ..." if "explain" in system
                                                        else json.dumps(FINDINGS))
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
        return httpx.Response(500)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("HINOKI_API_KEY", "test-key")
    hinoki._FINDINGS.clear()
    f = Fake()
    return f, httpx.AsyncClient(transport=httpx.MockTransport(f))


def test_the_adapter_is_registered_as_its_own_r3_server():
    a = tool_providers.get("hinoki")
    assert a is not None and a.RISK == "R3" and a.ADAPTER_ONLY
    assert {t["name"] for t in a.list_tools()} == {"scan_repository", "analyze_diff", "explain_finding", "propose_fix"}
    scan = next(t for t in a.list_tools() if t["name"] == "scan_repository")
    assert scan["inputSchema"]["required"] == ["repo_url"]


@pytest.mark.asyncio
async def test_scan_explain_and_fix(fake):
    f, client = fake
    out = await hinoki.call("scan_repository", {"repo_url": "https://github.com/acme/shop"}, client=client)
    assert out["ok"] and out["untrusted"]
    body = json.loads(out["text"])
    assert body["files_reviewed"] == ["app/db.py"]                 # docs, vendored and minified files are skipped
    assert [x["title"] for x in body["findings"]] == ["SQL injection"]   # an untitled finding is dropped
    finding = body["findings"][0]
    assert finding["id"].startswith("hf_") and finding["severity"] == "high"
    assert "    3     q = f" in f.sent[0]["messages"][1]["content"]     # the model saw numbered lines
    assert set(f.hosts) == {"api.github.com", "raw.githubusercontent.com", "inference.hinoki.security"}

    explained = json.loads((await hinoki.call("explain_finding", {"finding_id": finding["id"]}, client=client))["text"])
    assert explained["explanation"].startswith("It is bad")
    fixed = json.loads((await hinoki.call("propose_fix", {"finding_id": finding["id"]}, client=client))["text"])
    assert fixed["applied"] is False and fixed["patch"].startswith("--- a/app/db.py") and "+++ b/app/db.py" in fixed["patch"]


@pytest.mark.asyncio
async def test_analyze_diff(fake):
    f, client = fake
    out = await hinoki.call("analyze_diff", {"diff": "--- a/app/db.py\n+++ b/app/db.py\n+    q = f'{name}'"},
                            client=client)
    assert out["ok"] and json.loads(out["text"])["findings"][0]["cwe"] == "CWE-89"
    assert f.hosts == ["inference.hinoki.security"]                  # a diff is never fetched from anywhere


@pytest.mark.asyncio
async def test_refusals(fake, monkeypatch):
    f, client = fake
    for args, why in [({"repo_url": "https://gitlab.com/a/b"}, "github.com"),
                      ({"repo_url": "https://github.com/acme/private"}, "not a public"),
                      ({"repo_url": "file:///etc/passwd"}, "github.com")]:
        out = await hinoki.call("scan_repository", args, client=client)
        assert out["status"] == "failed:args" and why in out["text"]
    assert (await hinoki.call("explain_finding", {"finding_id": "hf_nope"}, client=client))["status"] == "failed:args"
    assert (await hinoki.call("analyze_diff", {"diff": " "}, client=client))["status"] == "failed:args"
    assert (await hinoki.call("exploit", {}, client=client))["status"] == "failed:no_tool"
    assert f.sent == []                                              # nothing reached the model
    monkeypatch.setenv("HINOKI_API_KEY", "")
    assert (await hinoki.call("scan_repository", {}, client=client))["status"] == "failed:not_configured"


def test_parse_findings_survives_prose_and_junk():
    assert hinoki.parse_findings("no json here", source="s") == []
    got = hinoki.parse_findings('Sure! {"findings": [{"title": "X", "line": "7"}]} done', source="s")
    assert got[0]["line"] is None and got[0]["severity"] == "medium"


def test_an_r3_adapter_runs_only_while_the_gate_enforces(monkeypatch):
    a = tool_providers.get("hinoki")
    monkeypatch.setenv("HINOKI_API_KEY", "test-key")
    for mode in ("off", "shadow"):
        monkeypatch.setenv("TOOL_GATE", mode)
        assert "enforcing" in (tool_providers.refusal(a) or "")
    monkeypatch.setenv("TOOL_GATE", "enforce")
    assert tool_providers.refusal(a) is None
    monkeypatch.setenv("HINOKI_API_KEY", "")
    assert "not configured" in tool_providers.refusal(a)
    assert tool_providers.adapter_only("hinoki") is a and tool_providers.adapter_only("ai.exa/exa") is None   # Exa keeps its MCP card
