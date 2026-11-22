# What was built in this session, and where it improves on this record

**2026-11-23.** Scope: the purchase path and the Way page. This is not the measurement layer; the pricing,
execution and caps records in this folder are unchanged except where noted in §7.

The short version: the product is now **one output, one purchase, one run**, the payment returns to a page that
actually runs the Way, and the Way page no longer sells a licence. The measurement layer still does not exist, and
that is now more urgent, not less.

---

## 1. What is now built

| Area | Commit | What changed | Verification |
|---|---|---|---|
| One-run purchases | `c1168008` | `db/217`: `procedure_licences` → `procedure_purchases`, `consumed_at`, `used_by_run_id`, partial unique on active purchases | billing suite |
| Referral migration fix | `04746772` | restored the applied `212`, moved the rupee change to `218` | deploy booted |
| Way page + reviews | `2752439a` | buy button wired, reviews, related rail, record page | billing/reviews/public suites; tsc/build |
| Run panel dependency | `11edaa36` | committed the two frontend files the page imports | CI green |
| Rail fallback | `22a0ee44` | related Ways fall back to other published Ways | tsc |
| Catalogue paging | `0971bc1d` | `offset` + `has_more` + `next_offset`, infinite scroll | 3 public tests |
| Paid page | `8587c397` | `/way/{id}/paid`, explicit callback, `db/221` submission id | callback verified on a live link |
| Run above provenance | `1014ce16` | Run this section; provenance emptied; record page deleted | tsc/build |
| Purchased section | `ebc15c85` | the paid section **is** “Run this”; rail alignment; Elsewhere removed | tsc/build |
| CTA design | `7aec7d79` | `.kwm-cta` resets button chrome; micro-spring press per `docs/ios_animation.md` | live CSS |
| Rail type/spacing | `6f5518f5` | 16.5px names, 22px rows, 28px paid cards | tsc/build |

Live checks: hosted `/readyz` migrations ok; reviews `GET` 200 / signed-out `POST` 401; catalogue pages distinct
with `has_more` true then false; Razorpay link callback `…/way/{submission}/paid`; a real ₹1 payment captured — at
the time of the check the purchase was `paid` and unconsumed.

---

## 2. The decision that changes the record: one output, one purchase

`pricing_discussions.md` still describes a **licence** in several places. The product is not that.

- A purchase is for **one output of one version**. `way_runs.start` binds the purchase to the run
  (`bind_purchase`), the run consumes it on `done`, and a **failed or cancelled run releases it** — a failed
  attempt is never charged.
- A used or refunded purchase does not block buying another output later; the partial unique index is on
  **active** purchases only.
- The gate refuses a second output with `ProcedureAlreadyUsed`; a concurrent second run with `ProcedureInUse`.

**Why this is better than the licence:**

1. **One sale maps to one run.** That is the unit the measurement layer needs. A licence (“run it as often as you
   like”) makes the cost of a sale unbounded and the floor meaningless.
2. **Failure is free.** A run that fails releases the purchase, so the buyer is not charged for kell’s bad step.
3. **The price means something.** “₹1 for one output” can be compared with the run’s measured cost; “₹1 for
   unlimited runs” cannot.

**What it does not do:** it does not attribute model spend. `used_by_run_id` links a purchase to a run, but
`llm_spend` still has no `run_id`/`step_order` (the gap in `measurement.md`). The link makes the threading
obvious; it does not replace it.

---

## 3. The paid page, and why the old redirect was wrong

The payment link’s `callback_url` came from the browser’s `Referer`. A purchase made from `/route` returned the
buyer to `/route`, which 307s to `/task`. The fallback was worse: it built `/way/{procedure_id}` with the **parent
procedure id**, not the submission id, so it pointed at a Way that does not exist.

Now:

- `db/221` stores `submission_id` on the purchase; the callback is
  `https://www.kell.org.in/way/{submission_id}/paid`, never the referrer.
- `/way/{id}/paid` is deliberately sparse: **Do it on the website** (opens the run panel) and **Download kell**
  (keep and edit files on the device; submit Ways or procedures yourself).
- The page polls the Way until the server reports `paid` before enabling the run; the webhook is the source of
  truth and can trail the redirect.
- `db/220` caches `checkout_url`. A Razorpay link takes **~3.9s** to create; the page pre-warms on hover/focus, and
  a repeat call returns from the cache in **~0.8s**. The old button only set a busy flag and never called the
  endpoint, so it sat on “Opening payment…” forever.

This is better than the record’s path because the buyer is not sent to a page that cannot run anything, and the
run is one press away.

