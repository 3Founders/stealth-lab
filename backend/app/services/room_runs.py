"""
A Way running inside a Room, as a presentation a person steps through.

The flow a buyer sees (docs/blinkit/presentation_rooms.md):

    paid -> /chat?room=...   the room exists, the run has NOT started
         -> the main pane asks only for the inputs the run needs
         -> they press Start -> steps run one at a time, each showing its result
         -> if a step is wrong they edit it and restart from there, paying for the steps it re-runs
         -> at the end, Ways that claim to apply afterwards are offered

Three rules this module exists to keep:

1. **A room never starts a run by itself.** Preparing and starting are separate calls, because a person who lands on
   a paid page should read what is needed before anything spends money.
2. **A run's prompts are frozen at start.** `way_runs.plan` is the snapshot the executor follows; nothing here accepts
   a prompt edit mid-run. The only thing a person may change is a step's *inputs*, which is what a restart edits.
3. **A restart is charged for what it re-runs.** The price is split per step (`cost_estimate.per_step_prices`), so a
   restart from step 3 costs steps 3..N. The purchase is re-opened rather than a new one created — the buyer already
   paid for this output and is redoing part of it.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)


class RoomRunError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _slug(goal: str) -> str:
    """The room title. `rooms.title` is a slug with a CHECK constraint, so the goal name cannot be one verbatim."""
    base = re.sub(r"[^a-z0-9]+", "-", (goal or "way").lower()).strip("-")[:56] or "way"
    return f"#{base}"


async def prepare(pool: Any, *, owner_sub: str, submission_id: str) -> dict[str, Any]:
    """Create (or find) the room for a paid Way, and return what the presentation needs. Does NOT start the run.

    Idempotent per (owner, Way): pressing Run twice must not make two rooms or two charges.
    """
    row = await pool.fetchrow(
        """SELECT g.id::text AS id, g.goal_name, g.description, g.status, g.parent_procedure_id::text AS parent
           FROM goal_submissions g WHERE g.id::text = $1""", str(submission_id))
    if row is None:
        raise RoomRunError("no such Way", 404)
    if row["status"] not in ("approved", "ai_approved"):
        raise RoomRunError(f"this Way is {str(row['status']).replace('_', ' ')}; only an approved Way runs", 409)

    existing = await pool.fetchrow(
        """SELECT id::text AS id, title, display_name FROM rooms
           WHERE owner_sub = $1 AND way_submission_id = $2::uuid AND deleted_at IS NULL""",
        str(owner_sub), str(submission_id))
    if existing is None:
        title = _slug(row.get("goal_name") or "")
        # The slug is unique per owner; a second Way with the same name gets a numeric suffix rather than failing.
        taken = {r["title"] for r in await pool.fetch(
            "SELECT title FROM rooms WHERE owner_sub = $1 AND deleted_at IS NULL", str(owner_sub))}
        candidate, n = title, 2
        while candidate in taken:
            candidate = f"{title}-{n}"
            n += 1
        from app.services import rooms as rooms_service
        from app.utils.ids import uuid7

        created = await rooms_service.create_room(pool, str(owner_sub), candidate, room_id=str(uuid7()))
        room_id = created["id"] if isinstance(created, dict) else created
        await pool.execute(
            "UPDATE rooms SET display_name = $2, way_submission_id = $3::uuid WHERE id = $1::uuid",
            str(room_id), row.get("goal_name") or candidate, str(submission_id))
        existing = {"id": str(room_id), "title": candidate, "display_name": row.get("goal_name") or candidate}

    run = await _live_run(pool, str(existing["id"]))
    return {"room_id": str(existing["id"]), "title": existing["title"],
            "display_name": existing["display_name"] or existing["title"],
            "run": run}


async def _live_run(pool: Any, room_id: str) -> Optional[dict[str, Any]]:
    row = await pool.fetchrow(
        """SELECT id::text AS id, status FROM way_runs WHERE room_id = $1::uuid
           ORDER BY started_at DESC LIMIT 1""", str(room_id))
    return {"run_id": row["id"], "status": row["status"]} if row else None


async def inputs_needed(pool: Any, *, submission_id: str) -> list[dict[str, Any]]:
    """The inputs the buyer must supply before the run can start, flattened across steps, in step order.

    Read from each step's declared `inputs` (db/226). A step with none contributes nothing, so a Way that declares
    nothing is startable at once — which is every Way that exists today.
    """
    rows = await pool.fetch(
        """SELECT step_order, title, inputs FROM goal_submission_steps
           WHERE submission_id = $1::uuid ORDER BY step_order""", str(submission_id))
    out: list[dict[str, Any]] = []
    for r in rows:
        declared = r["inputs"]
        if isinstance(declared, str):
            try:
                declared = json.loads(declared)
            except ValueError:
                declared = None
        for item in declared or []:
            if not isinstance(item, Mapping) or not item.get("key"):
                continue
            out.append({
                "step": int(r["step_order"]),
                "step_title": r["title"],
                "key": str(item["key"]),
                "label": str(item.get("label") or item["key"]),
                "required": bool(item.get("required", True)),
                "help": str(item.get("help") or ""),
            })
    return out


def missing_required(needed: list[dict[str, Any]], supplied: Mapping[str, Any]) -> list[str]:
    """Which required inputs have no value. Supplied values are keyed `f"{step}.{key}"` so two steps may share a key."""
    out = []
    for item in needed:
        if not item.get("required"):
            continue
        v = supplied.get(f"{item['step']}.{item['key']}")
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(f"{item['step']}.{item['key']}")
    return out


def inputs_for_step(supplied: Mapping[str, Any], step: int) -> dict[str, str]:
    """This step's own answers, without the `"{step}."` prefix, so a step sees `topic`, not `2.topic`.

    A blank or whitespace-only answer is dropped: it is not an answer, and passing it on would tell a step that the
    person supplied something they left empty.
    """
    prefix = f"{int(step)}."
    out: dict[str, str] = {}
    for k, v in (supplied or {}).items():
        if not isinstance(k, str) or not k.startswith(prefix) or v in (None, ""):
            continue
        text = str(v)
        if not text.strip():
            continue
        out[k[len(prefix):]] = text
    return out


def with_inputs(step: Mapping[str, Any], answers: Mapping[str, str]) -> dict[str, Any]:
    """A step's prompt with the person's answers appended, as the step's own context.

    The prompt itself is never rewritten — this is the runtime copy the executor calls with, not the stored step, so
    "prompts are frozen" still holds: what the Way *is* cannot change, but what the person *supplies* is part of
    running it.

    Answers are labelled as the person's own input, not as instructions from kell, so a step cannot be steered by
    text that happens to look like a directive.
    """
    out = dict(step)
    if not answers:
        return out
    given = "\n".join(f"- {k}: {v}" for k, v in answers.items())
    base = str(out.get("prompt") or "")
    out["prompt"] = (base + "\n\nThe person running this step supplied:\n" + given).strip()
    return out


def input_files(step: Mapping[str, Any], answers: Mapping[str, str]) -> dict[str, str]:
    """Inputs a script step should find on disk in /out, named as the author declared them.

    A value that names a filename (`report.pdf`) is written under that name with whatever the person supplied, so a
    script that expects to open `report.pdf` finds it. Values with no usable name are skipped rather than guessed at.
    """
    declared = step.get("inputs")
    if isinstance(declared, str):
        try:
            declared = json.loads(declared)
        except ValueError:
            declared = None
    files: dict[str, str] = {}
    for item in declared or []:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "")
        name = str(item.get("file") or item.get("name") or "")
        if key in answers and name and re.match(r"^[A-Za-z0-9._-]{1,120}$", name):
            files[name] = answers[key]
    return files


async def start(
    pool: Any, *, room_id: str, submission_id: str, supplied: Mapping[str, Any], owner_id: str,
) -> dict[str, Any]:
    """Begin the room's run with the person's inputs. This is the ONE place a run starts from a room.

    Separate from `prepare` on purpose: a person who lands on a paid page should read what the Way needs before
    anything spends their output, so creating the room never starts work.

    Refused when a required input is missing — a step that reads a blank produces a plausible document from nothing,
    which is the failure mode this whole flow exists to prevent. The supplied values are frozen into
    `way_runs.step_inputs` at start, so a run cannot silently change its inputs later; only a restart can, and that is
    charged for the steps it re-runs.
    """
    room = await pool.fetchrow(
        "SELECT id::text AS id, way_submission_id::text AS way FROM rooms WHERE id = $1::uuid AND deleted_at IS NULL",
        str(room_id))
    if room is None or room["way"] != str(submission_id):
        raise RoomRunError("this room is not for that Way", 404)
    if await _live_run(pool, str(room_id)) is not None:
        raise RoomRunError("this Way is already running in this room", 409)

    needed = await inputs_needed(pool, submission_id=str(submission_id))
    missing = missing_required(needed, supplied)
    if missing:
        raise RoomRunError(f"these inputs are needed first: {', '.join(missing)}", 400)

    from app.execution import way_runs

    row = await pool.fetchrow(
        "SELECT parent_procedure_id::text AS parent FROM goal_submissions WHERE id = $1::uuid", str(submission_id))
    if row is None or not row["parent"]:
        raise RoomRunError("this Way has no procedure version to run", 409)

    # `way_runs.start` runs the licence gate and binds the purchase itself (one purchase, one run), so this path does
    # not repeat either — doing it twice would bind a purchase twice and fail the second time.
    summary = "\n".join(f"{v}" for k, v in sorted(dict(supplied).items()) if isinstance(v, str) and v.strip())
    run = await way_runs.start(pool, row["parent"], owner_id=str(owner_id), input=summary, mode="headless")
    run_id = str(run.get("run_id") or run.get("id") or "")
    if run_id:
        await pool.execute(
            "UPDATE way_runs SET room_id = $2::uuid, step_inputs = $3::jsonb WHERE id = $1::uuid",
            run_id, str(room_id), json.dumps(dict(supplied)))
    return {"room_id": str(room_id), "run_id": run_id,
            "state": "running" if run_id else "failed", "run": {"run_id": run_id, "status": "running"},
            "inputs": needed}


async def readiness(pool: Any, *, room_id: str, submission_id: str) -> dict[str, Any]:
    """What the main pane should show: the inputs to ask for, or the running steps."""
    needed = await inputs_needed(pool, submission_id=submission_id)
    live = await _live_run(pool, room_id)
    if live and live["status"] not in ("cancelled",):
        return {"state": "running" if live["status"] in ("running", "waiting_human", "waiting_session")
                else live["status"], "run": live, "inputs": needed}
    if needed:
        return {"state": "needs_inputs", "run": None, "inputs": needed}
    # No declared inputs: the person still presses Start, because a run must never begin from a page they landed on.
    return {"state": "ready", "run": None, "inputs": []}


async def _step_prices(pool: Any, run: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The run's plan, priced per step, with the inputs already supplied counted as context."""
    from app.submissions.cost_estimate import per_step_prices

    plan = run.get("plan") or []
    if isinstance(plan, str):
        plan = json.loads(plan)
    supplied = run.get("step_inputs") or {}
    if isinstance(supplied, str):
        try:
            supplied = json.loads(supplied)
        except ValueError:
            supplied = {}
    # What the buyer typed is real input: it belongs in the context the estimate prices.
    as_text = "\n".join(str(v) for v in (supplied or {}).values() if isinstance(v, str))
    return per_step_prices(plan, run_input=as_text)


