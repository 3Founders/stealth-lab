# Privacy and redaction

Type: grilling
Status: claimed
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
