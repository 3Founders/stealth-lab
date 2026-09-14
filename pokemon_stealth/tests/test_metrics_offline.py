from __future__ import annotations

from pokemon_stealth.metrics import RunMetrics, append_run, learning_curve, load_runs, summarize


def _metrics(**overrides) -> RunMetrics:
    base = dict(
        run_id="r1", condition="fresh", model="mock", mission="brock", seed=1,
        success=True, wall_time_s=1.0, llm_calls=3, input_tokens=100, output_tokens=50,
        estimated_cost_usd=0.01, macro_actions=5, timestamp="2026-01-01T00:00:00",
    )
    base.update(overrides)
    return RunMetrics(**base)


def test_append_run_creates_csv_and_jsonl(tmp_path):
    append_run(tmp_path, _metrics())
    assert (tmp_path / "runs.csv").is_file()
    assert (tmp_path / "runs.jsonl").is_file()
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "r1"


def test_append_run_is_additive_across_calls(tmp_path):
    append_run(tmp_path, _metrics(run_id="r1"))
    append_run(tmp_path, _metrics(run_id="r2"))
    runs = load_runs(tmp_path)
    assert len(runs) == 2
    assert {r["run_id"] for r in runs} == {"r1", "r2"}


def test_load_runs_returns_empty_list_when_no_file(tmp_path):
    assert load_runs(tmp_path) == []


def test_summarize_computes_success_rate_per_condition(tmp_path):
    append_run(tmp_path, _metrics(run_id="a", condition="fresh", success=True))
    append_run(tmp_path, _metrics(run_id="b", condition="fresh", success=False))
    append_run(tmp_path, _metrics(run_id="c", condition="stealth", success=True))
    runs = load_runs(tmp_path)
    summary = summarize(runs)
    assert summary["fresh"]["n_runs"] == 2
    assert summary["fresh"]["success_rate"] == 0.5
    assert summary["stealth"]["success_rate"] == 1.0


def test_summarize_computes_cost_per_success(tmp_path):
    append_run(tmp_path, _metrics(run_id="a", success=True, estimated_cost_usd=0.02))
    runs = load_runs(tmp_path)
    summary = summarize(runs)
    assert summary["fresh"]["cost_per_success"] == 0.02


def test_learning_curve_orders_by_timestamp(tmp_path):
    append_run(tmp_path, _metrics(run_id="r2", condition="stealth", mission="brock", timestamp="2026-01-02T00:00:00"))
    append_run(tmp_path, _metrics(run_id="r1", condition="stealth", mission="brock", timestamp="2026-01-01T00:00:00"))
    runs = load_runs(tmp_path)
    curve = learning_curve(runs, "brock", "stealth")
    assert [row["run_index"] for row in curve] == [1, 2]
    # r1 (earlier timestamp) should be run_index 1
    assert curve[0]["tokens"] == 150
