# Observation layer

Type: grilling
Status: resolved
Blocked by: 01, 02

## Question

How is the observation layer between raw events and claims represented, and what produces observations?

spec.md's example chain: raw event `Edit file X` → observation `Authentication implementation was modified` → claim `User commonly validates authentication changes immediately after editing`. Observations must retain source event IDs, episode ID, extractor/model, confidence, timestamp, provenance, and extraction version — and observations are explicitly **not** automatically facts.

The relevant existing facts:

- **No observation layer exists.** Nothing in the repo interprets events semantically.
- `episodes` is effectively write-only: one writer (`loop.py`, debate transcripts only), zero readers. `episode_type` permits `document` and `trace` but nothing ever writes them.
- The closest existing analogue is `failure_capture.py`, which stores the model's own `subgoal_failed(reason)` sentence as a `knowledge_nodes` row rather than the raw trajectory — with an explicit rationale that "a 20-30 call transcript is mostly list_dir/search noise and embeds badly." That is an observation in all but name.
- `hierarchy.py` accepts a `summarizer` parameter but every caller gets `_default_summary`, a deterministic string concat. No LLM summarizer is passed anywhere in the repo.

Decide:

- Own table, `knowledge_nodes` node type, or a property on the episode? (Coordinate with 03 — the answers should be coherent, not necessarily identical.)
- What is the cardinality? One observation per event, per event group, per episode? spec.md says "a semantic interpretation of one or more events," which means the source-event link is many-to-many and needs somewhere to live.
- **What actually produces observations?** This is the ticket's sharpest question. spec.md demands deterministic local processing first and forbids making every tool call trigger an LLM call. So: which observations are derivable deterministically (files touched, tests run and their outcome, commands executed, commits made), and which genuinely need a model?
- `extraction version` is required on every observation for replay. What is versioned — the extractor code, a prompt, a model id, or all three as a compound key?

Grill these:

- Is the observation layer earning its place, or is it a layer of indirection between events and claims that a claim extractor could read events directly to avoid? What breaks if observations are skipped?
- Confidence on an observation: spec.md says justification is canonical and confidence derived. What is an observation's confidence *derived from*, when it is the first interpretive step and has no supporting evidence beneath it but the events themselves?
- If the deterministic extractors cover most of the value, does the LLM extractor belong in milestone 1 at all?

## Research findings (Brief 2 — [answers2.md](../research/answers2.md))

Not an answer; evidence for whoever resolves this. Every finding below is flagged by the source
as borrowed from an adjacent domain — **no study measures any of this for LLM-coding-agent
traces specifically**.

**04.5 — the layer earns its place, on borrowed evidence.** Process-mining event abstraction is
the studied analogue and it favours the intermediate layer: "without abstracting sequences of
events to high-level concepts, the results of applying process mining easily become very complex
and difficult to interpret" (Springer 2020 survey). Supervised and unsupervised abstraction both
recover structure that low-level logs lose. **But** no study in that literature measures error
propagation through the extra stage, and nobody has published a rule-based baseline before adding
a model for agent traces. Practical consequence: build the layer, but make it **optional and
measurable** — log raw events *and* derived observations, and evaluate downstream task
performance with and without it. That mirrors what ticket 05 concluded for its two extractors.

**04.3 — start deterministic, measure the delta.** Deterministic first (files touched, tests run,
commits made); a model only for semantic labels (intent, task type, dependency rationale). The
split ratio is unmeasured, which is itself the argument for making the model layer switchable
rather than assumed.

**04.4 — versioning is a compound key.** `(code_hash, prompt_hash, model_id, decoding_params)` —
MLflow/OpenLineage/DVC practice. One trap the findings call out explicitly: **a hosted model can
change behaviour without changing its advertised ID**, so the version key alone is insufficient.
Mitigation is to log the model's output distribution (mean logprob, entropy) over a fixed
calibration set and detect drift. Consistent with ticket 06's per-row extractor-version stamping.

**04.6 — do not store confidence at this layer.** This is the sharpest finding and it reverses
the obvious design. Raw token/sequence probabilities are **overconfident and uncalibrated**;
verbalized self-confidence is **uncorrelated with accuracy**. The literature's recommendation is
explicit: if confidence is unreliable, do not store it. If confidence is genuinely needed, use
**conformal prediction** against a calibration set and store the *prediction-set size* (1 = high
confidence, 3 = low) rather than a float — ~2ms/query overhead, finite-sample guarantees.
Self-consistency across runs is the only cheap signal with real support, and it is expensive
enough to reserve for high-stakes observations.

This agrees with the map's own standing preference — provenance and justification are canonical,
confidence is *derived* from evidence — so "no confidence float at the observation layer" is the
consistent position, not a concession.

**Inherited job from ticket 10**: this ticket owns assigning `epistemic_status`
(`observed` | `inferred`) on claims. The deterministic/model split above is exactly the line that
determines the value.

## Answer

