# Founder rulings needed — status check (2026-08-26, CORE-A)

CLAUDE.md's kickoff for this wave asked me to write up three founder-only
rulings sitting open on the board so Anuj could clear them in one pass: D1
(capability bands), D4 (deletion mechanism), and whether `procedures` /
`agents` / `observations` should ever gain a `tenant_id` column.

Before writing tradeoffs, I checked whether D1/D4 were actually still open.
**They aren't.** Both were ratified by founder quiz on 2026-08-25 (commit
`3c1de7b`, attribution commit `ddb3894`) and are already folded into spec v4
and — for D1 — already running in code. The board's "Founder dependencies"
table (`build-board.md`) was never updated after ratification, which is why
CLAUDE.md's kickoff still described them as open. Only item 3 is a genuine
live decision. I've corrected the board table and logged this below; the
rest of this document exists mainly so the record is in one place and the
mistake doesn't repeat.

---

## 1. D1 — Capability band boundaries — RESOLVED, no action needed

**What was being asked:** where the 0–5 capability ladder's levels sit on the
continuous P(outcome | state, procedure, implementation) scale, and what P
thresholds drive auto-route / offer / refuse behavior.

**Resolution:** Option B (moderate), ratified 2026-08-25. Written verbatim
into spec v4 §16 (line 787: *"Band boundaries (D1 ratified by founder
2026-08-25)"*):

| Level | P interval | Additional gate |
|---|---|---|
| 2 reproduced | ≥ 0.50 | ≥2 independent executions |
| 3 validated | ≥ 0.70 | verification plan satisfied |
| 4 generalized | ≥ 0.85 | holds in ≥2 environments |
| 5 trusted | ≥ 0.95 | reproduction in ≥2 environments + completed review |

Routing: `P ≥ 0.90` auto-route · `0.70 ≤ P < 0.90` offer · `P < 0.70` refuse.

