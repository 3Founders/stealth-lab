"""Offline: the rebuilt OpenHands pipeline -- normalizer, selection rules, gates."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.ingest.openhands import normalize as nz
from app.ingest.openhands.select import pick


def _call(cid, name, args, text=""):
    return {"role": "assistant", "content": text, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _result(cid, name, text):
    return {"role": "tool", "content": text, "name": name, "tool_call_id": cid}


def _row(messages, resolved=1, tid="t1"):
    return {"trajectory_id": tid, "instance_id": "o__r-1", "repo": "o/r", "trajectory": messages,
            "model_patch": "diff --git a/x b/x", "exit_status": "submit", "resolved": resolved}


ISSUE = ("<uploaded_files>\n/workspace/r\n</uploaded_files>\nConsider the following issue description:\n"
         "<issue_description>\nParser drops the last line\n</issue_description>\nPlease fix it.")


def test_a_run_becomes_one_event_per_action_in_order():
    t = nz.normalize(_row([
        {"role": "system", "content": "You are OpenHands"},
        {"role": "user", "content": ISSUE},
        _call("a", "str_replace_editor", {"command": "view", "path": "/w/p.py"}, "Let me look."),
        _result("a", "str_replace_editor", "1 def parse(): ..."),
        _call("b", "execute_bash", {"command": "cd /w && python -m pytest tests -q"}),
        _result("b", "execute_bash", "1 failed\n[Command finished with exit code 1]"),
        _call("c", "str_replace_editor", {"command": "str_replace", "path": "/w/p.py", "old_str": "a", "new_str": "b"}),
        _result("c", "str_replace_editor", "edited"),
        _call("d", "execute_bash", {"command": "ls"}),
        _result("d", "execute_bash", "p.py\n[The command completed with exit code 0.]"),
        _call("e", "finish", {"message": "done"}),
    ]), dataset="nebius/x", revision="0" * 40)
    kinds = [(e.tool_name, e.canonical_event_type, e.success) for e in t.events]
    assert kinds == [("task", "OBSERVE", None), ("str_replace_editor", "READ", None), ("execute_bash", "TEST", False),
                     ("str_replace_editor", "WRITE", None), ("execute_bash", "EXECUTE", True),
                     ("finish", "COMMIT", None)]
    assert t.events[0].tool_input == {"issue": "Parser drops the last line"}
    assert t.events[1].tool_input["reasoning"] == "Let me look."
    assert [e.sequence for e in t.events] == list(range(6))
    assert len({e.dedup_key for e in t.events}) == 6
    assert t.outcome == "success" and t.metadata["declared_goal"] == "Parser drops the last line"
    assert t.metadata["system_prompt_sha256"] and t.model == nz.MODEL
    assert all(t.events[i].timestamp < t.events[i + 1].timestamp for i in range(5))


@pytest.mark.parametrize("messages,defect", [
    ([{"role": "user", "content": "x"}, _result("zz", "execute_bash", "out")], "unknown call"),
    ([{"role": "user", "content": "x"}, _call("a", "execute_bash", {"command": "ls"})], "has no result"),
    ([{"role": "user", "content": "x"}, {"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "function": {"name": "execute_bash", "arguments": "{not json"}}]}], "not JSON"),
    ([_call("a", "finish", {})], "task is missing"),
])
def test_a_broken_run_is_rejected_whole(messages, defect):
    with pytest.raises(nz.MalformedTrajectory, match=defect):
        nz.normalize(_row(messages), dataset="d", revision="0" * 40)


def test_unknown_tools_are_not_given_a_guessed_type():
    t = nz.normalize(_row([{"role": "user", "content": "x"}, _call("a", "browser", {"url": "u"}),
                           _result("a", "browser", "page")]), dataset="d", revision="0" * 40)
    assert t.events[1].canonical_event_type is None


def _run(tid, instance, resolved, exit_status="submit", messages=50):
    return {"trajectory_id": tid, "instance_id": instance, "repo": "o/r", "resolved": resolved,
            "exit_status": exit_status, "messages": messages, "row_group": 0, "row_index": 0}


def test_selection_keeps_the_most_direct_success_and_one_real_wrong_attempt():
    runs = [_run("t3", "A", 1, messages=80), _run("t2", "A", 1, messages=40), _run("t1", "A", 1, messages=40),
            _run("f1", "A", 0, "RuntimeError: Agent reached maximum iteration", messages=10),
            _run("f2", "A", 0, messages=60), _run("f3", "A", 0, messages=55),
            _run("g1", "B", 0, "Timeout", messages=5)]
    chosen = pick(runs)
    picked = {(s.instance_id, s.outcome): s.trajectory_id for s in chosen}
    assert picked == {("A", "resolved"): "t1", ("A", "failed"): "f3"}, "B has only a timeout: nothing to learn"
    a = next(s for s in chosen if s.instance_id == "A")
    assert (a.runs_total, a.runs_resolved) == (6, 3)


def test_item_keys_are_task_and_outcome():
    s = pick([_run("t1", "A", 1)])[0]
    assert s.item_key == "A:resolved" and s.dedup_key == "swe-task:A:resolved"


# --- gates ----------------------------------------------------------------------------------------------------

class _Ledger:
    def __init__(self, owner=None):
        self.owner = owner

    async def identity_owner(self, key):
        return self.owner


def _state(**kw):
    from app.ingest.openhands.pipeline import ParentTask, RunState

    class Held:
        scored_repos = ("django/django",)

        def is_held_out(self, x):
            return x == "held__one-1"

    parents = kw.pop("parents", {"o__r-1": ParentTask("MIT License", "abc", ("t",), (), "img")})
    return RunState(ledger=kw.pop("ledger", _Ledger()), held=Held(), parents=parents, client=None)


def _item(instance="o__r-1", repo="o/r"):
    from app.ingest.openhands.select import Selected

    return Selected(instance, repo, "t1", "resolved", 0, 0, 40, "submit")


@pytest.mark.parametrize("item,state_kw,reason", [
    (dict(instance="held__one-1"), {}, "held_out_instance"),
    (dict(repo="Django/Django"), {}, "held_out_repo"),
    ({}, dict(ledger=_Ledger({"pipeline": "openhands", "item_key": "x"})), "duplicate_identity"),
    ({}, dict(parents={}), "parent_missing"),
])
def test_gates_reject_with_a_reason(item, state_kw, reason):
    from app.ingest.openhands.pipeline import gate

    got, _detail = asyncio.run(gate(_state(**state_kw), _item(**item)))
    assert got == reason


def test_license_gates():
    from app.ingest.openhands.pipeline import ParentTask, gate

    for name, reason in (("BSD", "license_unmappable"), (None, "license_unmappable"),
                         ("Zope Public License 2.1", "license_quarantine")):
        got, _ = asyncio.run(gate(_state(parents={"o__r-1": ParentTask(name, "c")}), _item()))
        assert got == reason, name
    ok, detail = asyncio.run(gate(_state(), _item()))
    assert ok is None and detail["used_under"] == "MIT"


def test_credit_names_the_dataset_and_the_repository_license():
    from app.ingest.openhands.pipeline import credit

    c = credit(_item(), "MIT")
    assert c["license"] == "CC-BY-4.0" and c["repository_license"] == "MIT"
    assert "Nebius" in c["notice"] and "o/r" in c["notice"] and "MIT" in c["notice"]
