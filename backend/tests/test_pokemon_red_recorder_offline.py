"""
Offline tests for the Pokemon Red experiment recorder
(backend/app/experiments/pokemon_red_recorder.py). No live pokemon-agent,
no network, no database -- every test redirects the module's RUNS_DIR /
RESULTS_DIR at a pytest tmp_path so nothing here ever touches the real
experiments/pokemon-red/ directory.
"""
from __future__ import annotations

import csv
import json

import pytest

import app.experiments.pokemon_red_recorder as pr


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Every test gets its own runs/ and results/ directories -- the
    module resolves RUNS_DIR/RESULTS_DIR as globals at call time (never
    captured into a function default), so patching them here is enough;
    no separate pointer-path constant needs patching (see _pointer_path's
    own docstring)."""
    monkeypatch.setattr(pr, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pr, "RESULTS_DIR", tmp_path / "results")
    yield


# ------------------------------------------------------------- start_run


def test_start_run_creates_json_and_jsonl_with_expected_metadata():
    record = pr.start_run("raw", "pokemon_experiment_start_v2", model="claude-sonnet-5")

    assert record["condition"] == "raw"
    assert record["starting_checkpoint"] == "pokemon_experiment_start_v2"
    assert record["model"] == "claude-sonnet-5"
    assert record["objective"] == pr.DEFAULT_OBJECTIVE
    assert record["success"] is False
    assert record["ended_at"] is None
    assert record["game_actions"] == 0
    # Never inferred -- see module docstring on why these stay null.
    assert record["blackouts"] is None
    assert record["battle_losses"] is None

    run_json = pr._run_json_path(record["run_id"])
    run_jsonl = pr._run_jsonl_path(record["run_id"])
    assert run_json.exists()
    assert run_jsonl.exists()

    on_disk = json.loads(run_json.read_text(encoding="utf-8"))
    assert on_disk == record

    events = [json.loads(line) for line in run_jsonl.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "run_started"
    assert events[0]["run_id"] == record["run_id"]


def test_start_run_refuses_when_a_run_is_already_active():
    pr.start_run("raw", "ckpt-a")
    with pytest.raises(pr.ActiveRunError, match="already active"):
        pr.start_run("notes", "ckpt-b")


def test_start_run_requires_condition_and_checkpoint():
    with pytest.raises(ValueError):
        pr.start_run("", "ckpt")
    with pytest.raises(ValueError):
        pr.start_run("raw", "")


# ------------------------------------------------------- game_action counting


def test_game_action_events_are_tallied_by_action_string_count_not_call_count():
    """The critical metric: game_actions must count individual action
    strings across calls, not MCP call count -- one call with 3 actions
    plus one call with 1 action must total 4, not 2."""
    record = pr.start_run("raw", "ckpt")
    run_id = record["run_id"]

    pr.log_mcp_call("POST", "/action", {"actions": ["press_a", "wait_60", "press_start"]})
    pr.log_mcp_call("POST", "/action", {"actions": ["press_b"]})

    finished = pr.finish_run(run_id, success=True)
    assert finished["game_actions"] == 4


def test_log_mcp_call_is_a_noop_when_no_run_is_active():
    """No pointer file at all -- must not create one, must not raise."""
    pr.log_mcp_call("POST", "/action", {"actions": ["press_a"]})
    assert not pr._pointer_path().exists()
    assert list(pr.RUNS_DIR.glob("*.jsonl")) == [] if pr.RUNS_DIR.exists() else True


# -------------------------------------------------- all six call-shapes


def test_all_six_mcp_call_shapes_are_classified_and_tallied():
    record = pr.start_run("stealth", "ckpt")
    run_id = record["run_id"]

    pr.log_mcp_call("GET", "/state", None)
    pr.log_mcp_call("GET", "/screenshot", None)
    pr.log_mcp_call("POST", "/action", {"actions": ["press_a", "press_a"]})
    pr.log_mcp_call("POST", "/save", {"name": "mid_run"})
    pr.log_mcp_call("POST", "/load", {"name": "mid_run"})
    pr.log_mcp_call("POST", "/games/new", {"name": "sess"})

    finished = pr.finish_run(run_id, success=True)
    assert finished["game_state_calls"] == 1
    assert finished["game_screenshot_calls"] == 1
    assert finished["game_actions"] == 2
    assert finished["game_saves"] == 1
    assert finished["game_loads"] == 1
    assert finished["game_resets"] == 1


def test_session_scoped_load_path_counts_as_one_game_load_not_extra_calls():
    """game_load's session-routing fix makes GET /games/current and GET
    /games as internal lookups before the real POST /games/{sid}/load --
    those two probes must NOT be counted as game_state/extra loads."""
    record = pr.start_run("stealth", "ckpt")
    run_id = record["run_id"]

    pr.log_mcp_call("GET", "/games/current", None)
    pr.log_mcp_call("GET", "/games", None)
    pr.log_mcp_call("POST", "/games/sess-1/load", None)

    finished = pr.finish_run(run_id, success=True)
    assert finished["game_loads"] == 1
    assert finished["game_state_calls"] == 0
    assert finished["game_screenshot_calls"] == 0


# --------------------------------------------------------------- finish_run


def test_finish_run_computes_wall_clock_seconds():
    record = pr.start_run("raw", "ckpt")
    run_id = record["run_id"]

    # Backdate started_at so the duration is deterministic and testable
    # without a real sleep.
    run_path = pr._run_json_path(run_id)
    on_disk = json.loads(run_path.read_text(encoding="utf-8"))
    on_disk["started_at"] = "2026-01-01T00:00:00+00:00"
    run_path.write_text(json.dumps(on_disk), encoding="utf-8")

    import app.experiments.pokemon_red_recorder as pr_mod

    real_now_iso = pr_mod._now_iso
    try:
        pr_mod._now_iso = lambda: "2026-01-01T00:05:00+00:00"  # +300s
        finished = pr.finish_run(run_id, success=True)
    finally:
        pr_mod._now_iso = real_now_iso

    assert finished["wall_clock_seconds"] == 300.0
    assert finished["ended_at"] == "2026-01-01T00:05:00+00:00"


def test_finish_run_records_success_and_failure_reason():
    r1 = pr.start_run("raw", "ckpt")
    finished = pr.finish_run(r1["run_id"], success=True)
    assert finished["success"] is True
    assert finished["failure_reason"] is None

    r2 = pr.start_run("notes", "ckpt")
    finished2 = pr.finish_run(r2["run_id"], success=False, failure_reason="ran out of time")
    assert finished2["success"] is False
    assert finished2["failure_reason"] == "ran out of time"


def test_finish_run_defaults_ending_checkpoint_to_last_save_name():
    record = pr.start_run("raw", "ckpt")
    run_id = record["run_id"]
    pr.log_mcp_call("POST", "/save", {"name": "mid_point"})
    pr.log_mcp_call("POST", "/save", {"name": "final_before_brock"})

    finished = pr.finish_run(run_id, success=True)
    assert finished["ending_checkpoint"] == "final_before_brock"


def test_finish_run_explicit_ending_checkpoint_overrides_last_save():
    record = pr.start_run("raw", "ckpt")
    run_id = record["run_id"]
    pr.log_mcp_call("POST", "/save", {"name": "auto_detected"})

    finished = pr.finish_run(run_id, success=True, ending_checkpoint="manual_override")
    assert finished["ending_checkpoint"] == "manual_override"


def test_finish_run_clears_the_active_run_pointer():
    record = pr.start_run("raw", "ckpt")
    assert pr._read_active_run_id() == record["run_id"]
    pr.finish_run(record["run_id"], success=True)
    assert pr._read_active_run_id() is None


def test_finish_run_raises_when_nothing_is_active():
    with pytest.raises(pr.ActiveRunError, match="No active run"):
        pr.finish_run()


def test_finish_run_appends_run_finished_event():
    record = pr.start_run("raw", "ckpt")
    run_id = record["run_id"]
    pr.finish_run(run_id, success=True)

    events = [json.loads(l) for l in pr._run_jsonl_path(run_id).read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "run_finished"
    assert events[-1]["success"] is True


# ------------------------------------------------------------------ CSV


def test_finish_run_writes_a_valid_summary_csv_row():
    record = pr.start_run("raw", "pokemon_experiment_start_v2")
    pr.finish_run(record["run_id"], success=True)

    csv_path = pr.RESULTS_DIR / "summary.csv"
    assert csv_path.exists()
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["run_id"] == record["run_id"]
    assert rows[0]["condition"] == "raw"
    assert rows[0]["starting_checkpoint"] == "pokemon_experiment_start_v2"


def test_summary_csv_accumulates_across_multiple_finished_runs():
    r1 = pr.start_run("raw", "ckpt")
    pr.finish_run(r1["run_id"], success=True)
    r2 = pr.start_run("stealth", "ckpt")
    pr.finish_run(r2["run_id"], success=False, failure_reason="lost to Brock")

    with (pr.RESULTS_DIR / "summary.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert {row["run_id"] for row in rows} == {r1["run_id"], r2["run_id"]}


# -------------------------------------------------------------- no secrets


def test_classify_call_only_extracts_known_safe_keys_never_the_whole_body():
    """An unexpected key in a request body (a stand-in for something that
    should never end up in a log file) must never be captured -- only the
    specific per-path fields _classify_call names are ever plucked out."""
    event, extra = pr._classify_call(
        "POST", "/save", {"name": "slot1", "secret_field": "should-not-appear"},
    )
    assert event == "game_save"
    assert extra == {"name": "slot1"}
    assert "secret_field" not in extra

    event2, extra2 = pr._classify_call(
        "POST", "/action",
        {"actions": ["press_a"], "api_key": "sk-should-not-appear"},
    )
    assert event2 == "game_action"
    assert "api_key" not in extra2
    assert set(extra2) == {"count", "actions"}


def test_no_response_body_or_screenshot_bytes_are_ever_logged():
    """log_mcp_call's signature only ever takes the request method/path/
    body -- it has no parameter for a response, so a /state JSON body or
    a /screenshot PNG can never reach the event log through this hook."""
    import inspect
    sig = inspect.signature(pr.log_mcp_call)
    assert list(sig.parameters) == ["method", "path", "json_body"]


def test_unclassified_call_is_not_logged_at_all():
    record = pr.start_run("raw", "ckpt")
    run_id = record["run_id"]
    pr.log_mcp_call("GET", "/saves", None)  # not one of the six tool call shapes
    pr.log_mcp_call("GET", "/health", None)

    events = [json.loads(l) for l in pr._run_jsonl_path(run_id).read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in events] == ["run_started"]


# ------------------------------------------------------- failure containment


def test_log_mcp_call_never_raises_even_with_a_corrupt_pointer_file(monkeypatch):
    pr.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    pr._pointer_path().write_text("not valid json {{{", encoding="utf-8")
    pr.log_mcp_call("POST", "/action", {"actions": ["press_a"]})  # must not raise


def test_log_mcp_call_never_raises_when_runs_dir_is_unwritable(monkeypatch, tmp_path):
    record = pr.start_run("raw", "ckpt")
    # Point RUNS_DIR somewhere that can't be written to mid-run.
    monkeypatch.setattr(pr, "RUNS_DIR", tmp_path / "does" / "not" / "exist" / "nested")
    # Pointer now unreadable at the new (nonexistent) location -> treated
    # as "no active run", which is a safe, silent no-op.
    pr.log_mcp_call("POST", "/action", {"actions": ["press_a"]})  # must not raise


def test_log_mcp_call_never_raises_on_malformed_json_body():
    pr.start_run("raw", "ckpt")
    pr.log_mcp_call("POST", "/action", {"actions": "not-a-list-but-a-string"})  # must not raise
    pr.log_mcp_call("POST", "/action", None)  # must not raise
    pr.log_mcp_call("POST", "/action", {})  # must not raise


# -------------------------------------------------------------------- CLI


def test_cli_start_then_finish_round_trip(capsys):
    rc = pr.main(["start", "--condition", "recorder-smoke", "--checkpoint", "ckpt"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "started run" in out

    rc2 = pr.main(["finish", "--success"])
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "success=True" in out2


def test_cli_status_reports_no_active_run_then_active_run():
    rc = pr.main(["status"])
    assert rc == 0

    pr.start_run("raw", "ckpt")
    rc2 = pr.main(["status"])
    assert rc2 == 0


def test_cli_finish_without_a_start_reports_error_not_traceback(capsys):
    rc = pr.main(["finish", "--success"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "No active run" in err