**Already implemented, not just decided:** CORE-B's 1.9b
(`procedure_extraction/capability.py`, board queue item 4) implements these
exact bands as named constants, explicitly labeled "RATIFIED D1 thresholds"
in the board record. CORE-B's failure-handler wiring and the status page
(SHIP's P2) both consume it. There is nothing left to rule on.

**Why the board looked open:** the "Founder dependencies" table's D1 row was
written before ratification and nobody deleted it afterward — a plain
staleness bug, not a real gap. Corrected in this session (see board Log).

---

## 2. D4 — Deletion mechanism — RESOLVED (mechanism); one sub-item is a
deliberately deferred default, not an open question

**What was being asked:** how erasure/right-to-be-forgotten reconciles with
the append-only `[H]` history invariant (§19).

**Resolution:** Option A, crypto-shredding with visible shells, ratified
2026-08-25. Written verbatim into spec v4 §34b (*"ratified by founder ruling
(D4, 2026-08-25)"*): payloads are encrypted per-scope from birth; erasure
destroys the scope key, so every row/edge/index entry survives and stays
queryable while content becomes permanently undecryptable; a tombstone
records *that* and *why*, never *what*.

**What's genuinely still open — but isn't a founder call:** field-level
encryption isn't built yet. §34b itself names this: *"until field-level
encryption ships (Band 5), payloads are necessarily stored in the clear;
erasure requests in that window fall back to tombstone-append plus payload
null-eviction, logged as known technical debt."* That's an implementation
gap for whoever enters Band 5, not a decision gap — the mechanism, and the
fact that today's tombstone approach is transitional debt against it, are
both already on paper.

**The one thing actually left deferred by the ruling itself:** key custody
(who holds organization-scope shredding keys). The ruling text answers this
too, just with an explicit "revisit later": *"company-held by default until
the Band 5 residency decision revisits customer-held KMS."* This has a
default, has a named trigger for when to revisit it (Band 5 residency), and
blocks nothing today — which matches the board's own "Blocks: Band 5.6 only"
entry. Nothing for the founder to do here now either; it's correctly parked.

**Why the board looked open:** same staleness bug as D1 — the table row
predates ratification.

---

## 3. Should `procedures` / `agents` / `observations` gain a `tenant_id`
column? — the one genuinely open question

**What's being asked, in plain terms:** the system currently enforces
isolation on two independent axes. *Visibility* (D5's scope vocabulary —
global/organization/team/project/repository/branch/user/session/task/entity)
governs which records a query is allowed to see based on where they were
scoped. *Tenancy* (HARDENING H1/H2 — `tenant_id` + `TenantScope` +
app-layer predicate + RLS backstop) governs which records belong to which
customer organization. After this wave's adoption sweep, every table that
carries actual execution/belief content — `knowledge_nodes`, `task_nodes`,
`episodes`, and the `[H]` truth tables (`evidence`, `executions`,
`change_sets`, `change_set_operations`, `failure_routes`) — is scoped on
*both* axes. Three tables are not: `procedures`, `agents`, `observations`.
They have visibility/owner_id columns but no `tenant_id` column at all, so
tenancy simply cannot be enforced there today — only the (weaker,
organization-agnostic) visibility predicate applies.

**Why it's deferred / not mine to decide:** adding it is schema work (this
lane's territory mechanically), but the actual answer depends on a product
call I can't make: is the procedure library meant to be **siloed per
customer organization**, like every other content table now is, or is it
meant to be a **shared "commons"** that multiple organizations draw from and
contribute to — closer to a cross-customer library of proven procedures?
`observations` and `agents` should inherit whatever answer `procedures`
gets, since observations feed procedure extraction and agents are the
things procedures get attributed to; there's no separate case for treating
them differently from procedures.

One thing worth being precise about, since the schema has a "Commons"
organization already seeded: that org exists purely as a **migration
compatibility shim** — H1's `db/28` reused it as the target every pre-H1
`tenant_id` value resolves to, so existing rows didn't need touching. It was
not created as a product feature for cross-tenant sharing. Its existence
shouldn't be read as an implicit answer to this question either way.

**What changes once it's answered:**
- A migration adding `tenant_id` to the three tables, additive with a
  Commons-org `DEFAULT`, following the exact `db/28`/`db/29` pattern (no
  backfill logic needed, existing rows resolve for free).
- `scope_predicates()` adoption at the remaining visibility-only call sites:
  `applicability.py` (counts over `procedures`), `agent_search.py`
  (`agents`), `observations.py`'s read paths, and the status page's
  `_visibility_call` sites (SHIP flagged this exact one-line flip in their
  P2 notes).
- H2's RLS backstop would need to decide whether these three tables join the
  five `[H]` tables under `FORCE ROW LEVEL SECURITY` via the same shared
  `sl_tenant_scope_allows()` function, or get their own policy — see
  recommendation below on why this matters.
- The WAVE-3 sweep's "honest exclusions" note (this lane's own commit
  `72191ad`) becomes closeable.

**Options:**

| Option | Shape | Cost / risk |
|---|---|---|
| A. Full tenant_id, siloed (recommended) | Same treatment as every other content table: additive column, Commons default, `scope_predicates()` everywhere, RLS via H2's existing shared function | Mechanical, low-risk, consistent; forecloses any future cross-org procedure sharing without a deliberate later feature |
| B. Leave visibility-only, commons-by-design | Explicit decision that procedures are a shared library across all tenants; document it as intentional rather than a gap | No migration cost now; but it means a customer's internal procedures (which encode their codebase, conventions, secrets-adjacent context) are visible cross-org by construction — a real exposure for an enterprise trust product, and it can't be un-shipped cheaply once customers are relying on it |
| C. Hybrid — nullable tenant_id, NULL = commons-shared | Per-row opt-in sharing; most flexible | Forks RLS enforcement into two logical paths (tenant-match vs. NULL-is-public) inside what H2 deliberately built as *one* shared policy function to avoid exactly this kind of enforcement drift; adds product surface (a publish/share action) nobody has asked for yet |

**My recommendation: Option A.** The strongest argument isn't product
preference, it's consistency with the security posture this wave just
finished building: H1/H2 exist specifically so tenancy has one enforcement
path (`scope_predicates()` app-side, `sl_tenant_scope_allows()` at the RLS
layer) with no per-table exceptions to reason about. Leaving these three
tables on visibility-only isn't a neutral no-op — it's a standing gap in
that story for exactly the tables that hold the most customer-specific
content (procedures encode a customer's actual workflows). Option C's
nullable-sharing model is the more interesting product idea if cross-org
learning is ever wanted, but it should be a deliberate opt-in feature built
later, not a default baked into the base schema now. If the founder wants
B or C instead, the sweep is small either way — CORE-A can take it as a
single next-wave item once ruled.

---

## Actions taken this session

- Corrected `build-board.md`'s "Founder dependencies" table: D1 and D4 rows
  marked resolved with pointers to the ratifying commits and spec v4
  sections; added a new row for the `tenant_id` question with a pointer to
  this document.
- Logged this write-up in the board's Log section.

No schema or app code touched, per this task's own instruction.
