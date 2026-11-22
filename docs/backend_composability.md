# Making the backend representative and composable

**What the data model has to become. 2026-11-22.** Researched against FAOPs (arXiv 2610.22061, v2
2026-11-01) and the agentic-benchmark-validity literature, then checked line by line against
`backend/db/18_procedures.sql` and `backend/app/execution/procedure_graph.py`.

The question was two things that sound separate:

- **Representative** — the data should actually stand for the work it claims to represent.
- **Easy to use and compose** — anyone can plug a Way in anywhere.

They are one problem, and there is now a paper that solves exactly this one. Read the next section
before anything else, because it renames the problem in a way that makes the fixes obvious.

---

## 0. The finding that reorganises everything

**FAOPs** (JPMorgan, arXiv 2610.22061) makes the argument we have been circling, and makes it
better. It compares an agent-procedure library to a **flat file before Codd**:

| Pre-Codd flat file | Your `procedures` table today |
|---|---|
| Redundancy — every row repeats everything | Steps are prose; shared sub-workflows are **copy-pasted text** |
| Broken integrity — edit one row, miss another | Copies of a sub-Way **drift out of sync**; dependents break silently |
| Uncontrolled access — whoever reads gets all | A step names **any tool it likes**; no per-step scope |
| No optimization — every query scans | Reordering/extracting a step is **done by hand, unproven** |

And it names the five defect classes precisely: *integrity* (drift, invisible dependencies, no shared
vocabulary — `client_id` vs `id_client`), *correctness* (untyped state, a step reading a field nothing
produced), *isolation*, *composition* (no proof refactoring preserves behaviour), *governance*.

Then it does the thing nobody else does: **four normal forms**, each removing one anomaly, each
checkable by a compiler before any run.

| Normal form | Removes | Rule |
|---|---|---|
| **FNF-1 Decomposed** | compound-step anomaly — a leaf bundling several effects, so no single effect can be tested, traced, retried or priced | Every leaf performs **at most one** effect from a fixed signature |
| **FNF-2 Typed** | missing-field anomaly — a step reads a field no earlier step produced | Forward dataflow: report actual available-schema at each step, via `S_in/out/opt` |
| **FNF-3 Factored** | duplication anomaly — a shared sub-workflow copied and drifting | Every shared sub-workflow is **one canonical FAOP** the others `call(id)`; acyclic |
| **FNF-4 Scoped** | incomplete resource declaration | Each FAOP lists resources its **own** actions use **and** those its callees use; caller guardrails stay with their declarer |

The effect signature is the part that makes FNF-1 mechanical rather than a judgment call:

```
Σ_E = { op_HTTP, op_DB, op_LLM, op_tool, op_file, op_queue,
        op_ret, op_plan, op_compute, nop }
```

**`op_compute` is the key idea for your reusability requirement:** it is a leaf that does *any* amount
of data work — parse this JSON, filter these records, total these fields — but reads nothing beyond its
declared inputs, writes nothing beyond its declared outputs, and calls no model, API or database. That
is how you get atomicity without turning every pure transformation into an expensive step.

**The state rule** (their Convention 1) is exactly the contract a Way needs:

> Each FAOP runs in its own state scope, like a function call: it sees only the fields its caller passes
> in, plus the local fields it computes itself — never another FAOP's internals — and returns to the
> caller only its declared outputs.

If two steps write the same field, the later wins. That is a **defined** collision policy, which is the
thing §1.4 of `docs/KELL.md` currently leaves open.

### What this costs us: nothing, and our position improves

FAOP is published, formal, and implemented (SkillBase). **Adopting it is not invention — it is
adopting a standard, exactly as we did with STREAM's falsifier rule.** And it strengthens our position:
FAOP is a *theory of composition*. It says nothing about **evidence**. Our delta stays what it always
was — FNF gives you provable composition; kell adds verifiable outcomes. That is a much better sentence
than "we have a procedure store".

---

## 1. Where your schema actually stands

Read against `db/18_procedures.sql` and `execution/procedure_graph.py`. The columns are already there;
**what is missing is that nothing enforces or computes them.**

| FAOP requirement | Column that exists | Status today |
|---|---|---|
| `id` versioned key | `procedure_id` + `version` | ✅ and versionless refs are already refused |
| `n, d` name + description | `name`, `goal`, `display_name`, `display_description` | ✅ |
| `S_in` — fields read | `parameter_schema.slots` | ⚠️ free-form JSON, no field vocabulary |
| `S_out` — fields promised | `expected_effects` / `postconditions` | ⚠️ prose array, not a typed field set |
| body as `seq / if / loop / call(id)` | `steps` JSONB | ❌ **flat list**; keys are `action, binding, do, goal, subprocedure_ref` — no `if`, no `loop` |
| effect label per leaf | *none* | ❌ **missing entirely** — this is the FNF-1 blocker |
| `call(id)` acyclicity | `subprocedure_ref` + cycle detection | ✅ cycle net + depth cap exist |
| resource scope per step | `steps[i].binding` (kind: mcp_tool, command…) | ⚠️ storable, **never checked against what the step uses** |
| atomicity of leaves | — | ❌ a step's `do` is prose the model interprets |

**The three real gaps, in priority order:**

1. **No effect label.** Without one symbol per leaf you cannot even *state* FNF-1, let alone check it.
   This is a one-column change (`steps[i].effect`) and it is the cheapest high-value thing on this list.
2. **No typed field vocabulary.** FNF-2 needs `S_in ⊆ available`; that needs field *names*, not prose.
   Today a step can say it "needs the payment id" and nothing can check that step 1 produced one.
3. **Steps are flat, not structured.** There is no `if`/`loop`, so branching procedures are expressed
   as prose inside one step — which is precisely the compound-step anomaly FNF-1 exists to remove.

