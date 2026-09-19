"""
Semantic context compaction (mocked providers, no network): retention
decisions, pinning + safety guards, tool call/result pairing, batching, cache,
safe skip when every provider is down, raw-history immutability, handoff.
"""
import asyncio
import copy

from app.services.context_compaction import durable
from app.services.context_compaction.engine import (
    CompactionConfig, MemoryRetentionCache, compact_context, run_compaction_job, should_compact,
)
from app.services.context_compaction.models import Action, ContextItem, StealthState
from app.services.context_compaction.normalize import group_units, items_from_chat_messages
from app.services.semantic.jobs import COMPACT_CONTEXT_JOB
from tests.semantic_fakes import QueuePool, ScriptedProvider, make_judge, transient

CFG = CompactionConfig(recent_window=0, min_units=1)
BIG_CONFIG = "port: 8080\n" + "key: value\n" * 2000       # ~5k tokens


def _run(coro):
    return asyncio.run(coro)


def call(i, name, args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"t{i}", "function": {"name": name, "arguments": args}}]}


def result(i, text):
    return {"role": "tool", "tool_call_id": f"t{i}", "content": text}


def session():
    return items_from_chat_messages([
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Fix the failing auth refresh test. Do not touch prod config."},
        call(1, "Read", {"file_path": "config/app.yaml"}), result(1, BIG_CONFIG),
        call(2, "Bash", {"command": "pytest tests/test_auth.py"}),
        result(2, "FAILED test_auth_refresh - AssertionError\n1 failed\nexit code 1"),
        call(3, "Bash", {"command": "pnpm generate-api"}), result(3, "generated src/generated/api.ts\nexit code 0"),
        {"role": "user", "content": "ok continue"},
    ])


def state(**kw):
    base = dict(goal="fix auth refresh", claims=[{"claim_id": f"C-{n}", "statement": f"fact {n}"} for n in (18, 19, 20)],
                implementations=["IMPL-7"], blockers=["auth refresh test failing"])
    base.update(kw)
    return StealthState(**base)


def by_unit(items):
    return {u.unit_id: u for u in group_units(items)}


def fake_retention(plan=None, default=Action.KEEP_VERBATIM, counter=None):
    """A mock 'model': decides by unit content, like a real judge would."""
    def model(state_prompt, units):
        if counter is not None:
            counter.append(len(units))
        out = []
        for u in units:
            action, refs = (plan or {}).get(u["kind"], (default, []))
            if u["kind"] == "file_read":
                action, refs = (plan or {}).get("file_read", (Action.KEEP_REFERENCE_ONLY, ["C-18", "C-19", "C-20"]))
            out.append({"unit_id": u["unit_id"], "action": action.value, "relevance": 0.3, "reason": "mock",
                        "durable_refs": refs, "confidence": 0.9})
        return out
    return model


def fake_summary(state_prompt, units):
    return {u["unit_id"]: {"kind": u["kind"], "attempted": u["call"] or u["kind"], "path": u["path"],
                           "result": (u["content"] or "")[:30].replace("\n", " ")} for u in units}


def judge_for(retention, summary=fake_summary, **kw):
    prov = ScriptedProvider("jev", [None])
    prov._next = lambda op, *a: (retention if op == "retention" else summary)(*a)
    return make_judge(prov, **kw), prov


def view_ids(res):
    return {i for e in res.view for i in e.item_ids}


# --------------------------------------------------------------- examples from the spec


def test_I_persisted_unchanged_file_read_becomes_reference_only():
    items, st = session(), state()
    judge, _ = judge_for(fake_retention(default=Action.KEEP_VERBATIM))
    res = _run(compact_context(items, st, judge, cfg=CFG, force=True))
    read = next(e for e in res.view if "config/app.yaml" in e.content or e.action is Action.KEEP_REFERENCE_ONLY)
    assert read.action is Action.KEEP_REFERENCE_ONLY
    assert "persisted as C-18,C-19,C-20" in read.content and "(unchanged)" in read.content
    assert BIG_CONFIG not in res.render()
    assert res.stats["retained_tokens"] < res.stats["input_tokens"] // 2 and res.stats["counts"]["KEEP_REFERENCE_ONLY"] >= 1


def test_reference_only_must_point_at_real_durable_state():
    """A hallucinated ref (not in Stealth state) is upgraded to KEEP_COMPACT, never trusted."""
    judge, _ = judge_for(fake_retention(plan={"file_read": (Action.KEEP_REFERENCE_ONLY, ["C-999"])}))
    res = _run(compact_context(session(), state(), judge, cfg=CFG, force=True))
    d = next(d for d in res.decisions if d.action is not Action.KEEP_VERBATIM and d.source == "guard")
    assert d.action is Action.KEEP_COMPACT and BIG_CONFIG not in res.render()
    assert "config/app.yaml" in res.render()                    # path literal preserved in the compact form


