"""Batched-description coverage for app/services/repo_ingestion.py and the batched artifact
upsert in app/services/skill_ingestion.py. No network, no DB.

This file deliberately does NOT patch `_preserve_script_artifact` / `preserve_repo_file_artifacts`
away: the point is to count the statements the real writer emits, and to count how many times an
executable's bytes are actually put into object storage. `FakePool` therefore serves all three
things the pipeline touches -- the described-content probe, the multi-row artifact INSERT, and the
artifact -> procedure link -- and remembers enough state that a second identical run behaves the
way the real partial unique index makes it behave.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

import pytest

from app.services import repo_ingestion as ri

REPO = "acme/ui"
COMMIT = "d" * 40
_BATCH_ITEM_RE = re.compile(r"^id=(\d+)\|path=(.*)\|domain=([a-z_]+)$")
_ARTIFACT_ROW_WIDTH = 15

CONFIGS = {
    ".prettierrc.json": b'{"printWidth": 100, "semi": true, "singleQuote": true}',
    "ruff.toml": b"line-length = 100\n[lint]\nselect = [\"E\", \"F\"]\nignore = [\"E501\"]\n",
}
STYLES = {f"styles/s{i}.css": f".s{i}{{color:#{i:06x};border-radius:{i}px}}".encode() for i in range(10)}
SCRIPTS = {"scripts/boot.sh": b"#!/bin/sh\npython -m venv .venv\n",
           "scripts/seed.py": b"print('seed')\n"}


def tree_for(files: dict[str, bytes]) -> list[dict]:
    return [{"type": "blob", "path": p, "size": len(b),
             "sha": hashlib.sha256(b).hexdigest(), "mode": "100644"} for p, b in files.items()]


def github(files: dict[str, bytes]) -> object:
    def get(url: str) -> tuple[int, bytes]:
        if "/git/trees/" in url:
            return 200, json.dumps({"tree": tree_for(files)}).encode()
        if url.split("?", 1)[0].endswith("/license"):
            return 200, json.dumps({"license": {"spdx_id": "MIT"}}).encode()
        path = url.split(f"/{COMMIT}/", 1)[-1]
        return (200, files[path]) if path in files else (404, b"")
    return get


class FakePool:
    """The three statements the pipeline issues, with the real uniqueness semantics.

    `described` is the set of content hashes that produced a linked capture -- exactly what
    DESCRIBED_PROBE_SQL asks for. `artifacts` is keyed by the partial unique identity
    (path, content_hash), so re-preserving the same blob returns the row that is already there
    rather than a second one, which is what makes a rerun assertion meaningful."""

    def __init__(self) -> None:
        self.described: set[str] = set()
        self.artifacts: dict[tuple[str, str], str] = {}
        self.inserts: list[tuple[str, tuple]] = []
        self.links: list[tuple] = []
        self.rows: list[dict] = []
        self.insert_error: Exception | None = None

    async def fetch(self, sql: str, *args):
        if sql.lstrip().upper().startswith("SELECT"):
            return [{"content_hash": h} for h in args[0] if h in self.described]
        if self.insert_error is not None:
            raise self.insert_error
        self.inserts.append((sql, args))
        rows = []
        for base in range(0, len(args), _ARTIFACT_ROW_WIDTH):
            path, content_hash = args[base + 3], args[base + 5]
            identity = (str(path), str(content_hash))
            artifact_id = self.artifacts.setdefault(identity, f"art-{len(self.artifacts) + 1}")
            rows.append({"id": artifact_id, "path": path})
            self.rows.append({"path": path, "content_hash": content_hash, "role": args[base + 6],
                              "status": args[base + 11], "stored": args[base + 10]})
        return rows

    async def execute(self, sql: str, *args) -> str:
        self.links.append(args)
        for identity, artifact_id in self.artifacts.items():
            if artifact_id == str(args[0]):
                self.described.add(identity[1])
        return "UPDATE 1"

    @property
    def preserved(self) -> list[dict]:
        return self.rows


class FakeClient:
    """Answers whatever shape it was asked. `script` may rewrite the reply for one id, and
    `calls` records both the count of provider calls and the per-call file list."""

    def __init__(self, script=None) -> None:
        self.calls = 0
        self.requested: list[list[str]] = []
        self._script = script or (lambda path, item_id: None)

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        self.calls += 1
        user = kw["messages"][1]["content"]
        batched = [(m.group(1), m.group(2)) for line in user.splitlines() if (m := _BATCH_ITEM_RE.match(line))]
        if batched:
            self.requested.append([p for _i, p in batched])
            body = {"items": [self._item(i, p) for i, p in batched]}
        else:
            path = user.split("Path: ", 1)[1].split("\n", 1)[0]
            self.requested.append([path])
            body = self._item("1", path)
        msg = type("M", (), {"content": json.dumps(body)})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    def _item(self, item_id: str, path: str) -> dict:
        override = self._script(path, item_id)
        if override is not None:
            return override
        return {"id": item_id, "goal": f"Reuse the concrete pattern demonstrated in {path}",
                "description": f"{path} concretely names the reusable values a reader needs."}


@pytest.fixture
def pool(monkeypatch):
    from app.services import ingest_budget
    from app.services import object_storage

    fake = FakePool()
    stored: list[bytes] = []
    calls = {"procedures": [], "blobs": stored, "guards": [], "records": []}

    async def fake_capture(_pool, **kw):
        calls["procedures"].append(kw)
        return {"id": f"row-{len(calls['procedures'])}", "procedure_id": f"proc-{len(calls['procedures'])}"}

    async def fake_store_blob(_pool, _store, content, content_type="text/plain"):
        stored.append(content)
        return {"locator": f"blob/{len(stored)}"}

    async def fake_guard(op: str = "") -> None:
        calls["guards"].append(op)

    async def fake_record(model, op, usage, provider="google") -> None:
        calls["records"].append((model, op))

    monkeypatch.setattr("app.services.procedures.capture_procedure", fake_capture)
    monkeypatch.setattr(object_storage, "get_store", lambda: object())
    monkeypatch.setattr(object_storage, "store_blob", fake_store_blob)
    monkeypatch.setattr(ingest_budget, "guard", fake_guard)
    monkeypatch.setattr(ingest_budget, "record_completion", fake_record)
    return SimpleCalls(fake, calls)


class SimpleCalls:
    def __init__(self, pool, calls) -> None:
        self.pool = pool
        self.calls = calls

    @property
    def procedures(self) -> list[dict]:
        return self.calls["procedures"]

    @property
    def blobs(self) -> list[bytes]:
        return self.calls["blobs"]

    @property
    def guards(self) -> list[str]:
        return self.calls["guards"]

    @property
    def records(self) -> list:
        return self.calls["records"]


def ingest(pool, files, client=None, **kw) -> dict:
    return asyncio.run(ri.ingest_repo(
        pool, REPO, client=client or FakeClient(), model="m", http_get=github(files),
        commit=COMMIT, per_domain=ri.MAX_PER_DOMAIN, **kw))


def test_twelve_files_cost_two_calls_and_the_budget_is_charged_once_per_call(pool):
    files = {**STYLES, **SCRIPTS}
    client = FakeClient()
    out = ingest(pool.pool, files, client, concurrency=4)

    assert client.calls == 2, "twelve small files at batch size 8 is one full batch plus one of four"
    assert sorted(p for call in client.requested for p in call) == sorted(files)
    assert sorted(len(call) for call in client.requested) == [4, 8]
    assert len(pool.guards) == client.calls == len(pool.records) == 2
    assert set(pool.guards) == {ri.DESCRIPTION_OP}
    assert all(model == "m" and op == ri.DESCRIPTION_OP for model, op in pool.records)

    assert out["status"] == "captured" and len(out["captured"]) == 12
    assert out["description_batch_size"] == 8
    assert out["skipped"] == {} and [c["path"] for c in out["captured"]] == [p for p, _ in
                                                                            ri.select_files(tree_for(files))]


def test_batch_size_is_configurable_and_one_restores_the_per_file_prompt(pool):
    files = {**STYLES, **SCRIPTS}
    single = FakeClient()
    out = ingest(pool.pool, files, single, batch_size=1, concurrency=4)
    assert single.calls == 12 and all(len(call) == 1 for call in single.requested)
    assert len(pool.guards) == 12 and len(out["captured"]) == 12

    pool.pool.described.clear()
    pool.pool.inserts.clear()
    pool.calls["blobs"].clear()
    half = FakeClient()
    ingest(pool.pool, files, half, batch_size=4, concurrency=4)
    assert half.calls == 3 and sorted(len(call) for call in half.requested) == [4, 4, 4]

    pool.pool.described.clear()
    huge = FakeClient()
    ingest(pool.pool, files, huge, batch_size=99, concurrency=4)
    assert huge.calls == 2, "99 is clamped to MAX_DESCRIPTION_BATCH_SIZE, not honoured"
    assert sorted(len(call) for call in huge.requested) == [4, 8]
    assert ri.description_batch_size(99) == ri.MAX_DESCRIPTION_BATCH_SIZE
    assert ri.description_batch_size(0) == 1
    assert ri.description_batch_size("nonsense") == ri.DEFAULT_DESCRIPTION_BATCH_SIZE
    assert ri.description_batch_size(None) == ri.DEFAULT_DESCRIPTION_BATCH_SIZE
    assert ri.MAX_DESCRIPTION_BATCH_SIZE * ri.BATCH_ITEM_CHARS == ri.MAX_PROMPT_CHARS


def test_one_abstain_or_malformed_item_costs_only_that_file(pool):
    files = {"styles/a.css": b".a{color:#0af}", "styles/b.css": b".b{color:#fa0}",
             "styles/c.css": b".c{color:#0fa}", "styles/d.css": b".d{color:#af0}",
             "styles/e.css": b".e{color:#f0a}"}

    def script(path, _item_id):
        if path == "styles/a.css":
            return {"id": _item_id, "abstain": True}
        if path == "styles/b.css":
            return {"id": _item_id, "goal": "short", "description": "too short to pass"}
        if path == "styles/c.css":
            return {"id": "no-such-id", "goal": "Answer for an id nobody asked about here",
                    "description": "This row names an id that was never requested at all."}
        if path == "styles/d.css":
            return "not even an object"
        return None

    client = FakeClient(script)
    out = ingest(pool.pool, files, client, concurrency=2)

    assert client.calls == 1
    assert {c["path"] for c in out["captured"]} == {"styles/e.css"}
    assert out["skipped"] == {"abstained": 4}
    assert out["status"] == "captured"
    assert [kw["steps"][0]["source_locator"]["path"] for kw in pool.procedures] == ["styles/e.css"]


def test_one_failing_call_is_only_its_own_files_loss(pool):
    files = {f"styles/s{i}.css": f".s{i}{{color:#0{i}f0f0}}".encode() for i in range(3)}

    class HalfDown(FakeClient):
        def create(self, **kw):
            if "styles/s1.css" in kw["messages"][1]["content"]:
                self.calls += 1
                raise RuntimeError("provider is down")
            return super().create(**kw)

    out = ingest(pool.pool, files, HalfDown(), batch_size=1, concurrency=1)
    assert out["skipped"] == {"llm_error": 1}
    assert {c["path"] for c in out["captured"]} == {"styles/s0.css", "styles/s2.css"}
    assert out["status"] == "captured"


def test_a_whole_batch_that_fails_spends_nothing_and_is_reported_as_retryable(pool):
    files = {"styles/a.css": b".a{color:#0af}", "styles/b.css": b".b{color:#fa0",
             "scripts/boot.sh": b"#!/bin/sh\nexit 0\n"}

    class Exploding(FakeClient):
        def create(self, **kw):
            self.calls += 1
            raise RuntimeError("provider is down")

    with pytest.raises(RuntimeError, match="every description call failed"):
        ingest(pool.pool, files, Exploding(), concurrency=2)
    assert pool.pool.inserts == [] and pool.procedures == [] and pool.blobs == []


def test_a_budget_stop_still_propagates_before_a_single_byte_is_preserved(pool, monkeypatch):
    from app.services.governance import BudgetExceeded

    async def guard(_op: str = "") -> None:
        raise BudgetExceeded("ingestion model budget exceeded: $50.00 of $50.00")

    monkeypatch.setattr("app.services.ingest_budget.guard", guard)
    with pytest.raises(BudgetExceeded, match="budget exceeded"):
        ingest(pool.pool, {**STYLES, **SCRIPTS}, FakeClient(), concurrency=4)
    assert pool.pool.inserts == [] and pool.procedures == [] and pool.blobs == []


def test_code_quality_configs_are_described_by_template_with_zero_llm_calls(pool):
    client = FakeClient()
    out = ingest(pool.pool, CONFIGS, client, concurrency=2)

    assert client.calls == 0 and client.requested == []
    assert pool.guards == [] and pool.records == []
    assert out["status"] == "captured" and {c["path"] for c in out["captured"]} == set(CONFIGS)
    by_path = {kw["steps"][0]["source_locator"]["path"]: kw for kw in pool.procedures}
    prettier = by_path[".prettierrc.json"]
    assert prettier["goal"].startswith("Apply this repository's Prettier configuration")
    assert "printWidth" in prettier["steps"][0]["description"]
    assert "semi" in prettier["steps"][0]["description"]
    ruff = by_path["ruff.toml"]
    assert ruff["goal"].startswith("Apply this repository's Ruff configuration")
    assert "line-length" in ruff["steps"][0]["description"]
    assert all(10 <= len(kw["goal"]) <= 300 and 10 <= len(kw["display_description"]) <= 800
               for kw in pool.procedures)


def test_a_template_that_cannot_state_a_usable_goal_falls_back_to_the_llm(pool):
    domain = ri.classify(".prettierrc.json")
    assert domain.name == ri.TEMPLATE_DOMAIN
    assert ri.template_description(".prettierrc.json", domain, '{"printWidth": 100}') is not None
    assert ri.template_description("scripts/boot.sh", ri.classify("scripts/boot.sh"),
                                   "#!/bin/sh\n") is None
    assert ri.template_description("styles/a.css", ri.classify("styles/a.css"), ".a{}") is None
    assert ri.template_description(".prettierrc.json", domain, "")["goal"].startswith("Apply this")
    assert "carries this repository's Prettier configuration" in \
        ri.template_description(".prettierrc.json", domain, "")["description"]
    assert ri.config_settings('{"a": 1, "b": 2}') == ["a", "b"]
    assert ri.config_settings("line-length = 100\n[lint]\nselect = [\"E\"]\n") == ["line-length", "lint", "select"]
    assert ri.config_settings("") == []

    out = ingest(pool.pool, {**CONFIGS, "styles/a.css": b".a{color:#0af}"}, FakeClient(), concurrency=2)
    assert out["status"] == "captured" and len(out["captured"]) == 3
    assert {kw["steps"][0]["source_locator"]["path"] for kw in pool.procedures} == set(CONFIGS) | {"styles/a.css"}


def test_artifact_metadata_lands_in_one_statement_and_only_executables_are_copied(pool):
    files = {**STYLES, **SCRIPTS}
    out = ingest(pool.pool, files, FakeClient(), concurrency=4)

    assert len(pool.pool.inserts) == 1, "one multi-row upsert, not one statement per file"
    (sql, args) = pool.pool.inserts[0]
    assert sql.count("(gen_random_uuid()") == len(files)
    assert "ON CONFLICT (source_type, uri, content_hash) WHERE role IS NOT NULL" in sql
    assert len(args) == len(files) * _ARTIFACT_ROW_WIDTH
    assert len(pool.pool.preserved) == len(files)
    assert {row["path"] for row in pool.pool.preserved} == set(files)
    for row in pool.pool.preserved:
        assert row["status"] == ("stored" if row["role"] == "executable_source" else "metadata_only")
        assert (row["stored"] is not None) == (row["role"] == "executable_source")

    assert sorted(pool.blobs) == sorted(SCRIPTS.values()), "each executable's bytes, once, and only those"
    assert len(pool.blobs) == len(SCRIPTS)
    assert out["status"] == "captured" and len(out["captured"]) == len(files)
    assert len(pool.pool.links) == len(files)


def test_the_batched_statement_binds_every_column_it_names(pool):
    """A multi-row VALUES list is where a missing placeholder becomes a runtime
    Postgres error rather than a wrong answer, so the arity is asserted against
    the statement text itself and not only against the parameter list."""
    from app.services import skill_ingestion as si

    files = {"styles/a.css": b".a{color:#0af}", "styles/b.css": b".b{color:#fa0}",
             "scripts/boot.sh": b"#!/bin/sh\nexit 0\n"}
    ingest(pool.pool, files, FakeClient(), concurrency=2)
    (sql, args) = pool.pool.inserts[0]

    columns = sql[sql.index("(") + 1:sql.index(") VALUES ")].split(", ")
    values = sql[sql.index(") VALUES ") + len(") VALUES "):sql.index(" ON CONFLICT")].split("), (")
    assert len(columns) == 17
    assert len(values) == len(files)
    for offset, row in enumerate(values):
        expressions = row.strip("()").split(", ")
        assert len(expressions) == len(columns), "every VALUES tuple names every target column"
        bound = [int(e[1:].split("::")[0]) for e in expressions if e.startswith("$")]
        assert bound == list(range(offset * _ARTIFACT_ROW_WIDTH + 1,
                                   (offset + 1) * _ARTIFACT_ROW_WIDTH + 1))
    assert "false" in values[0], "execution_allowed is the literal, never a bound visibility string"
    assert args[_ARTIFACT_ROW_WIDTH - 1] is None
    assert si.REPO_ARTIFACT_ROW_PARAMS == _ARTIFACT_ROW_WIDTH
    assert si.ARTIFACT_UPSERT_BATCH == 64


def test_a_reexecuted_run_creates_nothing(pool):
    files = {**STYLES, **SCRIPTS, **CONFIGS}
    first_client = FakeClient()
    first = ingest(pool.pool, files, first_client, concurrency=4)
    assert first["status"] == "captured" and len(first["captured"]) == len(files)
    assert len(pool.pool.preserved) == len(files) and len(pool.blobs) == len(SCRIPTS)
    procedures = list(pool.procedures)
    inserts = list(pool.pool.inserts)

    second_client = FakeClient()
    second = ingest(pool.pool, files, second_client, concurrency=4)

    assert second_client.calls == 0
    assert second["captured"] == [] and second["status"] == "empty"
    assert second["skipped"] == {"already_described": len(files)}
    assert pool.pool.inserts == inserts, "no artifact metadata is rewritten for known content"
    assert pool.procedures == procedures
    assert pool.blobs == sorted(SCRIPTS.values())


def test_one_file_alone_keeps_the_single_file_prompt_and_one_call(pool):
    client = FakeClient()
    out = ingest(pool.pool, {"styles/a.css": b".a{color:#0af}"}, client)
    assert client.calls == 1 and client.requested == [["styles/a.css"]]
    assert out["status"] == "captured" and len(out["captured"]) == 1
    (kw,) = pool.procedures
    assert kw["steps"][0]["source_locator"]["uri"] == f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/styles/a.css"
    assert kw["steps"][0]["source_locator"]["content_hash"] == \
        hashlib.sha256(b".a{color:#0af}").hexdigest()


def test_a_file_too_large_for_a_bounded_excerpt_keeps_its_own_call(pool):
    big = b"/* " + (b"x" * (ri.MAX_BATCH_FILE_BYTES + 10)) + b" */"
    files = {"styles/huge.css": big, "styles/small.css": b".s{color:#0af}"}
    client = FakeClient()
    out = ingest(pool.pool, files, client, concurrency=2)
    assert client.calls == 2
    assert sorted(p for call in client.requested for p in call) == sorted(files)
    assert sorted(len(call) for call in client.requested) == [1, 1]
    assert out["status"] == "captured" and len(out["captured"]) == 2


def test_a_preservation_failure_skips_the_capture_instead_of_ingesting_unbacked_files(pool):
    pool.pool.insert_error = RuntimeError("ingested_artifacts is unavailable")
    files = {"styles/a.css": b".a{color:#0af}"}
    out = ingest(pool.pool, files, FakeClient(), concurrency=2)
    assert out["status"] == "empty" and out["skipped"] == {"artifact_error": 1}
    assert pool.procedures == [] and pool.pool.links == []
