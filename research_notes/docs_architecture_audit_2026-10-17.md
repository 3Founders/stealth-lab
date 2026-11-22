# Docs + MCP architecture audit — corrections and findings (2026-10-17)

Verification pass over `docs/` (67 entries), `.stealth/`, `docs/legal/`, `packaging/npm/lib/library.mjs`,
`backend/app/stealth/*`, and `docs/local_vs_canonical.md`. Verified against the tree.

**Your three corrections are correct.** Two of them are already handled in the plan. Four findings below
change what should happen next, one of them materially.

---

## 1. The pilot plan already handles the flat-seat problem — the numbers are what is stale

**Answer to your open question: no structural change is needed.** `docs/pilot_plan_lead_2026-10.md`
already refuses to promise a dollar saving inside included usage, in three separate places:

- **§2:** *"The saving is against API-priced Claude Code, not a Cursor seat. On a flat seat inside its
  included usage the dollar saving can be zero even when routing works."*
- **§3:** *"Billing only if the lower bound of the savings is above zero. A flat-seat customer needs a
  different basis (see section 6)."*
- **§6:** *"**Flat seats:** the savings-share basis fails inside included usage. Options: measure a
  time-saved study with no fee; or run only where they are over their included usage or on metered/API
  access."*
- **§5 phase 0 gate:** *"Go only if the ceiling is above about 15% and the security reviewer will approve
  a one-page agreement."*

The savings-share terms are already conditional on a spend audit finding positive ceiling. **The
structure is correct. Do not rebuild it.**

**But the evidence table in §2 is from phase 1 and understates the final run.** It quotes
`HANDOFF §2.2`; §12 is newer. The differences are not cosmetic — one row reverses sign:

| Suite | Pilot §2 (phase 1) | Handoff §12 (final, 205 tasks) |
|---|---|---|
| BIRD | 45 → 44 solved, −50%, n=48 | 46 → 44 solved, **−59%**, n=50 |
| DS-1000 | 29 → 27 solved, −43% | 29 → 26 solved, **−51%** |
| IFEval | −62% | **−68%** |
| BoolQ | −89% | **−86%** |
| **OpenAPI** | **16 → 13 solved, −1%** | **16 → 18 solved, −14%** |
| Whole suite | −38% (128 tasks) | **−25% all / −49% code** (205 tasks) |

**OpenAPI is the important one: the pilot table shows it as the failure case (13/20). The final run has
it improving by two (18/20).** A pilot deck built from §2 would argue against a suite that actually got
better.

Two further stale items in §2:

- *"4 of C's misses were wrong cheap answers that passed the visible check and the test-first tests
  (bird-1028, bird-1166, ds1000-50, ds1000-831)."* §12 records **five**: DS-1000 ×3 and BIRD ×2.
- §3 says *"the whole-suite figure is −38%"* — that is the 128-task phase-1 number. The 205-task
  figures are −25% overall and −49% on code.

**Action:** refresh §2's table and the wrong-answer count from handoff §12. Do not touch §3, §5 or §6.

---

## 2. `.stealth/` in this repo is a *different schema* from the one you described

`packaging/npm/lib/library.mjs:1` documents the Node/local-agent schema exactly as you described:
`library.md`, `routing.md`, `SUMMARY.md`, `index/library.idx`, `index/terms.idx`, and
`library/solutions/<id>.diff`, with `.gitattributes merge=union`.

**This repo's actual `.stealth/` contains none of those files.** It contains:

```
.stealth/
  claims.md (53 KB)   context.md   procedures.md   implementations.md
  run.md   run.json   events.jsonl   meta.json
  index/ claims.idx  procedures.idx  implementations.idx  root.idx  run.idx  faulted.json
```

So there are **two `.stealth` schemas in play**: the Node library schema (and it is the one with the
merge-union portability property you described), and the backend/canonical schema that this repo
actually instantiates. **The merge-union behaviour, the `<3 KB` SUMMARY back-cover, and the
`library/solutions/*.diff` portability are all properties of the Node schema only.**