def test_H_unresolved_test_failure_is_retained_even_if_the_model_says_drop():
    judge, _ = judge_for(fake_retention(plan={"test": (Action.DROP, [])}))
    res = _run(compact_context(session(), state(), judge, cfg=CFG, force=True))
    assert "test_auth_refresh" in res.render()
    failing = next(d for d in res.decisions if d.item_id in {i.item_id for i in session() if i.kind == "test"} or d.source == "pin")
    assert failing.action in (Action.KEEP_VERBATIM, Action.KEEP_COMPACT) and failing.source in ("pin", "guard")


def test_J_failed_attempt_is_kept_compact_to_prevent_a_retry_loop_even_when_resolved_later():
    msgs = [{"role": "user", "content": "build it"}]
    for n in (1, 2, 3):
        msgs += [call(n, "Bash", {"command": "npm run build"}), result(n, "Error: missing dependency left-pad\nexit code 1")]
    msgs += [call(4, "Bash", {"command": "npm run build"}), result(4, "built ok\nexit code 0")]
    items = items_from_chat_messages(msgs)
    judge, _ = judge_for(fake_retention(default=Action.DROP))
    res = _run(compact_context(items, state(blockers=[]), judge, cfg=CFG, force=True))
    kept = [e for e in res.view if "npm run build" in e.content]
    assert len(kept) >= 3, "the three failed attempts must survive as negative knowledge"
    assert all(e.action in (Action.KEEP_COMPACT, Action.KEEP_VERBATIM) for e in kept)


def test_successful_persisted_implementation_run_can_be_reference_only():
    items = items_from_chat_messages([{"role": "user", "content": "regen"}, call(1, "Bash", {"command": "pnpm generate-api"}),
                                      result(1, "ok\nexit code 0")])
    judge, _ = judge_for(fake_retention(plan={"command": (Action.KEEP_REFERENCE_ONLY, ["IMPL-7"])}))
    res = _run(compact_context(items, state(blockers=[]), judge, cfg=CFG, force=True))
    ref = next(e for e in res.view if e.action is Action.KEEP_REFERENCE_ONLY)
    assert "pnpm generate-api" in ref.content and "IMPL-7" in ref.content


# --------------------------------------------------------------- pinning / pairing / summaries


def test_hard_pins_system_and_user_instructions_survive_a_drop_everything_model():
    judge, _ = judge_for(fake_retention(default=Action.DROP, plan={"file_read": (Action.DROP, [])}))
    res = _run(compact_context(session(), state(), judge, cfg=CFG, force=True))
    text = res.render()
    assert "You are a coding agent." in text and "Do not touch prod config." in text and "ok continue" in text


def test_tool_call_and_result_are_judged_as_one_pair_and_never_orphaned():
    items = session()
    units = group_units(items)
    read_unit = next(u for u in units if u.result.kind == "file_read")
    assert [i.kind for i in read_unit.items] == ["tool_call", "file_read"]
    seen_units = []
    judge, prov = judge_for(lambda s, us: (seen_units.extend(us), fake_retention()(s, us))[1])
    _run(compact_context(items, state(), judge, cfg=CFG, force=True))
    assert all(len(u["unit_id"]) for u in seen_units) and len(seen_units) == len(units)   # 1 decision per PAIR
    judge2, _ = judge_for(fake_retention(plan={"file_read": (Action.DROP, [])}))
    res = _run(compact_context(items, state(), judge2, cfg=CFG, force=True))
    kept = view_ids(res)
    for u in units:  # a pair is kept whole or removed whole (DROP_PAIR)
        assert all(i.item_id in kept for i in u.items) or not any(i.item_id in kept for i in u.items)


def test_summary_that_loses_a_required_literal_falls_back_to_verbatim():
    bad_summary = lambda s, us: {u["unit_id"]: {"kind": "x", "result": "something happened"} for u in us}
    judge, _ = judge_for(fake_retention(plan={"file_read": (Action.KEEP_COMPACT, [])}), summary=bad_summary)
    res = _run(compact_context(session(), state(), judge, cfg=CFG, force=True))
    assert BIG_CONFIG in res.render()             # exact content kept: summary dropped config/app.yaml