---

## 2. The representative-data half

"Representative" is a **construct-validity** question, and the benchmark literature is blunt that
agent data usually fails it.

**The strongest single finding for us:** a study of **40,285 published agent skills** found
**46.3% share a normalized name with at least one other**, and widespread *intent-level* redundancy —
the same user intent published repeatedly as separate skills. FAOP's framing of the same corpus:
~40,285 skills with heavy duplication; and a security analysis of 31,132 finds **26.1% carry at least
one vulnerability**, most often data exfiltration (13.3%) and privilege escalation (11.8%).

**That is the strongest possible empirical case for atomic reusability, and we should lead with it.**
Nearly half of published "reusable" components are duplicates of each other. The format has a
duplication problem precisely because nothing forces factoring.

The validity literature adds the constraints on the data itself:

- **ABC (Agentic Benchmark Checklist)** — many agentic benchmarks have defects in task setup or reward
  design (SWE-bench-Verified: insufficient tests; τ-bench: counts empty responses as successes), producing
  **up to 100% relative** misestimation. Applied to CVE-Bench it cut overestimation by **33%**.
- **Construct validity of coding benchmarks** (arXiv 2611.05665) — failing to *specify the target
  construct* is a substantive measurement problem; a domain label inferred from the task sample does not
  fix it; an aggregate pass rate cannot represent a capability without evidence of coverage.
- **Item-level releases** (arXiv 2604.03244 / OpenEval) — the fix is an **item-centred schema**: each
  entry is one atomic item with its content, the *adaptations* applied for a run, the response, the
  scores, and per-metric judge models. OpenEval holds 225k items / 8M item-level responses.

**So "representative" has a concrete, checkable meaning:** the stored object must let someone recover
*which items*, *under which adaptation*, *scored by which metric and judge*. Your `evidence` table
already does this better than most (it has `independence_group`, and failures count against P). What it
lacks is the **item-level** grain — an outcome is recorded per procedure version, not per task item.

---

## 3. What to build, in order

Each step is independently shippable and each unblocks the next. Numbers are what "done" means.

### Step 1 — the effect label (FNF-1)
- Add `steps[i].effect` ∈ Σ_E, defaulting to `null` (= not yet classified, never guessed).
- Write a checker that reports every leaf with ≠ 1 effect label.
- **Done when:** a migration plus a check script reports, on the live library, how many leaves are
  atomic, unlabeled, and multi-effect. You cannot manage what you have not counted.
- **Do not backfill.** New evidence: 5.5% of skill edits move no version. The same caution applies to
  retro-labelling — a guessed `op_LLM` is worse than a known `null`.

### Step 2 — atomicity of the leaf itself
- A leaf is atomic only if its `do` is one effect. Today `do` is prose.
- The pragmatic path: keep prose as the *description*, add the effect symbol as the *declaration*, and
  flag every leaf whose `binding.kind` disagrees with its declared effect (an `op_DB` leaf bound to
  `mcp_tool` is a contradiction the compiler can catch today).

### Step 3 — the typed field vocabulary (FNF-2)
- A closed vocabulary of field names — the FAOP paper's `V` — not free-text slots.
- `S_in`/`S_out` per procedure, and the forward analysis `A(b, S)` from §3.2 of the paper.
- **Done when:** a procedure that reads a field no earlier step produced is *refused*, with the
  offending step named. That single check is worth more than every prose guarantee in the spec.

### Step 4 — composition without dissolution
This is `docs/KELL.md` §1.3, and FAOP gives it a formal basis we did not have: `op_compute` and the
scoped-state convention are exactly why **a call must stay a call**, not be inlined. An inlined
sub-procedure cannot have its own `S_in`/`S_out` checked, cannot be scoped independently, and cannot be
replaced without touching the caller. Keep the graph hierarchical; flatten only at execution, carrying
`origin: <atom>@<version>, sub_step: n`.

### Step 5 — item-level outcomes
- The evidence record gains a task-item reference, so a failure names *which item*.
- Adopt ABC's checklist as the admission gate for a Way claiming a benchmark result.
- **Done when:** a claim can name its items, its adaptation, its metric and (if model-scored) its judge —
  the OpenEval shape, minus the parts that do not apply.

---

## 4. What I would not build

- **A planner.** FAOP is explicit that skill *routing* is a separate problem it does not address. We
  already have routing; do not grow the normal forms into it.
- **FNF-4 enforcement on day one.** It is the most expensive (needs a real resource graph) and the least
  urgent (it prevents a security failure, not a creativity failure). Do 1–3 first.
- **Retro-labelling the existing library.** Fresh-start is a hard rule in this repo for a good reason.

---

## 5. The one-sentence version

**Your library has the four defects of a flat file before Codd — duplication, drift, unscoped access,
no checked refactoring — and FAOP is the relational theory that fixes exactly that; adopt its normal
forms for composition, and keep evidence as our delta, because FNF makes procedures provably
composable and nothing in it makes them verifiable.**

---

## Sources

- FAOPs: Normal Forms and an Algebra for AI Agent Workflows — https://arxiv.org/html/2610.22061
- Agent Skills: A Data-Driven Analysis of Claude Skills (40,285 skills) — https://arxiv.org/pdf/2602.08004
- ABC / rigorous agentic benchmarks (NeurIPS 39) — https://dl.acm.org/doi/10.52202/085713-5547
- Construct Validity of Coding Benchmarks — https://arxiv.org/abs/2611.05665
- AI Evaluation Should Require Standardized Item-Level Data Releases — https://arxiv.org/pdf/2604.03244
- OpenEval item-centred schema — https://open-eval.com/schema.html
