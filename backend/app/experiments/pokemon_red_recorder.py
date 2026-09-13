"""
Tiny experiment recorder for the Pokemon Red agent-memory benchmark
(raw / notes / stealth conditions vs. Brock). This is experiment
infrastructure, not a product feature -- intentionally small:

  - No database, no new service, no Docker. Every run is two plain files
    under experiments/pokemon-red/runs/: a JSON summary record and a
    JSONL chronological event log, plus one derived, always-rewritable
    experiments/pokemon-red/results/summary.csv.
  - This module does NOT touch StealthLab's event/trace/episode
    infrastructure (app/execution/recorder.py) -- that facade is
    Postgres/asyncpg-backed and durable-run-scoped by design; forcing a
    filesystem-only, no-DB experiment harness through it would mean
    faking a fake execution_run_id and a fake asyncpg connection just to
    reuse a type signature, which is not reuse, it's a shim. Nothing
    reusable there for a DB-less harness.
  - This module does NOT write to .stealth/ and does not import
    anything from app.stealth, app.db, or app.mcp_server.

INSTRUMENTATION HOOK, and why it lives here instead of in the six tools:
app/game_mcp/server.py imports `log_mcp_call` and calls it from ONE place
-- its shared `_request()` HTTP helper, which every one of the six tools
already routes through. That is the only change made to game_mcp: no
tool body, signature, or return value changes, no seventh tool is added,
and no action string is parsed/rewritten. `log_mcp_call` is called
whether or not an experiment run is active; it is a true no-op (a single
pointer-file existence check) when there isn't one, discovered by
reading a small pointer file rather than an env var, since env vars set
by a `recorder.py start` invocation cannot reach the already-running
game-mcp stdio subprocess without restarting it. The hook is wrapped in
its own try/except (belt), and app/game_mcp/server.py wraps the call
site in a second try/except (suspenders) -- a recorder bug can raise at
either layer and still never reach a real tool call or change gameplay.

WHAT IS COUNTED, and why: `game_actions` counts individual action
STRINGS passed to game_action across all calls (e.g. one
game_action(["press_a","wait_60"]) call adds 2 to game_actions, not 1)
-- this is a materially different number from an MCP call count, and
the schema keeps that distinction explicit rather than quietly
conflating "one tool call" with "one game action". game_state_calls /
game_screenshot_calls / game_saves / game_loads / game_resets count MCP
tool invocations by their real HTTP call shape (see _classify_call),
including game_load's post-fix session-routing path (GET /games/current
and GET /games are internal to that fix and are NOT counted as separate
loads -- only the actual POST /load or POST /games/{sid}/load call is).

WHAT IS DELIBERATELY NOT CAPTURED:
  - No LLM token or turn counts: Claude Code does not expose a reliable
    local count of either to this recorder, and this module does not
    guess at one from MCP call counts (an MCP call is not a model turn,
    and a game action is not a token) -- these fields are simply absent
    from the run record rather than present-and-wrong.
  - No blackout / battle-loss detection: pokemon-agent's own /state
    (pokemon_agent/memory/red.py, read_battle()) reports only
    in_battle/type/enemy-while-in-battle -- there is no battle-result,
    "player fainted", or blackout flag anywhere in its schema. Inferring
    a loss from e.g. "all party HP hit 0" plus a map change would be
    exactly the "vague state change" inference this benchmark's design
    explicitly forbids, so `blackouts` and `battle_losses` are always
    recorded as null, not guessed.
  - No conversation text, prompts, env vars, or credentials: the only
    request-body fields ever read are the specific, small, per-path keys
    named in _classify_call (a save/load "name", a reset session "name",
    the literal action-string list for /action) -- arbitrary extra keys
    in a request body are never captured, and no response body is ever
    stored (a /state response and a /screenshot's PNG bytes are never
    written to the event log).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = REPO_ROOT / "experiments" / "pokemon-red"
RUNS_DIR = EXPERIMENT_DIR / "runs"
RESULTS_DIR = EXPERIMENT_DIR / "results"
_ACTIVE_RUN_FILENAME = ".active_run"

DEFAULT_OBJECTIVE = "defeat Brock and obtain the Boulder Badge"

# event name -> run-record counter field it increments
_COUNTER_FIELDS = {
    "game_action": "game_actions",
    "game_state": "game_state_calls",
    "game_screenshot": "game_screenshot_calls",
    "game_save": "game_saves",
    "game_load": "game_loads",
    "game_reset": "game_resets",
}

_CSV_FIELDS = [
    "run_id", "condition", "model", "objective", "starting_checkpoint",
    "started_at", "ended_at", "wall_clock_seconds", "success", "failure_reason",
    "game_actions", "game_state_calls", "game_screenshot_calls",
    "game_saves", "game_loads", "game_resets",
    "blackouts", "battle_losses", "ending_checkpoint", "notes",
]

_SESSION_LOAD_RE = re.compile(r"^/games/[^/]+/load$")


class ActiveRunError(RuntimeError):
    """Raised for start-with-one-already-active / finish-with-none-active."""


# --------------------------------------------------------------------- paths


def _pointer_path() -> Path:
    """`RUNS_DIR / .active_run` computed fresh from the current global --
    NOT a precomputed constant -- so tests that monkeypatch RUNS_DIR alone
    get full isolation without also having to patch a second constant."""
    return RUNS_DIR / _ACTIVE_RUN_FILENAME


def _run_json_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def _run_jsonl_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.jsonl"


# ------------------------------------------------------------- file helpers


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False))
        f.write("\n")


def _read_active_run_id() -> Optional[str]:
    pointer = _pointer_path()
    if not pointer.exists():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        run_id = data.get("run_id")
        return run_id if isinstance(run_id, str) and run_id else None
    except Exception:
        return None


def _write_active_run_id(run_id: str) -> None:
    _atomic_write_json(_pointer_path(), {"run_id": run_id})


def _clear_active_run(run_id: str) -> None:
    """Only clears the pointer if it still names `run_id` -- never
    clobbers a different run's pointer."""
    if _read_active_run_id() != run_id:
        return
    try:
        _pointer_path().unlink()
    except FileNotFoundError:
        pass