def test_summary_provider_down_keeps_content_verbatim_and_queues_retry():
    prov = ScriptedProvider("jev", [None])
    prov._next = lambda op, *a: fake_retention(plan={"file_read": (Action.KEEP_COMPACT, [])})(*a) if op == "retention" \
        else (_ for _ in ()).throw(transient("summary down"))
    pool = QueuePool()
    res = _run(compact_context(session(), state(), make_judge(prov), cfg=CFG, force=True, pool=pool, session_id="s1"))
    assert res.status == "compacted" and BIG_CONFIG in res.render() and res.stats["summary_unavailable"] >= 1
    assert res.retry_job_id and pool.pending(COMPACT_CONTEXT_JOB)


# --------------------------------------------------------------- K batching / cache


def test_K_many_units_are_judged_in_batched_requests_not_one_call_each():
    msgs = [{"role": "user", "content": "go"}]
    for n in range(1, 46):
        msgs += [call(n, "Grep", {"pattern": f"p{n}"}), result(n, f"match {n}")]
    items = items_from_chat_messages(msgs)
    sizes = []
    judge, prov = judge_for(fake_retention(counter=sizes, default=Action.KEEP_VERBATIM))
    _run(compact_context(items, state(), judge, cfg=CompactionConfig(recent_window=0, min_units=1, batch_size=20), force=True))
    assert sizes == [20, 20, 6]                                  # 46 units -> 3 requests, not 46


def test_retention_cache_hit_skips_provider_and_state_change_invalidates_it():
    cache, items = MemoryRetentionCache(), session()
    sizes = []
    judge, _ = judge_for(fake_retention(counter=sizes))
    _run(compact_context(items, state(), judge, cfg=CFG, cache=cache, force=True))
    first = len(sizes)
    _run(compact_context(items, state(), judge, cfg=CFG, cache=cache, force=True))
    assert len(sizes) == first and cache.hits > 0                    # fully cached: no new provider call
    _run(compact_context(items, state(blockers=["a NEW blocker"]), judge, cfg=CFG, cache=cache, force=True))
    assert len(sizes) > first                                        # durable state changed -> re-judged


# --------------------------------------------------------------- D: providers down => retain, never destroy


def test_D_all_providers_fail_retains_original_context_and_queues_retry():
    items = session()
    before = copy.deepcopy(items)
    down = make_judge(ScriptedProvider("jev", [transient()]), ScriptedProvider("gemini", [transient()]),
                      ScriptedProvider("gemma", [transient()]))
    pool = QueuePool()
    res = _run(compact_context(items, state(), down, cfg=CFG, force=True, pool=pool, session_id="s1"))
    assert res.status == "skipped_unavailable"
    assert res.stats["retained_tokens"] >= res.stats["input_tokens"]           # nothing removed (labels only add)
    assert view_ids(res) == {i.item_id for i in items} and BIG_CONFIG in res.render()
    assert all(e.action is Action.KEEP_VERBATIM for e in res.view)
    assert items == before                                                       # input untouched
    assert res.retry_job_id and len(pool.pending(COMPACT_CONTEXT_JOB)) == 1
    assert down.metrics.counters["compaction.skipped_unavailable"] == 1
    from app.services.semantic.policy import METRICS
    assert METRICS.counters["semantic.requeues"] >= 1


def test_D2_when_down_the_last_valid_view_is_reused_plus_uncovered_items_verbatim():
    pool, items = QueuePool(), session()
    ok, _ = judge_for(fake_retention())
    first = _run(compact_context(items, state(), ok, cfg=CFG, force=True, pool=pool, session_id="s1"))
    assert first.status == "compacted"
    new = items_from_chat_messages([{"role": "user", "content": "one more thing"}], id_prefix="n")
    down = make_judge(ScriptedProvider("jev", [transient()]))
    res = _run(compact_context(items + new, state(), down, cfg=CFG, force=True, pool=pool, session_id="s1"))
    assert res.status == "skipped_unavailable" and res.used_last_valid_view
    assert BIG_CONFIG not in res.render() and "one more thing" in res.render()   # prior compaction kept, new item verbatim


def test_retry_job_recompacts_when_providers_return():
    pool, items = QueuePool(), session()
    down = make_judge(ScriptedProvider("jev", [transient()]))
    _run(compact_context(items, state(), down, cfg=CFG, force=True, pool=pool, session_id="s1"))
    [job] = pool.pending(COMPACT_CONTEXT_JOB)
    ok, _ = judge_for(fake_retention())
    assert _run(run_compaction_job(pool, job["payload"], judge=ok)) is True
    assert any(v["status"] == "compacted" for v in pool.views)


