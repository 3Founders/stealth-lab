"""The routing-tool activity feed: recorded, bounded, streamed, and kept off non-local callers."""
import asyncio
import inspect
import json

from app.mcp_server import activity


def _reset():
    activity._events.clear()
    activity._subscribers.clear()


def test_traced_records_result_and_keeps_signature():
    _reset()

    @activity.traced
    async def find_ways(query: str, ctx=None, top_k: int = 5) -> str:
        return "answer"

    assert list(inspect.signature(find_ways).parameters) == ["query", "ctx", "top_k"]
    assert asyncio.run(find_ways(query="q" * 500, top_k=3)) == "answer"
    ev = activity.recent()[0]
    assert ev["tool"] == "find_ways" and ev["ok"] and ev["output"] == "answer"
    assert ev["args"]["query"].startswith("q" * 300) and "+200 chars" in ev["args"]["query"]


def test_failure_is_recorded_and_reraised():
    _reset()

    @activity.traced
    async def call_model(prompt: str) -> str:
        raise ValueError("boom")

    try:
        asyncio.run(call_model(prompt="x"))
        raise AssertionError("should raise")
    except ValueError:
        pass
    ev = activity.recent()[0]
    assert not ev["ok"] and "boom" in ev["output"]


def test_output_is_cut_and_ring_is_bounded():
    _reset()
    activity.record("t", {}, "x" * (activity.MAX_OUTPUT + 5), 1, True)
    assert activity.recent()[0]["truncated"] and len(activity.recent()[0]["output"]) == activity.MAX_OUTPUT
    for _ in range(activity.MAX_EVENTS + 10):
        activity.record("t", {}, "o", 1, True)
    assert len(activity.recent()) == activity.MAX_EVENTS


def test_stream_replays_then_delivers_live():
    _reset()
    activity.record("find_ways", {}, "old", 1, True)

    async def go():
        got = []
        agen = activity.stream(0, heartbeat_s=0.05)
        got.append(await agen.__anext__())          # replay
        nxt = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.01)
        activity.record("find_ways", {}, "new", 1, True)
        got.append(await nxt)
        await agen.aclose()
        return got

    a, b = asyncio.run(go())
    assert json.loads(a.split("data: ", 1)[1])["output"] == "old"
    assert json.loads(b.split("data: ", 1)[1])["output"] == "new"
    assert not activity._subscribers


def test_a_call_shows_as_pending_while_it_runs_then_completes_in_place():
    _reset()
    gate = asyncio.Event()

    @activity.traced
    async def find_ways(query: str) -> str:
        await gate.wait()
        return "done"

    async def go():
        task = asyncio.ensure_future(find_ways(query="q"))
        await asyncio.sleep(0.01)
        mid = [dict(e) for e in activity.recent()]
        agen = activity.stream(0, heartbeat_s=0.05)
        replay = json.loads((await agen.__anext__()).split("data: ", 1)[1])
        nxt = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.01)
        gate.set()
        await task
        final = json.loads((await nxt).split("data: ", 1)[1])
        await agen.aclose()
        return mid, replay, final

    mid, replay, final = asyncio.run(go())
    assert len(mid) == 1 and mid[0]["pending"] and mid[0]["ok"] is None and mid[0]["output"] == ""
    assert replay["pending"] and replay["id"] == final["id"]
    assert not final["pending"] and final["ok"] and final["output"] == "done" and final["seq"] > replay["seq"]
    assert len(activity.recent()) == 1
    assert activity.recent(after=final["seq"]) == []  # a viewer that already has the final state is not sent it again
