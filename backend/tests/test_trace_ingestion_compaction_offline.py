"""
Trace ingestion + semantic compaction, at the real entry point
`extract_trajectory_semantics` (raw trace_events -> compactor -> extraction
prompt -> Goals/Claims/Implementations/Procedures). Mocked semantic providers
and a scripted extraction client; no network, no DB.
"""
import json
import re

import pytest

import app.services.trajectory_semantics as ts
from app.config import settings
from app.services.access import AccessScope
from app.services.procedure_extraction.schema import ExtractionTransientFailure
from tests.semantic_fakes import ScriptedProvider, make_judge, transient
from tests.test_trajectory_semantics_orchestration_offline import FakePool, _episode_row, _patch_writers

BIG = "class Settings:\n" + "    value = 'x' * 40\n" * 400          # a ~4k-token file read
CLAIMS = [{"claim_id": "C-18", "statement": "port is 8080"}, {"claim_id": "C-19", "statement": "db is postgres"}]


def ev(idx, tool, tin, tout, success=True, canonical="EXECUTE"):
    return {"id": f"00000000-0000-0000-0000-{idx:012d}", "sequence": idx, "event_type": "PostToolUse",
            "canonical_event_type": canonical, "tool_name": tool, "tool_input": tin, "tool_output": tout,
            "success": success, "timestamp": None}


def trajectory(n_noise=30):
    rows = [ev(1, "Read", {"file_path": "config/app.py"}, {"content": BIG}, canonical="READ")]
    for i in range(n_noise):
        rows.append(ev(2 + i, "Grep", {"pattern": f"needle{i}"}, {"matches": f"match {i} " * 30}, canonical="SEARCH"))
    k = 2 + n_noise
    rows.append(ev(k, "Bash", {"command": "pytest tests/test_auth.py"},
                   {"stdout": "FAILED test_auth_refresh - AssertionError\nexit code 1"}, success=False, canonical="TEST"))
    rows.append(ev(k + 1, "Edit", {"file_path": "auth.py"}, {"ok": True}, canonical="EDIT"))
    return rows


class RecordingPool(FakePool):
    async def fetchrow(self, sql, *args):
        if "context_retention_cache" in sql or "context_compaction_views" in sql:
            return None
        return await super().fetchrow(sql, *args)


class PromptClient:
    """Scripted extraction model. `respond(prompt)` builds the JSON reply from
    the prompt it actually received."""

    def __init__(self, respond):
        self.prompts, self._respond = [], respond
        outer = self

        class _C:
            @staticmethod
            def create(**kw):
                prompt = kw["messages"][-1]["content"]
                outer.prompts.append(prompt)
                msg = type("M", (), {"content": json.dumps(outer._respond(prompt))})()
                return type("R", (), {"choices": [type("Ch", (), {"message": msg})()]})()

        self.chat = type("Chat", (), {"completions": _C})()


def empty_extraction(**over):
    base = {"primary_goal": None, "subgoals": [], "candidate_procedures": [], "implementations": [], "claims": [],
            "preconditions": [], "failure_modes": [], "recovery_patterns": [], "verification_actions": [],
            "outcome": "failure", "reusable_elements": [], "uncertainties": []}
    base.update(over)
    return base


def retention_model(state, units):
    out = []
    refs = [c["id"] for c in state["claims"]][:1]
    for u in units:
        if u["kind"] == "file_read" and refs:
            action = ("KEEP_REFERENCE_ONLY", refs)
        elif u["kind"] in ("search", "status"):      # search noise + narration/waiting/background-task junk
            action = ("DROP", [])
        elif u["failed"] or u["kind"] == "test":
            action = ("KEEP_VERBATIM", [])
        else:
            action = ("KEEP_COMPACT", [])
        out.append({"unit_id": u["unit_id"], "action": action[0], "relevance": 0.3, "reason": "mock",
                    "durable_refs": action[1], "confidence": 0.9})
    return out


def summary_model(state, units):
    return {u["unit_id"]: {"kind": u["kind"], "attempted": u["call"], "path": u["path"]} for u in units}


def working_judge():
    prov = ScriptedProvider("jev", [None])
    prov.batches = []          # size of every retention request

    def nxt(op, *a):
        if op == "retention":
            prov.batches.append(len(a[1]))
            return retention_model(*a)
        return summary_model(*a)
    prov._next = nxt
    return make_judge(prov), prov


@pytest.fixture
def low_threshold(monkeypatch):
    """Kept as a fixture name for the tests below; there is NO size gate any
    more, so this only makes sure compaction is enabled."""
    monkeypatch.setattr(settings, "trace_ingestion_compaction_enabled", True)