---

## 4. Reviews, catalogue, and the Way page

- **Reviews** (`db/219`, `services/way_reviews.py`): one person’s verdict, 1–5 plus text, one per person per Way
  (a repost edits). Public `GET`, signed-in `POST`. It lives in its own table so **a rating can never move a
  success rate**; the evidence panel is still measurement.
- **Catalogue paging** (`0971bc1d`): `GET /v1/goals/submissions/public?limit=&offset=` returns `has_more` and
  `next_offset`, ordered `(created_at DESC, id DESC)` so pages cannot overlap. The homepage and `/task` append as
  you scroll, with a keyboard-accessible button as the fallback.
- **Way page order:** Output (only when a real artefact exists) → price, which becomes **Run this** once bought →
  description → steps → history → Run this (free Ways) → Provenance (heading only) → reviews → related rail.
- **Provenance** is now an empty heading at the user’s request; the stored-record link and fingerprint were removed
  and the record page deleted. This is a product decision, not a claim that provenance is solved.

---

## 5. Where this is better than the record, stated plainly

1. **One-run purchases replace the licence.** The record’s “fixed licence per output” is now literal: one output,
   one run, one purchase, with a release on failure.
2. **The purchase carries the run id.** `used_by_run_id` is the missing link between a sale and the run whose cost
   must be measured. It is not attribution, but it is the join key.
3. **The callback is explicit.** No Referer, no `/route`, no parent-id confusion.
4. **Checkout is pre-warmed and cached.** The 4s gateway call is hidden behind hover; a repeat is ~0.8s.
5. **Reviews are separated from evidence.** The old “no comment box” rule becomes “opinion in its own table”, which
   is stronger than either a comment box or no reviews.
6. **The catalogue pages.** No fixed 12; `has_more` is answered without a count query.
7. **The paid page offers a real choice.** Browser run or desktop download, with copy that says what the desktop is
   for, instead of “start it in kell desktop” on a page that could not start anything.

---

## 6. What is still not built, and is now more urgent

| Gap | Why it matters now |
|---|---|
| `llm_spend.run_id` / `step_order`, `way_runs.cost_usd` | one sale = one run; without attribution the floor is a guess |
| measured floor / realized margin | a ₹1 sale can be below the cost of its run |
| learned caps | caps are still constants (`caps.md`) |
| sample run + thumbnails | designed in `docs/thumbnails.md`; not wired |
| PII scan/redaction for sample inputs and output frames | a sample run publishes a real artefact |
| first-run spend ceiling | a runaway first run has no bound |

The ₹1 price deserves emphasis: `pricing_discussions.md` D6 says a Way with no measured run is publishable, not
sellable. That rule is still not enforced, and with one-run purchases the cost of goods is now per sale.

---

## 7. Corrections to wording in this folder

- **“Licence” → “purchase”** in `pricing_discussions.md` §2.1, §4.3 (`may_download (licence-bound)`), and the
  “1 licence sold” line. The path already says “one paid, unconsumed purchase”; the prose should match.
- **Artifacts are built.** `execution.md` §2 is right: `produces`/`imports`, `submissions/artifacts.py`,
  `db/222_step_produces.sql` and object storage exist, and `view()` exposes artifacts. The gap is the
  `procedure_outputs` table and the Way-page projection, not the executor.
- **`output_thumbnails.py` exists** (PDF via PyMuPDF, images; Office text-only fallback). It is not wired to a
  table or to `get_public`; see `docs/thumbnails.md`.
- **The ₹1 is a price, not a measurement.** The run behind it has not been attributed; the floor is unknown.

---

## 8. Next, in order

1. **A. Attribution** — `llm_spend.run_id` + `step_order`, `way_runs.cost_usd/tokens_*`; thread the run id from
   `way_runs.start` through `call_unit`. The purchase already holds the run id.
2. **B. A sample run** — anonymized inputs + answers, kell runs once, output becomes the sample
   (`docs/thumbnails.md`).
3. **C. `procedure_outputs` + projection** — the table, `get_public`’s `output`, `similar[].thumb_url`.
4. **D. PII** — scan input, output text and frames; withhold or mask; never store the raw input.
5. **E. Floor** — measured cost × (1 + retry) as the floor; the author sets the price above it.

---

## 9. One sentence

**The product is now one output per purchase, the payment returns to a page that runs it, and the Way page sells
the run rather than a licence — which makes the missing measurement layer the next thing that must be built,
because every sale is now exactly one run whose cost nobody has recorded.**
