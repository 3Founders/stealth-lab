"""The cache benchmark's router (cache_proxy.py) and how the runner puts it in front of each arm."""
import time

import cache_proxy as cp
import runner

OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-5-5"


def tool_step(text="ok", error=False, n=1):
    return {"messages": [{"role": "user", "content": "fix it"},
                         {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": text,
                                                       "is_error": error}] * n}]}


def test_a_small_tool_result_is_routine_and_anything_else_is_hard():
    assert cp.step_kind(tool_step("3 passed"))[0] == "routine"
    assert cp.step_kind(tool_step("Traceback ...", error=True))[0] == "hard"
    assert cp.step_kind(tool_step("x" * (cp.ROUTINE_MAX_CHARS + 10)))[0] == "hard"
    assert cp.step_kind({"messages": [{"role": "user", "content": "add a feature"}]})[0] == "hard"


def test_naive_router_switches_routine_steps_only_for_the_sessions_model():
    r = cp.Router("naive", "claude-opus", HAIKU)
    assert r.pick(OPUS, tool_step())[0] == HAIKU
    assert r.pick(OPUS, {"messages": [{"role": "user", "content": "add a feature"}]})[0] == OPUS
    assert r.pick("claude-haiku-4-5", tool_step())[0] == "claude-haiku-4-5"     # Claude Code's own side calls are left alone


def test_cache_aware_router_keeps_a_warm_large_cache_and_switches_otherwise():
    r = cp.Router("cache-aware", "claude-opus", HAIKU)
    assert r.pick(OPUS, tool_step())[0] == HAIKU                              # nothing cached yet
    r.answered(OPUS, {"status": 200, "cache_read_input_tokens": 60000})
    assert r.pick(OPUS, tool_step())[0] == OPUS                               # warm and large: stay
    r.last_session_ts = time.time() - cp.CACHE_TTL_S - 1
    assert r.pick(OPUS, tool_step())[0] == HAIKU                              # expired: switching costs nothing extra


def test_off_never_switches_and_every_k_is_a_fixed_rate(tmp_path):
    assert cp.Router("off", "claude-opus", HAIKU).pick(OPUS, tool_step())[0] == OPUS
    r = cp.Router("every:2", "claude-opus", HAIKU)
    assert [r.pick(OPUS, tool_step())[0] for _ in range(4)] == [OPUS, HAIKU, OPUS, HAIKU]


def test_router_memory_survives_a_restart(tmp_path):
    state = tmp_path / "s.json"
    r = cp.Router("cache-aware", "claude-opus", HAIKU, state)
    r.answered(OPUS, {"status": 200, "input_tokens": 5, "cache_read_input_tokens": 50000})
    again = cp.Router("cache-aware", "claude-opus", HAIKU, state)
    assert again.last_prefix_tokens == 50005 and again.pick(OPUS, tool_step())[0] == OPUS


def test_usage_is_read_off_the_stream():
    rec = {"_t0": time.monotonic()}
    cp.usage_from_sse('data: {"type":"message_start","message":{"model":"m","usage":{"input_tokens":3,'
                      '"cache_read_input_tokens":100,"cache_creation_input_tokens":7}}}\n'
                      'data: {"type":"content_block_delta"}\n'
                      'data: {"type":"message_delta","usage":{"output_tokens":42}}\n', rec)
    assert (rec["input_tokens"], rec["cache_read_input_tokens"], rec["cache_creation_input_tokens"], rec["output_tokens"]) == (3, 100, 7, 42)
    assert rec["served_model"] == "m" and "ttft_s" in rec


def test_runner_sets_each_arms_route_only_when_the_benchmark_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "LOCAL", tmp_path)
    monkeypatch.setattr(runner, "c_home", lambda: tmp_path / "home")
    monkeypatch.delenv("ABTEST_CACHE_BENCH", raising=False)
    assert "ABTEST_PROXY_ROUTE" not in runner.arm_env("A", {})
    monkeypatch.setenv("ABTEST_CACHE_BENCH", "on")
    assert runner.arm_env("A", {})["ABTEST_PROXY_ROUTE"] == "off"
    assert runner.arm_env("R", {})["ABTEST_PROXY_ROUTE"] == "naive"
    assert runner.arm_env("RC", {})["ABTEST_PROXY_ROUTE"] == "cache-aware"
    assert runner.arm_env("C", {"token": "t", "gc": "g"})["ABTEST_PROXY_ROUTE"] == "off"


def test_a_zai_switch_goes_to_zai_with_its_own_key_and_no_claude_login(monkeypatch):
    monkeypatch.setenv("Z_AI_KEY", "zk")
    url, model, headers = cp.other_provider("zai:glm-5.3-flash", {"authorization": "Bearer oauth", "anthropic-beta": "x",
                                                                   "anthropic-version": "2023-06-01"})
    assert url == cp.ZAI_UPSTREAM and model == "glm-5.3-flash"
    assert headers == {"anthropic-version": "2023-06-01", "x-api-key": "zk"}
    assert cp.other_provider("claude-haiku-5-5", {"a": "b"}) == (cp.UPSTREAM, "claude-haiku-5-5", {"a": "b"})


def test_input_counts_sent_at_the_end_of_the_stream_are_kept():
    rec = {"_t0": time.monotonic()}
    cp.usage_from_sse('data: {"type":"message_start","message":{"model":"glm","usage":{"input_tokens":0}}}\n'
                      'data: {"type":"message_delta","usage":{"input_tokens":43311,"output_tokens":23}}\n', rec)
    assert rec["input_tokens"] == 43311 and rec["output_tokens"] == 23


def test_report_prices_the_first_request_cold_and_the_rest_as_reported():
    import cache_bench_report as rep
    r = {"served_model": "claude-opus-5-5", "input_tokens": 0, "cache_read_input_tokens": 1_000_000,
         "cache_creation_input_tokens": 0, "output_tokens": 0}
    assert round(rep.request_cost(r), 4) == 0.20                  # a cache read at Opus's $0.20/M
    assert round(rep.request_cost(r, cold=True), 4) == 5.00        # the same tokens written cold, $5/M
    assert rep.price("glm-5.3-flash", 10)[0] == 0.15