def _record_event(run_id: str, event: str, extra: Optional[dict] = None) -> None:
    obj: dict = {"ts": _now_iso(), "event": event, "run_id": run_id}
    if extra:
        obj.update(extra)
    _append_jsonl(_run_jsonl_path(run_id), obj)


# ------------------------------------------------------------- run control


def start_run(
    condition: str,
    checkpoint: str,
    *,
    model: Optional[str] = None,
    objective: str = DEFAULT_OBJECTIVE,
    notes: Optional[str] = None,
) -> dict:
    """Start a new run. Refuses if a run is already active rather than
    silently mixing two runs' events into one pointer/log."""
    if not condition or not condition.strip():
        raise ValueError("condition is required")
    if not checkpoint or not checkpoint.strip():
        raise ValueError("checkpoint is required")

    existing = _read_active_run_id()
    if existing is not None:
        raise ActiveRunError(
            f"A run is already active (run_id={existing!r}). Finish it first "
            f"(`recorder.py finish`) before starting a new one -- refusing to "
            f"start a second overlapping run rather than silently mixing two "
            f"runs' events together."
        )

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    started_at = _now_iso()
    record: dict[str, Any] = {
        "run_id": run_id,
        "condition": condition,
        "model": model,
        "objective": objective,
        "starting_checkpoint": checkpoint,
        "started_at": started_at,
        "ended_at": None,
        "wall_clock_seconds": None,
        "success": False,
        "failure_reason": None,
        "game_actions": 0,
        "game_state_calls": 0,
        "game_screenshot_calls": 0,
        "game_saves": 0,
        "game_loads": 0,
        "game_resets": 0,
        # Never inferred -- see module docstring. pokemon-agent exposes no
        # battle-result/blackout signal to detect these from.
        "blackouts": None,
        "battle_losses": None,
        "ending_checkpoint": None,
        "notes": notes,
    }
    _atomic_write_json(_run_json_path(run_id), record)
    _record_event(run_id, "run_started", {
        "condition": condition, "checkpoint": checkpoint, "model": model,
    })
    _write_active_run_id(run_id)
    return record


