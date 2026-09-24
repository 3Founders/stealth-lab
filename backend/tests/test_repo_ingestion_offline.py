"""Offline coverage for app/services/repo_ingestion.py (plain repo ingester) and the S5
"references are never copied" rule in _preserve_script_artifact. No network, no DB."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services import repo_ingestion as ri

COMMIT = "c" * 40
FILES = {
    "src/components/Button.tsx": b"export const Button = () => <button className='glossy'/>;",
    "styles/glossy.css": b".glossy { background: linear-gradient(#fff,#ddd); box-shadow: 0 2px 6px #0003; }",
    ".github/workflows/test.yml": b"jobs:\n  test:\n    runs-on: ubuntu-latest\n",
    "scripts/bootstrap.sh": b"#!/bin/sh\npython -m venv .venv\n",
    "node_modules/x/y.css": b".ignored{}",
    "README.md": b"# readme",
}


def fake_github(license_spdx="MIT"):
    def get(url):
        if url.endswith("/license"):
            return (200, json.dumps({"license": {"spdx_id": license_spdx}}).encode()) if license_spdx else (404, b"")
        if "/git/trees/" in url:
            tree = [{"type": "blob", "path": p, "size": len(b)} for p, b in FILES.items()]
            return 200, json.dumps({"tree": tree}).encode()
        if "/commits/" in url:
            return 200, json.dumps({"sha": COMMIT}).encode()
        path = url.split(f"/{COMMIT}/", 1)[-1]
        return (200, FILES[path]) if path in FILES else (404, b"")
    return get


class FakeClient:
    def __init__(self):
        self.calls = 0

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        self.calls += 1
        path = kw["messages"][1]["content"].split("Path: ", 1)[1].split("\n", 1)[0]
        body = {"goal": f"Reuse the pattern demonstrated in {path}", "description": f"Concrete contents of {path}."}
        msg = type("M", (), {"content": json.dumps(body)})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


@pytest.fixture
def captured(monkeypatch):
    calls = {"procedures": [], "artifacts": []}

    async def fake_capture(pool, **kw):
        calls["procedures"].append(kw)
        return {"id": "row", "procedure_id": f"proc-{len(calls['procedures'])}"}

    async def fake_preserve(pool, artifact, resource, raw_url, **kw):
        calls["artifacts"].append({"url": raw_url, **kw})
        return f"art-{len(calls['artifacts'])}"

    monkeypatch.setattr("app.services.procedures.capture_procedure", fake_capture)
    monkeypatch.setattr("app.services.skill_ingestion._preserve_script_artifact", fake_preserve)
    return calls


def test_classify_picks_domains_and_skips_vendored():
    assert ri.classify("styles/glossy.css").name == "ui_design"
    assert ri.classify(".github/workflows/test.yml").name == "ci_cd"
    assert ri.classify("scripts/bootstrap.sh").executes is True
    assert ri.classify("infra/main.tf").name == "infra"
    assert ri.classify("node_modules/x/y.css") is None
    assert ri.classify("README.md") is None


def test_select_files_caps_each_domain():
    tree = [{"type": "blob", "path": f"styles/s{i}.css", "size": 10} for i in range(20)]
    assert len(ri.select_files(tree, per_domain=3)) == 3
    assert ri.select_files(tree, per_domain=3, only=["ci_cd"]) == []


def test_parse_description_abstain_and_bounds():
    assert ri._parse_description('{"abstain": true}') is None
    assert ri._parse_description('{"goal": "x", "description": "y"}') is None       # too short
    ok = ri._parse_description('```json\n{"goal": "Style a glossy button", "description": "Gradient and shadow."}\n```')
    assert ok == {"goal": "Style a glossy button", "description": "Gradient and shadow."}


def test_ingest_repo_captures_one_step_procedures_pointing_at_pinned_urls(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m", http_get=fake_github()))
    assert out["status"] == "captured" and out["commit"] == COMMIT
    assert {c["domain"] for c in out["captured"]} == {"ui_design", "ci_cd", "scripts"}
    assert client.calls == 4                           # README + node_modules never reach the model
    for kw in captured["procedures"]:
        (step,) = kw["steps"]
        assert step["source_locator"]["uri"].startswith(f"https://raw.githubusercontent.com/acme/ui/{COMMIT}/")
        assert step["description"] and kw["display_description"] == step["description"]
        assert ":step" not in kw["name"]
    by_path = {kw["steps"][0]["source_locator"]["path"]: kw["steps"][0] for kw in captured["procedures"]}
    assert by_path["scripts/bootstrap.sh"]["binding"]["runtime"] == "shell"
    assert "binding" not in by_path["styles/glossy.css"]   # a reference is retrievable context, never run
    roles = {a["url"].rsplit("/", 1)[-1]: a["role"] for a in captured["artifacts"]}
    assert roles["glossy.css"] == "style_reference" and roles["bootstrap.sh"] == "executable_source"
    assert all(a["source_type"] == "repo_file" for a in captured["artifacts"])


def test_ingest_repo_rejects_copyleft_repos_before_any_llm_call(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m", http_get=fake_github("GPL-3.0")))
    assert out["status"] == "rejected" and client.calls == 0 and captured["procedures"] == []


def test_reference_files_are_not_copied_but_executables_are(monkeypatch):
    """S5: only executables keep a hashed byte copy (step_binding needs it to run)."""
    from types import SimpleNamespace

    from app.services import skill_ingestion

    stored = []

    async def fake_store_blob(pool, store, content, content_type):
        stored.append(content)
        return {"key": "k"}

    class Pool:
        async def fetchrow(self, sql, *a):
            return {"id": "art"}

    monkeypatch.setattr("app.services.object_storage.get_store", lambda: object())
    monkeypatch.setattr("app.services.object_storage.store_blob", fake_store_blob)
    res = SimpleNamespace(path="a.css", content=b"x", sha256="0" * 64, size=1)
    art = SimpleNamespace(repository="r/r", commit=COMMIT)
    asyncio.run(skill_ingestion._preserve_script_artifact(Pool(), art, res, "u", created_by="t", role="style_reference"))
    assert stored == []
    asyncio.run(skill_ingestion._preserve_script_artifact(Pool(), art, res, "u", created_by="t", role="executable_source"))
    assert stored == [b"x"]