@pytest.fixture
def relevant_claims(monkeypatch):
    seen = {}

    async def fake(pool, *, goal, top_k, access_scope=None):
        seen["scope"] = access_scope
        return CLAIMS
    monkeypatch.setattr("app.services.relevant_claims.get_relevant_claims", fake)
    return seen


def episode(**kw):
    return _episode_row(metadata={"declared_goal": "fix auth refresh"}, **kw)


def object_links(pool):
    return [a for s, a in pool.executed if "INSERT INTO trajectory_extraction_objects" in s]


# --------------------------------------------------------------------------


def junk(idx, message, event_type="Notification"):
    """A non-tool narration/status event -- the kind of noise a short episode still has."""
    return {"id": f"00000000-0000-0000-0000-{idx:012d}", "sequence": idx, "event_type": event_type,
            "canonical_event_type": "STATUS", "tool_name": None, "tool_input": {"message": message},
            "tool_output": None, "success": None, "timestamp": None}


@pytest.mark.asyncio
async def test_even_a_tiny_episode_is_compacted_and_junk_never_reaches_the_extractor(monkeypatch, relevant_claims):
    """No size gate: 8 short events, all far below any token threshold."""
    _patch_writers(monkeypatch)
    judge, prov = working_judge()
    rows = [
        junk(1, "Reading config/app.py now..."),
        ev(2, "Read", {"file_path": "config/app.py"}, {"content": "PORT=8080"}, canonical="READ"),
        junk(3, "Waiting for the reviewer agent to respond"),
        junk(4, "Launched background task bg-42"),
        ev(5, "Bash", {"command": "pytest tests/test_auth.py"},
           {"stdout": "FAILED test_auth_refresh\nexit code 1"}, success=False, canonical="TEST"),
        junk(6, "Still waiting for the reviewer agent..."),
        ev(7, "Edit", {"file_path": "auth.py"}, {"ok": True}, canonical="EDIT"),
        junk(8, "Polling background task bg-42"),
    ]
    pool = RecordingPool(episode(), rows)
    client = PromptClient(lambda p: empty_extraction())
    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client, compaction_judge=judge)

    prompt = client.prompts[0]
    assert result["compaction"]["status"] == "compacted" and prov.batches == [8]   # one batched judge request
    for noise in ("Reading config", "Waiting for the reviewer", "Launched background", "Still waiting", "Polling"):
        assert noise not in prompt
    assert "FAILED test_auth_refresh" in prompt and "auth.py" in prompt            # real work kept
    assert len(re.findall(r"^\d+\. ", prompt, re.M)) < len(rows)


@pytest.mark.asyncio
async def test_no_recency_pin_in_offline_ingestion_so_junk_at_the_end_can_still_be_dropped(monkeypatch, relevant_claims):
    """The agent-loop 'recent window' pin would shield every unit of a short episode."""
    _patch_writers(monkeypatch)
    judge, _ = working_judge()
    rows = [ev(1, "Edit", {"file_path": "a.py"}, {"ok": True}, canonical="EDIT")] + \
           [junk(i, f"heartbeat {i}") for i in range(2, 6)]                      # trailing junk, all inside the last 6
    client = PromptClient(lambda p: empty_extraction())
    await ts.extract_trajectory_semantics(RecordingPool(episode(), rows), "episode-1", client=client,
                                          compaction_judge=judge)
    assert "heartbeat" not in client.prompts[0] and "a.py" in client.prompts[0]


@pytest.mark.asyncio
async def test_large_trajectory_is_compacted_before_extraction_and_citations_stay_real(
        monkeypatch, low_threshold, relevant_claims):
    calls = _patch_writers(monkeypatch)
    judge, prov = working_judge()
    rows = trajectory()
    pool = RecordingPool(episode(), rows)

    def respond(prompt):
        persisted = int(re.search(r"^(\d+)\. \[persisted\]", prompt, re.M).group(1))
        failed = int(re.search(r"^(\d+)\. .*FAILED test_auth_refresh", prompt, re.M).group(1))
        return empty_extraction(claims=[
            {"text": "config lives in config/app.py", "event_indices": [persisted], "epistemic_status": "observed", "confidence": 0.9},
            {"text": "the auth refresh test fails", "event_indices": [failed], "epistemic_status": "observed", "confidence": 0.9}])

    client = PromptClient(respond)
    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client, compaction_judge=judge)

    info = result["compaction"]
    assert info["status"] == "compacted" and info["retained_tokens"] < info["input_tokens"] // 3
    assert prov.batches == [20, 13]                                       # 33 units judged in 2 batched requests, not 33
    prompt = client.prompts[0]
    assert BIG not in prompt and "needle7" not in prompt                  # bulky read reduced, search noise dropped
    assert "FAILED test_auth_refresh" in prompt                           # unresolved failure kept verbatim
    assert "[persisted] " in prompt and "C-18" in prompt                  # read replaced by pointer to durable Claim
    assert len(re.findall(r"^\d+\. ", prompt, re.M)) < len(rows)          # fewer prompt lines than raw events

    # extracted claims cite REAL trace_event ids, not prompt indices
    ids = {r["id"] for r in rows}
    links = object_links(pool)
    assert len(calls["claims"]) == 2 and len(links) == 2
    cited = [set(str(x) for x in a[3]) for a in links]                    # (extraction_id, type, object_id, event_refs, ...)
    assert all(c and c <= ids for c in cited)
    assert {rows[0]["id"]} in cited                                       # the persisted read cites the actual Read event


