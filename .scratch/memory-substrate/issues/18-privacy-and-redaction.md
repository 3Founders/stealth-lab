# Privacy and redaction

Type: grilling
Status:
Blocked by: 06, 09

## Question

What is the privacy pipeline for ingested traces — redaction, exclusion, sampling, retention and deletion?

spec.md is direct: agentic traces may contain extremely sensitive information. It requires secret redaction, credential/token redaction, configurable path exclusion, configurable tool exclusion, configurable event sampling, retention policy, deletion, **provenance-preserving deletion semantics**, encryption where appropriate, and local-first deployment compatibility. It requires that raw traces are not sent to an external LLM by default, and that semantic extraction be configurable with local deterministic processing preferred.

The relevant existing facts:

- **There is zero redaction, PII handling or secret scanning in this codebase.** Grepping for `redact|pii|scrub|mask_secret|secret_pattern` returns nothing in project code.
- `untrusted.py` exists and is well-reasoned, but it solves a *different* problem — prompt injection, via delimiting, an instruction preamble, pattern scanning, and capability restriction. Its `sanitize()` truncates and strips control characters and explicitly does **not** rewrite content.
- The repo already has a live instance of the problem: `graph_ingest.py` stores up to 20 KB of raw patch text per row, and `render_context(include_patches=True)` puts it straight into prompts, unscanned.
- Secrets *configuration* hygiene is good — all secrets are optional fields on one `Settings` object with `require()` failing loudly at point of use — but that is about the app's own credentials, not ingested content.
- Cost governance exists (`daily_llm_budget_usd`, per-viewer budgets, on by default), which is a useful precedent for "configurable limit enforced by default."

Decide:

- **Where does redaction happen?** Before raw persistence (safest, but destroys the raw payload spec.md wants recoverable), after raw persistence and before normalization (raw stays recoverable, but the sensitive data is on disk), or at read/export time (raw and normalized both sensitive, redaction only at the boundary)? spec.md wants raw recoverable "where privacy policy permits," which implies this is itself configurable — so the decision is really about what the default is.
- What is detected, and how? Entropy heuristics, known-token patterns (API key prefixes are largely well-known), an existing secret-scanning library, or `.gitignore`/`.env`-aware path rules? The false-negative cost is a leaked credential; the false-positive cost is corrupted memory.
- What is excluded by configuration — which paths, which tools? A `Read` of `.env` and a `Bash` command containing an inline token are different problems needing different rules.
- **Provenance-preserving deletion**: when a user deletes an episode, what happens to the claims and procedures derived from it? Cascade-delete destroys learned knowledge; leave-in-place leaves knowledge whose justification is gone. Tombstone the evidence and mark dependents unjustified? This is the hardest question in the ticket and it interacts with ticket 13's staleness rules.

Grill these:

- Redaction is the kind of safety feature that is trivially deferred and then never built, and this repo has a live precedent for exactly that (`tenant_id`, decorative for the whole life of the project). What is the **minimum that must exist before the first real trace is ingested** — as distinct from the full pipeline?
- If deterministic redaction is imperfect (it is), does that argue for redaction plus a hard local-only default, rather than redaction as the thing that makes external processing safe?
- Encryption "where appropriate": at rest via the database, per-column, or not in milestone 1? Local-first single-user deployment (ticket 09) may make this moot — or may be exactly where it matters, since a laptop is stolen more often than a server.
- Sampling is listed as a requirement, but sampling breaks episode completeness and therefore procedure mining. Is sampling actually wanted, or is *exclusion* the real requirement and sampling a performance idea that damages correctness?

## Answer

**Where redaction happens: client-side, at the collector, before transmission — not merely
"before the DB write."** Redacting on the backend still means raw secrets travel over the
network first, in tension with spec.md's own "local deterministic processing preferred"
stance. This is a real dependency on **ticket 16 (Ingestion pipeline shape)**, still open — the
collector has to be capable of running the redaction pass, which is a design requirement on 16,
not just an implementation detail here.

Redaction operates on the **parsed JSON structure** — walk the tree, pattern-match against
string leaf values, substitute within those leaves. Never regex against a raw serialized text
blob directly; that risks producing invalid JSON and breaking downstream normalization outright,
a strictly worse failure than the information loss redaction itself accepts.

