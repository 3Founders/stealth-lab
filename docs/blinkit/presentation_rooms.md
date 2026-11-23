# A Way running in a Room, as a presentation

**What was built, what it does, and the two rules that keep it honest. 2026-11-23.**

A person pays for a Way, is taken to a room, and steps through the procedure one screen at a time. If a step is
wrong they redo from there — and pay only for the steps that re-run.

---

## 0. The flow

```
/way/{id}/paid                     "Payment received"
  ├─ Do it on the website ──► POST /v1/rooms/from-way  {submission_id}
  │                            creates the room, links the Way, DOES NOT START
  └─ Download kell              (unchanged)

/chat/present?room=R&way=W
  needs_inputs  ── only the inputs this Way asks for, and a Start button
  ready         ── no inputs declared: the plan and a Start button
  running       ── step N of M, each finished step's result
  done          ── the deliverable, then "Often done after this"
```

**The room never starts a run by itself.** Preparing and starting are separate calls, because the first press is the
one that spends the person's output. A room that began work the moment it was created would spend before the buyer
had read what the Way needs.

---

## 1. Layout

```
┌────┬────────────────────────────────────────────┬──────────────────────┐
│ ⟩  │  Step 2 of 5                               │ More like this       │
│    │                                            │  · Same Goal         │
│ (minimised by default)                         │  · Same publisher    │
│    │  ┌──────────────────────────────────────┐  │                      │
│    │  │  Reviewer happy with this?           │  │ ┌──────────────────┐ │
│    │  │  [ Redo from here ]                  │  │ │ Advertise your   │ │
│    │  └──────────────────────────────────────┘  │ │ product or       │ │
│    │                                            │ │ procedure        │ │
│    │  <the step's output>                       │ └──────────────────┘ │
└────┴────────────────────────────────────────────┴──────────────────────┘
```

- **Left rail minimised by default.** The procedure is what the person came for; navigation is one press away.
- **Right rail**: other Ways, then a **dashed placeholder** that says *"Advertise your product or procedure"*. It is
  labelled as not-for-sale rather than dressed as a real sponsor, because a fake ad on a page about evidence is the
  one thing kell cannot afford.
- Below 1100px the right rail hides; below 700px the left rail folds too.

---

## 2. Prompts are frozen; inputs are not

Two different things, deliberately kept apart:

| | Editable by the buyer? | Why |
|---|---|---|
| A step's **prompt** | **No** | It is what the Way *is*. Changing it makes the output something the author never verified, and the price was set for the author's steps. |
| A step's **inputs** | **Yes** | That is exactly the part a person supplies. `way_inputs.py` already stood for this; `db/226` gives it a home. |

The executor's `way_runs.plan` is the snapshot a run follows. Nothing exposes a write path to it mid-run, and a
restart does not touch it — a test asserts that no restart statement contains `plan =`.

---

## 3. Restarting from the middle, and what it costs

The price is split **per step** (`cost_estimate.per_step_prices`), and the split is exact: the steps sum to the whole
Way's price, because both come from the same unrounded accumulators (`_step_costs_exact`). That is what makes a
restart priceable at all.

```
restart from step 3 of 5
  step 1  already run   (not paid again)
  step 2  already run   (not paid again)
  step 3  re-run        ₹0.31–₹0.85   ← what this costs
  step 4  re-run        ₹0.44–₹1.10
  step 5  re-run        ₹0.52–₹1.28
```

Four rules, each a test:

1. **No edits, no restart.** Re-running unchanged steps costs the same and produces the same result, so the server
   refuses with *"change something before restarting"*.
2. **The redo is charged for the steps it re-runs** (`db/227_restart_charges.sql`). The purchase is re-opened so the
   redo can proceed — the buyer already paid for this output — and `restart_charges` records the line item: run,
   purchase, `from_step`, steps charged, amount. The owner's rule, 2026-11-23: *"however many steps he retries, he
   should be charged accordingly."*