**Representation: a dedicated `observations` table**, diverging from claims. Ticket 03 established
the *test* — does the concept share `knowledge_nodes`' access pattern? — and explicitly allowed
the test to produce different answers for different concepts. It does here, for two reasons.
First, an observation is **immutable**: it is a fixed interpretation of fixed events, re-derived
under a new extractor version rather than superseded, so the bitemporal machinery
`knowledge_nodes` would donate goes unused. Second, observations are the **highest-volume object
in the system**, and ticket 10 has just made claims high-volume too by turning state into claim
rows — piling both onto one table compounds a risk rather than reusing infrastructure. The cost
of a separate table is re-wiring visibility scoping, and that cost is near zero because
`access.py::visibility_predicate()` already takes a table alias.

**Source-event link: a dedicated `observation_events` join table.** An observation interprets one
*or more* events, so the link is many-to-many. An array of event ids on the observation row is
cheaper and avoids a join, but reverse traversal — "which observations cite this event" — then
needs a GIN index and has nowhere to carry per-link metadata. Reverse traversal is load-bearing
for the provenance invariant ("why is this in memory?"), so both-directions indexing wins.
Rejected reusing the polymorphic `edges` table: it is CHECK-constrained to the graph's node
tables, and extending its polymorphism at the highest write volume in the system trades a
purpose-built join for a general one that then has to carry the graph *and* this.

**Both extractors get built, deterministic and model-based** — the same call ticket 05 made, for
the same reason: the split is unmeasured (no study reports a rule-based baseline before adding a
model for agent traces), so the honest move is to build both and measure the delta rather than
assume one. Deterministic covers files touched, tests run, commands executed, commits made;
a model covers semantic labels — and note spec.md's own worked example ("edit file X" →
"authentication implementation was modified") is squarely semantic, so a deterministic-only layer
would not deliver what the spec asks for.

This split does double duty: it **is** ticket 10's `epistemic_status`. Deterministic extraction
produces `observed`; model extraction produces `inferred`. One decision discharges two
obligations, and the field ticket 10 added has a well-defined producer rather than a vague one.

**The layer earns its place, on borrowed evidence, and is built to be measurable.** Process-mining
event abstraction is the studied analogue and favours the intermediate layer — un-abstracted logs
become "very complex and difficult to interpret," and both supervised and unsupervised abstraction
recover structure the low-level log loses. But that literature does not measure error propagation
through the extra stage, and nobody has published the comparison for agent traces. So: keep the
layer, and log raw events *and* derived observations so the downstream delta can actually be
evaluated later rather than asserted now. Observations remain explicitly **not** facts — spec.md
is emphatic on this and nothing here changes it.

**Extraction version stamps components, not one opaque hash**:
`(extractor_name, code_version, model_id, prompt_hash, decoding_params_hash)`. A single hash is
more compact but destroys the ability to ask "which observations came from model X" — exactly the
question a replay or a post-hoc audit needs. One trap recorded rather than solved: **a hosted
model can change behaviour without changing its advertised ID**, so this key is necessary but not
sufficient. Detecting that requires monitoring output distribution over a fixed calibration set;
real work, no forcing need in milestone 1, so it goes to fog.

**No confidence field on observations.** This is the sharpest call in the ticket and it reverses
the obvious design. Raw token/sequence probabilities are overconfident and uncalibrated;
verbalized self-confidence is *uncorrelated with accuracy*. Storing an uncalibrated float does not
merely add noise — it invites every downstream consumer to treat it as signal, and the map's
standing preference is that confidence is **derived from evidence, never asserted**. So the field
is omitted rather than populated with something misleading. Self-consistency across runs is the
only cheap signal with real support and is expensive enough to reserve for high-stakes cases. If
confidence is genuinely needed later, the mechanism is conformal prediction against a calibration
set, storing *prediction-set size* rather than a float — deferred to fog.

**Provenance of this answer.** Literature-grounded: that the abstraction layer earns its place
(process-mining event abstraction), the compound version key (ML lineage practice), and the
confidence finding (LLM calibration literature). Judgement calls: the dedicated table (reasoned
from this repo's volume situation, not from any source), and the join-table choice. Explicitly
unmeasured and flagged as such: the deterministic/model split ratio — which is the argument for
making the model layer switchable rather than assumed.

### Amendment (consistency pass)

**Both new tables carry `owner_id` and `visibility`.** `observations` and `observation_events` are
new tables, and [ticket 09](09-isolation-and-auth.md) requires every new table to carry
`owner_id TEXT` *and* `visibility visibility_level NOT NULL DEFAULT 'public'` — not one alone,
because `access.py::visibility_predicate()` references `visibility` in every non-unrestricted
branch, so `owner_id`-only yields broken SQL. The answer above noted that visibility scoping had
to be "re-wired" but did not state the column requirement explicitly; stated now so it is not
rediscovered at implementation time.

**Event references point at `trace_events`.** The `observation_events` join table's event side
references [ticket 06](06-canonical-trace-model.md)'s `trace_events` table — the atomic event log
— not the pre-existing OTel-shaped `traces` table, which is a different concept that happens to
share the word.