def _tally_events(run_id: str) -> dict:
    counts = {field: 0 for field in _COUNTER_FIELDS.values()}
    last_save_name: Optional[str] = None
    path = _run_jsonl_path(run_id)
    if not path.exists():
        return {"counts": counts, "last_save_name": last_save_name}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = obj.get("event")
        field = _COUNTER_FIELDS.get(event)
        if field is None:
            continue
        if event == "game_action":
            counts[field] += int(obj.get("count") or 0)
        else:
            counts[field] += 1
        if event == "game_save" and obj.get("name"):
            last_save_name = obj["name"]
    return {"counts": counts, "last_save_name": last_save_name}


def finish_run(
    run_id: Optional[str] = None,
    *,
    success: bool = False,
    failure_reason: Optional[str] = None,
    ending_checkpoint: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Finish a run: tallies its event log into final counters, stamps
    ended_at/wall_clock_seconds, writes success/failure, and refreshes
    results/summary.csv. Defaults to the currently active run."""
    if run_id is None:
        run_id = _read_active_run_id()
    if run_id is None:
        raise ActiveRunError(
            "No active run to finish (no run_id given and no run is active). "
            "Start one first, or pass run_id explicitly."
        )

    run_path = _run_json_path(run_id)
    if not run_path.exists():
        raise FileNotFoundError(f"Run record not found: {run_path}")
    record = json.loads(run_path.read_text(encoding="utf-8"))

    tally = _tally_events(run_id)
    record.update(tally["counts"])

    ended_at = _now_iso()
    started_dt = datetime.fromisoformat(record["started_at"])
    ended_dt = datetime.fromisoformat(ended_at)
    wall_clock = (ended_dt - started_dt).total_seconds()

    record["ended_at"] = ended_at
    record["wall_clock_seconds"] = round(wall_clock, 3)
    record["success"] = bool(success)
    record["failure_reason"] = failure_reason
    record["ending_checkpoint"] = ending_checkpoint or tally["last_save_name"]
    if notes is not None:
        record["notes"] = notes

    _atomic_write_json(run_path, record)
    _record_event(run_id, "run_finished", {
        "success": record["success"],
        "failure_reason": failure_reason,
        "ending_checkpoint": record["ending_checkpoint"],
    })
    _clear_active_run(run_id)
    _refresh_summary_csv()
    return record


def _refresh_summary_csv() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RESULTS_DIR / "summary.csv"
    records = []
    if RUNS_DIR.exists():
        for p in sorted(RUNS_DIR.glob("*.json")):
            try:
                records.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    records.sort(key=lambda r: r.get("started_at") or "")

    tmp = csv_path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)
    tmp.replace(csv_path)
    return csv_path


# --------------------------------------------------------- game_mcp hook


def _classify_call(
    method: str, path: str, json_body: Optional[dict],
) -> Optional[tuple[str, dict]]:
    """Maps one pokemon-agent HTTP call to (event_name, extra) -- or None
    to not log it at all (e.g. the GET /games/current and GET /games
    probes game_load's session-routing fix makes internally; those are
    not separate tool calls and must not be double-counted as loads).
    Only ever plucks specific, known-safe keys out of `json_body` --
    never stores the body verbatim, so an unexpected extra key in a
    request can never leak into the event log."""
    body = json_body or {}
    if method == "GET" and path == "/state":
        return "game_state", {}
    if method == "GET" and path == "/screenshot":
        return "game_screenshot", {}
    if method == "POST" and path == "/action":
        actions = body.get("actions") or []
        return "game_action", {"count": len(actions), "actions": list(actions)}
    if method == "POST" and path == "/save":
        return "game_save", {"name": body.get("name")}
    if method == "POST" and path == "/load":
        return "game_load", {"name": body.get("name")}
    if method == "POST" and _SESSION_LOAD_RE.match(path):
        return "game_load", {"session_path": path}
    if method == "POST" and path == "/games/new":
        return "game_reset", {"name": body.get("name")}
    return None


def log_mcp_call(method: str, path: str, json_body: Optional[dict] = None) -> None:
    """Observational hook: called from game_mcp's shared `_request()` for
    every pokemon-agent HTTP call. MUST NEVER raise -- a recorder bug
    must never affect gameplay, so the whole body is one try/except. A
    true no-op (one file-existence check) whenever no run is active."""
    try:
        run_id = _read_active_run_id()
        if run_id is None:
            return
        classified = _classify_call(method, path, json_body)
        if classified is None:
            return
        event, extra = classified
        _record_event(run_id, event, extra)
    except Exception:
        pass


# ------------------------------------------------------------------- CLI


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pokemon_red_recorder",
        description="Tiny experiment recorder for the Pokemon Red agent-memory benchmark.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="Start a new run")
    p_start.add_argument("--condition", required=True, help="e.g. raw | notes | stealth")
    p_start.add_argument("--checkpoint", required=True, help="starting save-state name")
    p_start.add_argument("--model", default=None)
    p_start.add_argument("--objective", default=DEFAULT_OBJECTIVE)
    p_start.add_argument("--notes", default=None)

    p_finish = sub.add_parser("finish", help="Finish the active run")
    p_finish.add_argument("--success", action="store_true")
    p_finish.add_argument("--failure-reason", default=None)
    p_finish.add_argument("--ending-checkpoint", default=None,
                           help="Defaults to the last game_save name in this run, if any")
    p_finish.add_argument("--notes", default=None)
    p_finish.add_argument("--run-id", default=None, help="Defaults to the active run")

    sub.add_parser("status", help="Show the currently active run, if any")
    sub.add_parser("summarize", help="Rebuild results/summary.csv from all run records")
    return ap


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.cmd == "start":
        try:
            record = start_run(
                args.condition, args.checkpoint, model=args.model,
                objective=args.objective, notes=args.notes,
            )
        except (ActiveRunError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"started run {record['run_id']} "
              f"(condition={record['condition']!r}, checkpoint={record['starting_checkpoint']!r})")
        print(f"  record: {_run_json_path(record['run_id'])}")
        print(f"  events: {_run_jsonl_path(record['run_id'])}")
        return 0

    if args.cmd == "finish":
        try:
            record = finish_run(
                args.run_id, success=args.success, failure_reason=args.failure_reason,
                ending_checkpoint=args.ending_checkpoint, notes=args.notes,
            )
        except (ActiveRunError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"finished run {record['run_id']}: success={record['success']} "
              f"wall_clock_seconds={record['wall_clock_seconds']}")
        print(f"  game_actions={record['game_actions']} "
              f"game_state_calls={record['game_state_calls']} "
              f"game_screenshot_calls={record['game_screenshot_calls']} "
              f"game_saves={record['game_saves']} game_loads={record['game_loads']} "
              f"game_resets={record['game_resets']} ending_checkpoint={record['ending_checkpoint']!r}")
        return 0

    if args.cmd == "status":
        run_id = _read_active_run_id()
        print("no active run" if run_id is None else f"active run: {run_id}")
        return 0

    if args.cmd == "summarize":
        path = _refresh_summary_csv()
        print(f"wrote {path}")
        return 0

    return 1  # pragma: no cover -- argparse `required=True` already rejects this


if __name__ == "__main__":
    raise SystemExit(main())