3. **Restarts are counted** (`way_runs.restarts`) and totalled in `way_runs.charged_paise`.
4. **The tail is what re-runs.** `next_step` goes back to the restart point and `outputs` keeps everything before it.

The charge is the **same per-step price the page showed before the button was pressed**, so the number a buyer saw is
the number they pay. Only a summary of their edit is stored (`"note: 214 chars"`), never the text: an input may be
private.

### The charge is recorded, not collected

`restart_charges` is a ledger. A restart does not go through the gateway again — the buyer holds a paid purchase and
is redoing part of it. Collecting the recorded amount (a settlement run, or a top-up before the next start) is a
billing job, and it is the next decision, not an omission.


---

## 4. Per-step cost, and the frontend that shows it

`GET /v1/goals/submissions/{id}/steps/cost` returns:

```json
{"steps": [{"order": 1, "title": "Find the template", "price_low_paise": 6, "price_high_paise": 38,
            "locator_kind": "url", "produces": [], "is_human": false}, ...],
 "total_price_low_paise": 30, "total_price_high_paise": 85}
```

`GET /v1/rooms/{id}/run/restart` is refused without `edits`, and returns the tail estimate with the restart.

---

## 5. Author-declared inputs

`PUT /v1/goals/submissions/{id}/steps/{n}/inputs` with `{"inputs": [{key, label, required, help}]}`.

A step with none contributes nothing, so **every Way that exists today is startable at once** — the feature is
additive, not a gate. The prompt-only case you raised ("ask whether they want to add inputs anyway") is the
submission screen's job: a step that declares no inputs is offered an "add inputs" affordance rather than being
blocked.

---

## 6. "Applies after this"

`GET /v1/goals/submissions/{id}/related` returns `{follows, similar}`.

**There is no succession edge in the schema.** A Way does not declare that it comes after another. So `follows` is the
honest approximation — another published Way toward the same goal, or by the same author — and the page labels it
**"Often done after this"**, never "the next step". Saying more than the data supports is the failure this codebase
keeps refusing to make.

---

## 7. What is built, and what is not

| Piece | State |
|---|---|
| `db/226` — room display name, room↔run, step inputs, restart count | ✅ applied |
| `POST /v1/rooms/from-way` (does not start) | ✅, 18 tests |
| `GET /v1/rooms/{id}/run` (readiness) | ✅ |
| `POST /v1/rooms/{id}/run/restart` | ✅ |
| `GET .../steps/cost`, `PUT .../steps/{n}/inputs`, `GET .../related` | ✅ |
| `present/page.tsx` + `PresentRun.tsx` + `present.css` | ✅, tsc clean |
| Paid page → room | ✅ |
| **Start** endpoint (begin the run with the supplied inputs) | ❌ **not built** — see below |
| `POST /v1/rooms/{id}/run/start` `{submission_id, values}` | ✅ — the only place a room begins work; refuses a blank required input, refuses a second start |
| Ad placeholders | ✅ labelled placeholder only |

**Suite:** 6268 passed, 16 failed — all pre-existing. Website `tsc` clean.

---

## 8. Start, and what it guarantees

`POST /v1/rooms/{id}/run/start` is the one entry point that begins work. It refuses:

- a room that belongs to a **different Way** (404),
- a run already **running** in that room (409) — pressing Start twice must not spend twice,
- a **blank required input** (400), naming which one — a step handed a blank writes a plausible document from nothing,
- a Way with **no procedure version** (409).

`way_runs.start` runs the licence gate and binds the purchase itself, so this path does not repeat either: doing it
twice would bind one purchase twice and fail the second time.

The answers are frozen into `way_runs.step_inputs` at start and reach the step as its own context
(`room_runs.with_inputs`), labelled as *the person's input*, never as an instruction from kell. A `script` step also
finds any declared input file in `/out` (`room_runs.input_files`), so a Way that reads a PDF can open it by name.

