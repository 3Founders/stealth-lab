"""Offline coverage for app/services/repo_ingestion.py (plain repo ingester) and the S5
"references are never copied" rule in _preserve_script_artifact. No network, no DB.

The license stage is exercised here too, end to end through `ingest_repo`: a repo-level id
from `/license?ref=`, a per-subfolder license index built off the pinned tree, and the
ALLOW / QUARANTINE / REJECT split that decides what is ever fetched or described."""
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

SELECTED = frozenset(FILES) - {"node_modules/x/y.css", "README.md"}

MIT_TEXT = b"""MIT License

Copyright (c) 2024 Acme Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
"""

GPL3_TEXT = b"""                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.
"""

PROPRIETARY_TEXT = b"""Copyright (c) 2024 Acme Inc. All rights reserved.

This file and the directory containing it are confidential. No licence is
granted to copy, modify or redistribute it without written permission.
"""


def fake_github(license_spdx="MIT", *, files=None, license_blobs=None):
    """A commit-addressed GitHub. `license_spdx` is what /license reports for THIS commit;
    `license_blobs` adds LICENSE files to the tree (and to the raw-blob fetches)."""
    payload = dict(FILES if files is None else files)
    for path, text in (license_blobs or {}).items():
        payload[path] = text.encode() if isinstance(text, str) else text

    def get(url):
        if "/git/trees/" in url:
            tree = [{"type": "blob", "path": p, "size": len(b)} for p, b in payload.items()]
            return 200, json.dumps({"tree": tree}).encode()
        if "/commits/" in url:
            return 200, json.dumps({"sha": COMMIT}).encode()
        if url.split("?", 1)[0].endswith("/license"):
            if not license_spdx:
                return 404, b""
            return 200, json.dumps({"license": {"spdx_id": license_spdx}}).encode()
        path = url.split(f"/{COMMIT}/", 1)[-1]
        return (200, payload[path]) if path in payload else (404, b"")
    return get


def spy_github(*args, **kwargs):
    """fake_github plus the ordered list of URLs it was asked for."""
    seen: list[str] = []
    inner = fake_github(*args, **kwargs)

    def get(url):
        seen.append(url)
        return inner(url)

    get.seen = seen
    return get


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.seen_paths: list[str] = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        self.calls += 1
        path = kw["messages"][1]["content"].split("Path: ", 1)[1].split("\n", 1)[0]
        self.seen_paths.append(path)
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


def test_select_files_skips_symlink_blobs_without_disturbing_the_cap():
    tree = [{"type": "blob", "path": "styles/link.css", "size": 10, "mode": "120000"},
            {"type": "blob", "path": "styles/real.css", "size": 10, "mode": "100644"}]
    assert [p for p, _ in ri.select_files(tree)] == ["styles/real.css"]


def test_changed_paths_only_ever_agrees_that_something_moved():
    current = [{"type": "blob", "path": "a.css", "sha": "s1"}, {"type": "blob", "path": "b.css", "sha": "s2"}]
    base = [{"type": "blob", "path": "a.css", "sha": "s1"}, {"type": "blob", "path": "b.css", "sha": "s0"}]
    assert ri.changed_paths(current, base) == {"b.css"}
    assert ri.changed_paths(current, []) == {"a.css", "b.css"}
    assert ri.changed_paths(current, [{"type": "blob", "path": "a.css", "sha": "s1"},
                                       {"type": "blob", "path": "b.css"}]) == {"b.css"}
    assert ri.changed_paths(current, [{"type": "tree", "path": "a.css", "sha": "s1"},
                                      {"type": "blob", "path": "b.css", "sha": "s0"}]) == {"a.css", "b.css"}


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
    assert out["skipped"] == {"license_rejected": 4}
    assert out["eligible"] == 0 and out["selected"] == 4
    assert {d["decision"] for d in out["license_decisions"]} == {"REJECT"}
    assert all("GPL-3.0" in d["reason"] and "reject floor" in d["reason"]
               for d in out["license_decisions"])


def test_license_lookup_and_tree_are_both_pinned_to_the_ingested_commit(captured):
    gh = spy_github("MIT")
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=FakeClient(), model="m", http_get=gh))
    assert out["status"] == "captured"
    assert f"https://api.github.com/repos/acme/ui/license?ref={COMMIT}" in gh.seen
    assert f"https://api.github.com/repos/acme/ui/git/trees/{COMMIT}?recursive=1" in gh.seen
    assert not any(u.endswith("/license") for u in gh.seen), "an unpinned license id describes another commit"
    assert out["license"] == "MIT" and out["license_blocked"] == 0 and out["license_decisions"] == []