async def restart(
    pool: Any, *, room_id: str, from_step: int, edits: Mapping[str, Any], owner_id: Optional[str] = None,
) -> dict[str, Any]:
    """Restart the room's run from `from_step`, with the person's edits applied, paying only for what re-runs.

    Refused when:
      * there is no finished run to restart from,
      * `edits` is empty — re-running unchanged steps produces the same result for the same cost,
      * the step is out of the plan's range.

    The purchase is **re-opened, not re-bought**: the buyer paid for this output and is redoing part of it, so a new
    purchase would charge twice for one deliverable. `restarts` counts it, so a charge cannot be dodged by restarting
    in a loop, and the returned `estimate` prices only steps `from_step..N`.
    """
    from app.submissions.cost_estimate import suffix_from
    from app.services import procedure_billing

    run = await pool.fetchrow(
        """SELECT r.id::text AS id, r.status, r.plan, r.step_inputs, r.restarts, r.procedure_row_id::text AS proc
           FROM way_runs r WHERE r.room_id = $1::uuid ORDER BY r.started_at DESC LIMIT 1""", str(room_id))
    if run is None:
        raise RoomRunError("this room has no run to restart", 404)
    if run["status"] not in ("done", "failed"):
        raise RoomRunError(f"the run is {run['status']}; restart applies to a finished run", 409)
    if not edits:
        raise RoomRunError(
            "change something before restarting: re-running the same steps costs the same and produces the same result",
            409)

    plan = run["plan"] if isinstance(run["plan"], list) else json.loads(run["plan"] or "[]")
    orders = [int(s.get("order") or i + 1) for i, s in enumerate(plan)]
    if int(from_step) not in orders:
        raise RoomRunError(f"step {from_step} is not in this run (steps {orders[0]}..{orders[-1]})", 400)

    prices = await _step_prices(pool, run)
    estimate = suffix_from(prices, int(from_step))

    # The charge: the steps this restart re-runs, at the same per-step prices the page showed. Not the whole Way —
    # the earlier steps already produced good output and are not re-run — and not nothing, which is what re-opening
    # the purchase alone amounted to.
    amount_paise = int(estimate["price_high_paise"])
    purchase = await pool.fetchrow(
        """SELECT id::text AS id, buyer_user_id::text AS buyer FROM procedure_purchases
           WHERE used_by_run_id = $1::uuid AND status = 'paid' ORDER BY created_at DESC LIMIT 1""", str(run["id"]))
    if purchase is None:
        raise RoomRunError("no paid purchase is tied to this run, so a restart cannot be charged", 409)

    # Re-open the purchase so the redo can proceed. Status stays 'paid' — the buyer did pay — and the charge for the
    # redo is recorded separately, with its line item.
    await pool.execute(
        """UPDATE procedure_purchases SET consumed_at = NULL, used_by_run_id = $1::uuid
           WHERE id = $2::uuid AND status = 'paid'""", str(run["id"]), str(purchase["id"]))
    await pool.execute(
        """INSERT INTO restart_charges
               (run_id, purchase_id, buyer_user_id, from_step, steps_charged, amount_paise,
                estimate_low_paise, estimate_high_paise, edit_summary)
           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9)""",
        str(run["id"]), str(purchase["id"]), purchase["buyer"], int(from_step),
        int(estimate["steps_paid_for"]), amount_paise,
        int(estimate["price_low_paise"]), int(estimate["price_high_paise"]),
        # Never the full text the person typed: a summary is enough to audit the charge without storing their input.
        " | ".join(f"{k}: {len(str(v))} chars" for k, v in dict(edits).items())[:500])

    supplied = run["step_inputs"] if isinstance(run["step_inputs"], dict) else json.loads(run["step_inputs"] or "{}")
    merged = {**(supplied or {})}
    for k, v in dict(edits).items():
        merged[str(k)] = v

    # Keep the earlier outputs (they are still good) and drop everything from `from_step` on, so the executor
    # re-runs exactly the tail. The prompts themselves are NOT editable: `plan` is untouched.
    kept = [o for o in json.loads(await pool.fetchval(
        "SELECT outputs::text FROM way_runs WHERE id = $1::uuid", str(run["id"])) or "[]")
        if int(o.get("order") or 0) < int(from_step)]
    await pool.execute(
        """UPDATE way_runs
              SET next_step = $2, status = 'running', outputs = $3::jsonb, step_inputs = $4::jsonb,
                  restarts = restarts + 1, charged_paise = charged_paise + $5,
                  error = '', finished_at = NULL, updated_at = now()
            WHERE id = $1::uuid""",
        str(run["id"]), int(from_step), json.dumps(kept), json.dumps(merged), amount_paise)

    return {"run_id": str(run["id"]), "from_step": int(from_step), "restarts": int(run["restarts"] or 0) + 1,
            "charge_paise": amount_paise,
            "estimate": {"price_low_paise": estimate["price_low_paise"],
                         "price_high_paise": estimate["price_high_paise"],
                         "steps_paid_for": estimate["steps_paid_for"],
                         "steps": estimate["steps"]},
            "note": (f"charged {amount_paise} paise for the {estimate['steps_paid_for']} step(s) this restart re-runs")}