Consequence: anyone asked "what is `.stealth/`?" from the design docs will not recognise this repo's own
directory, and any portability or portability-by-merge argument made from `library.mjs` does not apply
to the backend path. Worth one short doc that names both.

---

## 3. Finding: the plan's blocking item 1 is drafted, not unstarted — and the block is a lawyer, not a decision

`docs/plan_2026-10_tech_transfer_targeted_repair.md` §4 item 1 says *"Decide ownership and licence of
contributed procedures (a one-page ruling)"* and §3 says *"The user has an open question: who owns the
procedures."*

**That is out of date.** `docs/legal/GLOBAL_COMMONS_TERMS.md` (144 lines) is a drafted answer. Its status
line: **"STATUS: DRAFT — REQUIRES LEGAL REVIEW. NOT YET PUBLISHED OR IN EFFECT."**

It already specifies:

- **§2 publishing is explicit.** Only `POST /v1/procedures/{id}/publish` (`services/publish.py`) makes
  anything public. It creates a **brand-new independent global candidate row**; the private row and its
  history stay put; *"Your private execution/verification history is not copied into the new global
  candidate. The published candidate starts with zero global verification evidence, deliberately… Global
  trust has to be re-earned independently."*
- **Scrubbing is specified:** `_scrub_paths` / `_scrub_value` in `publish.py`.
- **Republishing never mutates** a previously-published global row — it makes another fresh candidate.
- **Publication requires a verified, non-anonymous actor identity**; `publish_local_procedure` refuses
  without one.
- **§4** already states the contributor obligation: *"You must own or control the content you publish,
  or have the rights necessary to share it under this license (see §5)."* §5 is the licence section.

**So the design question is answered in draft. The remaining block is engaging legal review**, not a
founder decision. That is a different kind of blocker: it has an owner, a cost, and a lead time, and it
does *not* block E1, E2 or E3 — only outward-facing work.

Note also `AUDIT_REPORT.md:167` and `:225`: subprocessor **DPA/SCC status is unconfirmed** and the audit
flags it as an open item. Your "no DPA" read is right; the audit agrees and lists it as work.

---

## 4. Finding: "Verified" does not mean what the tech-transfer plan assumes

This is the most consequential thing I found, and it sits under the plan.

`docs/legal/VERIFICATION_DISCLAIMER.md` (89 lines, also **STATUS: DRAFT — REQUIRES LEGAL REVIEW**)
defines Verified against the code, and the code path is:

1. **Artifact validation** (`execution/artifact_validation.py`) — *"is the edited artifact even valid
   (e.g. does a `.py` file parse and import). **This says nothing about whether the code does what it
   claims.**"*
2. **Behavioral verification** (`execution/behavior_verification.py`, `execution/verifiers/`) — an
   **opt-in**, capability-specific check that *"only runs for capabilities that have a registered
   verifier"*, that *"can only **downgrade** an already-valid declared success"*, and where *"a
   capability with no registered verifier is simply not behaviorally checked at all (**there is no
   universal, mandatory verification gate**)."*

Three consequences:

1. **A procedure can be "Verified" on parse-and-import alone.** That is a weaker claim than the word
   implies, and the disclaimer says so honestly — but a customer reading "Verified" in a list will not
   read the disclaimer.
2. **There is no mandatory gate**, so the plan's §3 item "re-verification at the recipient" is not a
   mechanism that exists — it is a mechanism to be built, and the current default is *nothing is
   behaviourally checked*.
3. **This is the mechanism behind the 205-task failure.** Five cheap answers passed every check and were
   wrong (DS-1000 ×3, BIRD ×2). The dispatch ladder runs its own test-first check, which is a different
   path from `execution/verifiers/` — but the structural fact is the same: nothing in the current design
   makes behavioural verification mandatory.

**The plan should treat "make behavioural verification mandatory for at least the dispatched rung" as a
prerequisite, not a downstream improvement.** Right now the strongest claim the system can make is
Candidate, and the disclosure burden sits with the reader.