**Honest limit, stated plainly rather than implied:** this is a best-effort floor for *detected*
patterns, not a guarantee. `GENERATIVE_OP_TYPES` (`untrusted.py`) is a true guarantee — a
generated op literally cannot reach existing content. Redaction has no equivalent property;
Gitleaks' own documentation admits secrets "that don't match any known pattern... will slide
past unless you add a rule for it." That is exactly why Grill 2's answer (local-only stays the
hard default regardless of redaction) is the *actual* backstop here, not an independent,
secondary consideration — it's what the system falls back on precisely because detection can't
be complete.

**Detection: layered, biased toward recall over precision, reusing a maintained pattern set.**
(1) Known-token-pattern matching first. Correction on tooling: Gitleaks' maintainer has declared
it feature-complete — future releases are security-patches-only, with active development moving
to a successor, Betterleaks. Either is a reasonable pick; the point is reusing a maintained
ruleset rather than hand-rolling regexes, not a specific-tool endorsement. Its actual approach
(regex + entropy) independently validates the layered structure proposed here. (2) Path-based
rules as a separate, complementary signal — a `Read` of `.env`/`*.pem`/`id_rsa` is sensitive by
content regardless of what pattern-matching finds, since e.g. a private key's raw bytes don't
match any known-prefix pattern at all. (3) Entropy heuristics as a secondary, more tunable
layer — real false-positive risk (commit SHAs, random fixture IDs), so it supplements the first
two rather than gating them.

**Exclusion: two separate, orthogonal, configurable axes — path and tool — both with real,
non-empty shipped defaults** (mirrors `daily_llm_budget_usd`'s on-by-default-with-a-real-value
pattern, not "off until configured"). Excluded content means full omission of the content itself
— but the event's *occurrence* (a tool was called) is still recorded, content-empty, so episode
reconstruction can later distinguish "something happened here, deliberately not kept" from
"nothing happened here." This is the actual distinction between exclusion and redaction:
exclusion is structural omission of content with the event shell kept; redaction is partial
substitution within content that's otherwise kept.

**Deletion: tombstone, using the bi-temporal pattern already established everywhere else in this
system.** When an episode is deleted, the episode row gets `t_invalid` set (`knowledge_update.py`'s
own pattern — never a hard delete). Claims/evidence citing it get flagged via `claims.py`'s
`truth_state` axis, not deleted (justification source gone is a different, weaker signal than
"this claim is now false"). What happens downstream to *procedures* built from that evidence —
deferred explicitly to **ticket 13**, stated honestly as an inference by analogy, not an existing
citation: ticket 13 discusses dependency-driven staleness generally, but never explicitly
addresses deletion as a trigger. Treat that connection as reasonable, not settled.

**Grill 1 — minimum before first real trace:** the narrow structural version only — client-side
redact-before-transmission for known-token-patterns, plus path/tool exclusion, both with real
shipped defaults, on by default. Not the full six-way nuanced pipeline. Exactly the shape of
decision the `tenant_id` history warns against deferring.

**Grill 2 — does imperfect redaction license external processing? No.** Local-only stays the hard
default regardless of redaction's presence (already spec.md's own stated rule) — redaction is
defense-in-depth for what's stored/shown locally, never the condition that unlocks sending raw
content externally.

**Grill 3 — encryption, closing the loop from ticket 09.** Blanket at-rest/in-transit is already
free via Supabase's platform default. Field-level, via `Supabase Vault`, is warranted for what
survives redaction — **but only if the Vault decryption key is stored separately from the
existing `.env`'s single, full-access `DATABASE_URL`** (confirmed: one shared credential, not
per-user sessions, per ticket 09's own resolution). If the Vault key sits in the same `.env`, a
stolen laptop yields both the DB credential and the Vault key together, and Vault provides zero
actual protection against exactly the threat it exists to address. This is a load-bearing
constraint on adopting Vault at all, not an implementation nicety to handle later.

**Grill 4 — sampling vs. exclusion:** exclusion is the real requirement, agreeing with the
ticket's own instinct. If sampling survives at all, it should mean sampling whole episodes
(accept/reject entirely), never dropping individual events within a kept episode — but stated
honestly, this doesn't dissolve the underlying cost, only relocates it: whatever episodes get
dropped are lost entirely, including potentially rare-but-important ones. A real trade-off, not
a solved problem.