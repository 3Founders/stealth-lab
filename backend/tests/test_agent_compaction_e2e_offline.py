"""
End-to-end: semantic compaction wired into the REAL `Agent.run` loop
(app/execution/coding_agent.py) via `MessageCompactor`. Fake OpenAI-style
client + mocked semantic providers; real RepoSandbox on a temp dir. No network.
"""
import copy
import types

from app.execution.coding_agent import Agent, RepoSandbox
from app.services.context_compaction.engine import CompactionConfig
from app.services.context_compaction.harness import COMPACT_HEADER, MessageCompactor
from app.services.context_compaction.models import Action, StealthState
from tests.semantic_fakes import ScriptedProvider, make_judge, transient

INSTANCE = {"repo": "acme/app", "problem_statement": "Fix the port bug", "instance_id": "i1"}
READS = 8


class ScriptedModel:
    """Reads a different slice of big.txt each step, then calls finish. Records
    a deep copy of the messages sent on every request."""

    def __init__(self):
        self.requests: list[list[dict]] = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(copy.deepcopy(kw["messages"]))
        n = len(self.requests)
        name, args = ("read_file", f'{{"path": "big.txt", "start_line": {n * 40}, "num_lines": 40}}') \
            if n <= READS else ("finish", "{}")
        call = types.SimpleNamespace(id=f"call{n}", function=types.SimpleNamespace(name=name, arguments=args))
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="", tool_calls=[call]))],
            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1))


def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir(parents=True)
    (d / "big.txt").write_text("\n".join(f"line {i}: " + "x" * 60 for i in range(1, 500)), encoding="utf-8")
    return RepoSandbox(str(d))


def tokens(messages):
    return sum(len(str(m.get("content") or "")) // 4 for m in messages)


def assert_valid_structure(messages):
    """Every tool message must answer a tool_call issued by an earlier assistant message."""
    issued = set()
    for m in messages:
        if m["role"] == "assistant":
            issued |= {c["id"] for c in (m.get("tool_calls") or [])}
        if m["role"] == "tool":
            assert m["tool_call_id"] in issued, f"orphaned tool result {m['tool_call_id']}"


def reference_judge():
    """Mock model: big file reads are already persisted as Claim C-1 -> reference only."""
    prov = ScriptedProvider("jev", [None])
    prov._next = lambda op, state, units: [
        {"unit_id": u["unit_id"], "action": Action.KEEP_REFERENCE_ONLY.value, "relevance": 0.2,
         "reason": "persisted", "durable_refs": ["C-1"], "confidence": 0.9} for u in units]
    return make_judge(prov)


def compactor(judge):
    return MessageCompactor(
        judge_factory=lambda: judge,
        state=StealthState(goal="fix port bug", claims=[{"claim_id": "C-1", "statement": "port is set in big.txt"}]),
        cfg=CompactionConfig(token_threshold=1500, recent_window=0, min_units=1), keep_recent_messages=4)


def run_agent(tmp_path, comp=None):
    model = ScriptedModel()
    result = Agent(model, "m", compactor=comp).run(INSTANCE, repo(tmp_path), arm="t")
    return result, model


def test_default_agent_is_unchanged_without_a_compactor(tmp_path):
    result, model = run_agent(tmp_path)
    assert result.stop_reason == "finished"
    assert [len(r) for r in model.requests] == [2 + 2 * i for i in range(READS + 1)]   # history only ever grows


def test_compaction_shrinks_what_the_model_sees_and_keeps_the_conversation_valid(tmp_path):
    _, control = run_agent(tmp_path / "a")
    comp = compactor(reference_judge())
    result, model = run_agent(tmp_path / "b", comp)

    assert result.stop_reason == "finished"
    last, control_last = model.requests[-1], control.requests[-1]
    assert tokens(last) < tokens(control_last) // 2                       # real reduction on the final request
    assert_valid_structure(last)
    for req in model.requests:
        assert req[0]["role"] == "system" and "Fix the port bug" in req[1]["content"]   # head kept exactly
        assert_valid_structure(req)
    compacted = next(m for m in last if str(m["content"]).startswith(COMPACT_HEADER))
    assert "persisted as C-1" in compacted["content"] and "big.txt" in compacted["content"]
    assert last[-1]["role"] == "tool" and last[-1]["tool_call_id"] == f"call{READS}"  # most recent step untouched
    assert any(h["status"] == "compacted" for h in comp.history)


def test_providers_down_agent_keeps_full_history_and_still_finishes(tmp_path):
    _, control = run_agent(tmp_path / "a")
    down = make_judge(ScriptedProvider("jev", [transient()]), ScriptedProvider("gemini", [transient()]))
    comp = compactor(down)
    result, model = run_agent(tmp_path / "b", comp)
    assert result.stop_reason == "finished"
    assert [tokens(r) for r in model.requests] == [tokens(r) for r in control.requests]   # nothing removed
    assert comp.history and all(h["status"] == "skipped_unavailable" for h in comp.history)


def test_compactor_exception_never_breaks_the_agent_loop(tmp_path):
    def boom():
        raise RuntimeError("judge construction failed")
    comp = MessageCompactor(judge_factory=boom, cfg=CompactionConfig(token_threshold=1500, min_units=1),
                            keep_recent_messages=4)
    result, _ = run_agent(tmp_path, comp)
    assert result.stop_reason == "finished" and comp.history[-1]["status"] == "error"


def test_below_threshold_compaction_is_a_no_op_and_calls_no_provider(tmp_path):
    prov = ScriptedProvider("jev", [None])
    comp = MessageCompactor(judge_factory=lambda: make_judge(prov),
                            cfg=CompactionConfig(token_threshold=10 ** 9), keep_recent_messages=4)
    result, _ = run_agent(tmp_path, comp)
    assert result.stop_reason == "finished" and prov.calls == [] and comp.history == []