def test_mit_repo_licenses_every_selected_path_from_the_repository_id(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m", http_get=fake_github("MIT")))
    assert out["eligible"] == 4 and out["license_blocked"] == 0
    assert set(out["skipped"]) & {"license_rejected", "license_quarantined"} == set()
    assert client.calls == 4 and len(captured["procedures"]) == 4


def test_a_repo_with_no_license_anywhere_is_quarantined_before_any_llm_call(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m", http_get=fake_github(None)))
    assert out["license"] is None and out["status"] == "quarantined"
    assert out["skipped"] == {"license_quarantined": 4} and out["eligible"] == 0
    assert client.calls == 0 and captured["procedures"] == []
    assert {d["decision"] for d in out["license_decisions"]} == {"QUARANTINE"}
    assert all("no license id available" in d["reason"] for d in out["license_decisions"])


def test_unknown_repository_license_is_quarantined_not_assumed_permissive(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m",
                                     http_get=fake_github("LicenseRef-Proprietary")))
    assert out["status"] == "quarantined" and client.calls == 0
    assert out["skipped"] == {"license_quarantined": 4}
    (decision,) = out["license_decisions"][:1]
    assert decision["spdx_id"] == "LicenseRef-Proprietary"
    assert "not on the disclosed permissive allowlist" in decision["reason"]


def test_a_subfolder_license_overrides_the_repository_id_for_the_paths_it_governs(captured):
    files = dict(FILES)
    files["packages/ui/tailwind.config.js"] = b"module.exports = { theme: { extend: {} } };"
    gh = spy_github("MIT", files=files,
                    license_blobs={"LICENSE": MIT_TEXT, "packages/ui/LICENSE": GPL3_TEXT})
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m", http_get=gh))

    assert out["license"] == "MIT" and out["status"] == "captured"
    assert "packages/ui/tailwind.config.js" not in client.seen_paths
    assert {c["path"] for c in out["captured"]} == SELECTED
    assert out["skipped"]["license_rejected"] == 1 and out["eligible"] == 4
    (decision,) = out["license_decisions"]
    assert decision["path"] == "packages/ui/tailwind.config.js"
    assert decision["decision"] == "REJECT" and decision["spdx_id"] == "GPL-3.0-only"
    assert decision["source_path"] == "packages/ui/LICENSE"

    captured_paths = {kw["steps"][0]["source_locator"]["path"] for kw in captured["procedures"]}
    assert captured_paths == SELECTED


def test_unidentified_subfolder_license_quarantines_instead_of_inheriting_the_repo_id(captured):
    files = dict(FILES)
    files["packages/ui/tailwind.config.js"] = b"module.exports = { theme: { extend: {} } };"
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(
        None, "acme/ui", client=client, model="m",
        http_get=fake_github("MIT", files=files, license_blobs={"packages/ui/LICENSE": PROPRIETARY_TEXT})))

    assert out["status"] == "captured" and out["skipped"] == {"license_quarantined": 1}
    (decision,) = out["license_decisions"]
    assert decision["spdx_id"] is None and decision["source_path"] == "packages/ui/LICENSE"
    assert "no license id available from packages/ui/LICENSE" in decision["reason"]
    assert "packages/ui/tailwind.config.js" not in client.seen_paths
    assert len(captured["procedures"]) == 4


def test_an_unreadable_license_blob_quarantines_rather_than_falling_back(captured):
    files = dict(FILES)
    files["packages/ui/tailwind.config.js"] = b"module.exports = { theme: { extend: {} } };"
    inner = fake_github("MIT", files=files, license_blobs={"packages/ui/LICENSE": PROPRIETARY_TEXT})

    def get(url):
        if url.endswith("/packages/ui/LICENSE"):
            return 503, b""
        return inner(url)

    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=FakeClient(), model="m", http_get=get))
    assert out["skipped"] == {"license_quarantined": 1}
    (decision,) = out["license_decisions"]
    assert decision["spdx_id"] is None and decision["source_path"] == "packages/ui/LICENSE"
    assert "no license id available from packages/ui/LICENSE" in decision["reason"]


def test_a_root_license_text_overrides_a_repository_id_that_disagrees_with_it(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(
        None, "acme/ui", client=client, model="m",
        http_get=fake_github("MIT", license_blobs={"LICENSE": GPL3_TEXT})))
    assert out["license"] == "MIT" and out["status"] == "rejected"
    assert client.calls == 0 and captured["procedures"] == []
    assert out["skipped"] == {"license_rejected": 4}
    assert {d["source_path"] for d in out["license_decisions"]} == {"LICENSE"}
    assert {d["spdx_id"] for d in out["license_decisions"]} == {"GPL-3.0-only"}


def test_an_unidentified_root_license_falls_back_to_the_detectors_own_reading_of_that_file(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(
        None, "acme/ui", client=client, model="m",
        http_get=fake_github("MIT", license_blobs={"LICENSE": PROPRIETARY_TEXT})))
    assert out["status"] == "captured" and client.calls == 4
    assert out["license_blocked"] == 0