@pytest.mark.asyncio
async def test_providers_down_under_the_cap_retains_the_full_legacy_prompt(monkeypatch, low_threshold, relevant_claims):
    _patch_writers(monkeypatch)
    down = make_judge(ScriptedProvider("jev", [transient()]), ScriptedProvider("gemini", [transient()]))
    rows = trajectory()
    pool = RecordingPool(episode(), rows)
    client = PromptClient(lambda p: empty_extraction())
    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client, compaction_judge=down)
    assert result["compaction"]["status"] == "skipped_unavailable"
    assert len(re.findall(r"^\d+\. ", client.prompts[0], re.M)) == len(rows)      # nothing dropped
    assert "needle7" in client.prompts[0]


@pytest.mark.asyncio
async def test_providers_down_over_the_cap_never_silently_truncates_the_tail(monkeypatch, low_threshold, relevant_claims):
    _patch_writers(monkeypatch)
    down = make_judge(ScriptedProvider("jev", [transient()]))
    rows = [ev(i, "Bash", {"command": f"echo {i}"}, {"o": i}) for i in range(1, 251)]     # > 200 event cap
    pool = RecordingPool(episode(), rows)
    client = PromptClient(lambda p: empty_extraction())
    with pytest.raises(ExtractionTransientFailure, match="refusing to silently truncate"):
        await ts.extract_trajectory_semantics(pool, "episode-1", client=client, compaction_judge=down)
    assert client.prompts == []          # no extraction call on a truncated view; the job is retried later


@pytest.mark.asyncio
async def test_over_the_cap_is_compacted_instead_of_losing_events_201_plus(monkeypatch, low_threshold, relevant_claims):
    _patch_writers(monkeypatch)
    judge, _ = working_judge()
    rows = [ev(i, "Grep", {"pattern": f"p{i}"}, {"m": "x"}, canonical="SEARCH") for i in range(1, 251)]
    rows[-1] = ev(250, "Bash", {"command": "pytest"}, {"stdout": "FAILED test_late_failure\nexit code 1"}, False, "TEST")
    pool = RecordingPool(episode(), rows)
    client = PromptClient(lambda p: empty_extraction())
    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client, compaction_judge=judge)
    assert result["compaction"]["status"] == "compacted"
    assert "FAILED test_late_failure" in client.prompts[0]     # the legacy hard cap would have cut this tail event


@pytest.mark.asyncio
async def test_claims_offered_to_the_judge_are_scoped_to_the_episode_owner(monkeypatch, low_threshold, relevant_claims):
    _patch_writers(monkeypatch)
    judge, _ = working_judge()
    pool = RecordingPool(episode(owner_id="user-7", visibility="private"), trajectory())
    await ts.extract_trajectory_semantics(pool, "episode-1", client=PromptClient(lambda p: empty_extraction()),
                                          compaction_judge=judge, owner_id="user-7", visibility="private")
    scope = relevant_claims["scope"]
    assert scope == AccessScope.for_user("user-7") and not scope.is_unrestricted


@pytest.mark.asyncio
async def test_compaction_can_be_disabled_by_config(monkeypatch, relevant_claims):
    _patch_writers(monkeypatch)
    monkeypatch.setattr(settings, "trace_ingestion_compaction_enabled", False)
    judge, prov = working_judge()
    result = await ts.extract_trajectory_semantics(
        RecordingPool(episode(), trajectory()), "episode-1",
        client=PromptClient(lambda p: empty_extraction()), compaction_judge=judge)
    assert result["compaction"]["status"] == "disabled" and prov.batches == []