def test_raw_items_are_never_mutated_by_a_successful_compaction():
    items = session()
    before = copy.deepcopy(items)
    judge, _ = judge_for(fake_retention(default=Action.DROP))
    _run(compact_context(items, state(), judge, cfg=CFG, force=True))
    assert items == before


# --------------------------------------------------------------- triggers


def test_compaction_triggers_do_not_fire_on_tiny_events_but_do_at_boundaries():
    items = session()
    cfg = CompactionConfig(token_threshold=10 ** 9)
    assert should_compact(items, "before_model_call", cfg)[0] is False
    assert should_compact(items, "before_handoff", cfg)[0] is True
    assert should_compact(items[:2], "before_handoff", cfg)[0] is False        # too small
    assert should_compact(items, "before_model_call", CompactionConfig(token_threshold=100))[0] is True
    judge, prov = judge_for(fake_retention())
    res = _run(compact_context(items, state(), judge, cfg=cfg, trigger="before_model_call"))
    assert res.status == "skipped_below_threshold" and prov.calls == []


def test_normalizer_handles_anthropic_style_tool_blocks():
    msgs = [{"role": "assistant", "content": [{"type": "tool_use", "id": "a1", "name": "Read",
                                                 "input": {"file_path": "x.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a1", "content": "print(1)"}]}]
    items = items_from_chat_messages(msgs)
    assert [i.kind for i in items] == ["tool_call", "file_read"] and items[1].path == "x.py"
    assert len(group_units(items)) == 1


# --------------------------------------------------------------- L: handoff


def test_L_handoff_persists_run_state_and_agent_b_resumes_without_agent_a_transcript():
    pool, items = QueuePool(), session()
    records = [{"id": "b1", "kind": "BLOCKER", "body": "auth refresh test failing", "answers_id": None, "actor_agent_id": "A"},
               {"id": "b2", "kind": "BLOCKER", "body": "old, resolved", "answers_id": None, "actor_agent_id": "A"},
               {"id": "r1", "kind": "BLOCKER_RESOLVED", "body": "fixed", "answers_id": "b2", "actor_agent_id": "A"}]

    async def list_fn(p, run_id):
        return list(records)

    async def record_fn(p, **kw):
        rec = {"id": f"h{len(records)}", "answers_id": None, **kw}
        records.append(rec)
        return rec

    async def claims_fn(p, *, goal, top_k, access_scope=None):
        return [{"claim_id": "C-18", "statement": "port is 8080"}]

    st = _run(durable.build_stealth_state(pool, goal="fix auth refresh", run_id="run1", list_fn=list_fn, claims_fn=claims_fn))
    assert st.blockers == ["auth refresh test failing"]                    # resolved blocker excluded
    assert st.claims[0]["claim_id"] == "C-18"

    judge, _ = judge_for(fake_retention())
    refreshed = []

    async def refresh():
        refreshed.append(1)

    pkg = _run(durable.prepare_handoff(
        pool, execution_run_id="run1", actor_agent_id="A", target_agent_id="B", items=items, state=st, judge=judge,
        session_id="s1", note="handing off auth work", record_fn=record_fn, list_fn=list_fn, refresh_run_md=refresh))
    assert pkg.compaction.status == "compacted" and refreshed == [1]
    handoff = next(r for r in records if r["kind"] == "HANDOFF")
    assert "COMPACT_CONTEXT" in handoff["body"] and "test_auth_refresh" in handoff["body"]
    assert BIG_CONFIG not in handoff["body"]                                # bulky read is not carried over

    ctx = _run(durable.resume_context(pool, execution_run_id="run1", session_id="s1", list_fn=list_fn))
    assert ctx["open_blockers"] == ["auth refresh test failing"]
    assert ctx["pending_handoffs"][0]["to"] == "B" and "test_auth_refresh" in ctx["compact_context"]


def test_handoff_with_providers_down_still_records_durable_note_and_drops_nothing():
    pool, items = QueuePool(), session()
    recorded = []

    async def record_fn(p, **kw):
        recorded.append(kw)
        return kw

    async def list_fn(p, run_id):
        return []

    down = make_judge(ScriptedProvider("jev", [transient()]))
    pkg = _run(durable.prepare_handoff(pool, execution_run_id="run1", actor_agent_id="A", target_agent_id="B",
                                       items=items, state=state(), judge=down, session_id="s1", note="n",
                                       record_fn=record_fn, list_fn=list_fn))
    assert pkg.compaction.status == "skipped_unavailable" and "retained uncompacted" in recorded[0]["body"]
    assert BIG_CONFIG in pkg.view_text