def test_license_allow_extension_un_quarantines_a_license_the_default_policy_refuses(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m",
                                     http_get=fake_github("MPL-2.0"), license_allow={"MPL-2.0"}))
    assert out["status"] == "captured" and client.calls == 4
    assert out["license_blocked"] == 0


def test_a_caller_allowlist_cannot_buy_a_copyleft_license(captured):
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(None, "acme/ui", client=client, model="m",
                                     http_get=fake_github("GPL-3.0-only"), license_allow={"GPL-3.0-only"}))
    assert out["status"] == "rejected" and client.calls == 0
    assert "configuration cannot allow" in out["license_decisions"][0]["reason"]


def test_non_allow_license_verdicts_are_audited_through_the_screening_run_path(captured, monkeypatch):
    from app.services import screening

    rows: list[dict] = []

    async def fake_record(pool, **kw):
        rows.append(kw)
        return {"decision": screening.decide(kw["findings"]), "decision_ids": [f"dec-{len(rows)}"]}

    monkeypatch.setattr(screening, "record_screening_run", fake_record)
    files = dict(FILES)
    files["packages/ui/tailwind.config.js"] = b"module.exports = { theme: { extend: {} } };"
    out = asyncio.run(ri.ingest_repo(
        object(), "acme/ui", client=FakeClient(), model="m",
        http_get=fake_github("MIT", files=files,
                             license_blobs={"packages/ui/LICENSE": GPL3_TEXT, "LICENSE": MIT_TEXT})))

    assert len(rows) == 1
    (row,) = rows
    (finding,) = row["findings"]
    assert finding["check_type"] == "license" and finding["severity"] == "block"
    assert screening.decide(row["findings"]) == "REJECT"
    assert any(s.startswith("repo_license:GPL-3.0-only") for s in finding["signals"])
    assert any(s == "repo_license_source:packages/ui/LICENSE" for s in finding["signals"])
    assert finding["reason"] == out["license_decisions"][0]["reason"]
    assert row["artifact_uri"] == f"https://github.com/acme/ui/blob/{COMMIT}/packages/ui/tailwind.config.js"
    assert row["detector_version"] == out["license_decisions"][0]["allowlist_version"]
    assert row["created_by"] == "repo_ingestion" and row["detector"]


def test_a_quarantine_finding_is_a_flag_so_screening_folds_it_to_quarantine(captured, monkeypatch):
    from app.services import screening

    rows: list[dict] = []

    async def fake_record(pool, **kw):
        rows.append(kw)
        return {"decision": screening.decide(kw["findings"]), "decision_ids": ["dec-1"]}

    monkeypatch.setattr(screening, "record_screening_run", fake_record)
    out = asyncio.run(ri.ingest_repo(object(), "acme/ui", client=FakeClient(), model="m",
                                     http_get=fake_github("MPL-2.0")))
    assert len(rows) == 4
    assert all(r["findings"][0]["severity"] == "flag" for r in rows)
    assert all(screening.decide(r["findings"]) == "QUARANTINE" for r in rows)
    assert all(f["check_type"] in screening.CHECK_TYPES for r in rows for f in r["findings"])
    assert out["status"] == "quarantined" and len(out["license_decisions"]) == 4


def test_a_failed_audit_never_costs_a_verdict_or_a_capture(captured, monkeypatch):
    from app.services import screening

    async def exploding_record(pool, **kw):
        raise RuntimeError("screening_decisions is unavailable")

    monkeypatch.setattr(screening, "record_screening_run", exploding_record)
    files = dict(FILES)
    files["packages/ui/tailwind.config.js"] = b"module.exports = { theme: { extend: {} } };"
    client = FakeClient()
    out = asyncio.run(ri.ingest_repo(
        object(), "acme/ui", client=client, model="m",
        http_get=fake_github("MIT", files=files, license_blobs={"packages/ui/LICENSE": GPL3_TEXT})))

    assert out["status"] == "captured" and out["skipped"] == {"license_rejected": 1}
    assert len(out["captured"]) == 4 and len(captured["procedures"]) == 4
    assert [d["decision"] for d in out["license_decisions"]] == ["REJECT"]


def test_no_pool_means_no_audit_row_but_the_skip_still_counts(captured):
    files = dict(FILES)
    files["packages/ui/tailwind.config.js"] = b"module.exports = { theme: { extend: {} } };"
    out = asyncio.run(ri.ingest_repo(
        None, "acme/ui", client=FakeClient(), model="m",
        http_get=fake_github("MPL-2.0", files=files)))
    assert out["skipped"] == {"license_quarantined": 5}
    assert out["license_decisions"] and all(d["spdx_id"] == "MPL-2.0" for d in out["license_decisions"])


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