---

## 5. Finding: the 27 → 42 result comes with a negative result that argues against E3

`docs/stealth_library.md:7-8`, sourced to `plan_2026-10_priors_library_survey.md:17`:

> "same-repo past fixes with their real diffs lifted 'right cause' from 27 to 42 of 96 (p=0.023);
> **steps-only knowledge did nothing**; repo-first retrieval found useful Goals 22-24% of the time vs
> 10%."

Two things the plan has not absorbed:

- **Steps-only knowledge did nothing.** The win came from **diffs**. The tech-transfer plan's §3 item 3
  (abstraction into typed slots) and **E3** (≥20/30 procedures abstract to typed slots) are measuring the
  thing this evidence says is inert. E3's bar should either be re-specified around *diff portability*, or
  E3 should explicitly be a **negative-result experiment** whose expected outcome is "no gain," which is
  still worth running and worth publishing.
- **Repo-first retrieval, 22-24% vs 10%, is already a working precondition-matching proxy** and is
  weaker evidence than E4 but stronger than nothing. It should be the control arm for E4, not E4's
  successor.

---

## 6. On the MCP architecture and `local_vs_canonical.md` — your caveat is accurate

`docs/local_vs_canonical.md` says exactly what you reported: the local tier is *"a disposable,
human-readable **cache/projection**"* driven through *"the simple local MCP frontend"*
(`app/stealth/*`), and the closing line preserves the undecided choice — *"the existing `.stealth/`
design deliberately treats the files as a **projection of Postgres**, and `local_sync` as the way back."*

Two things worth recording:

- **The bridge is one-directional and explicit.** *"The only way local content enters the canonical tier
  is an explicit, per-item selective sync / publish."* Six rules keep the tiers from damaging each
  other, including: private/org rows never leave the home shard and are never attached as provenance to
  a public row; **a user's private write is never blocked by a model outage** (private falls back to
  `judge_unavailable`, public fails closed); judged Procedure identity is opt-in per adapter and is
  explicitly **off** for `local_sync`.
- **The frontend is more built than "simple."** `app/stealth/` is 16 modules — `local_sync.py` alone is
  28 KB, plus `local_sync_encrypt.py`, `local_registry.py`, `local_key_store.py`, `ongoing_sync.py`,
  `project_sync.py`, `library.py` (31 KB), `atomic.py`, `edit_ledger.py`.

Your framing "whether the database is independent is flagged there as an undecided choice" is right, and
it is the single most consequential open architecture decision in the repo — because it determines
whether the product works offline at all. It deserves to be a named decision with an owner, not a
closing sentence.

---

## 7. Summary of actions

| # | Action | Why |
|---|---|---|
| 1 | **Refresh pilot §2's table and wrong-answer count from handoff §12** | OpenAPI reverses sign (13/20 → 18/20); BIRD/DS-1000/IFEval all improve; 4 → 5 wrong answers |
| 2 | **Do not restructure the pilot's flat-seat handling** | Already correct in §2, §3, §5, §6 |
| 3 | **Rewrite the plan's item 1 from "founder decision" to "legal review of a drafted answer"** | `GLOBAL_COMMONS_TERMS.md` answers the ownership question in draft |
| 4 | **Make "behavioural verification must be mandatory for the dispatched rung" a prerequisite** | `VERIFICATION_DISCLAIMER.md` documents opt-in-only and no universal gate; this is the 5-wrong-answer mechanism |
| 5 | **Re-specify E3 as a diff-portability experiment, expected outcome "no gain"** | `stealth_library.md`: steps-only knowledge did nothing |
| 6 | **Use repo-first retrieval (22-24% vs 10%) as E4's control arm** | Stronger than a no-procedure control, already measured |
| 7 | **Write the two-`.stealth`-schema note** | `library.mjs`'s merge-union/portability properties are Node-schema only |
| 8 | **Promote the DB-independent question from a closing sentence to a named decision** | Determines whether the product works offline |