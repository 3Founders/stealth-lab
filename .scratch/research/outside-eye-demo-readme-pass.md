# Outside-eye pass: demo.md / README.md — first-time-user & investor clarity

Founder request (2026-08-27, direct chat, not board-queued this time). Read
`demo.md` and the repo-root `README.md` as a first-time visitor would, then
checked every concrete claim in both against the actual code (grep/read, no
assumptions carried over from board entries). Scope was those two files;
three adjacent docs turned out to be load-bearing for whether either file's
claims are true, so they're included below as supporting evidence, not as
things I was asked to review independently.

## Finding 1 (critical) — README.md describes a different, superseded product

`demo.md` defines the current v0.1 product: a local-first **MCP server**
that gives a coding agent *earned memory* — ingest traces, distill
evidence-backed procedures, **refuse stale reuse with cited reasons**. That
positioning is also what `commLLM.md` (demo.md's own "companion: positioning"
pointer) states verbatim: *"a local-first MCP server that gives coding
agents earned memory... refuses reuse whose preconditions no longer hold,
with provable reasons."*

The repo-root `README.md` — the actual front door on GitHub, the first thing
a first-time visitor or investor doing technical diligence opens — describes
none of this. It's titled "Task Graph + Ontology for Workflow Intelligence"
and describes an entirely different product: a **workflow-debate platform**
(detect a bottleneck → three-model debate → Welch's-t-test replay → human
approval), with setup instructions for `workflow_db`, `backend_v2/`,
`frontend_v2/`, and a Render/Vercel deploy. Zero mention of MCP, traces,
procedures, staleness, or refusal anywhere in the file.

This isn't stale phrasing — it's a load-bearing pivot that never propagated
to the front door. `git log` confirms: the last real content commit to
`README.md` is `2ce3c7e` ("Re-added all the details"), and `demo.md`'s own
rewrite commit (`c6fcba2`) says "readme:" in its message but that edit
touched `backend/README.md`, not this file. Root `README.md` has not been
touched since the pivot at all.

**It's not just README.md.** `backend/README.md` and
`backend/README_MCP_SERVER.md` describe the *same* superseded product —
same `workflow_db` setup, same debate/decompose/apply-change-set tool
surface. I checked which of the two products is actually what
`backend/app/mcp_server/server.py` implements today (grepped every
`@server.tool()` decorator): the **8 real tools are `retrieve_precedent`,
`apply_change_set`, `propose_synthesis`, `solve_task`,
`detect_conflict_trigger`, `decompose_task`, `decide_decomposition`,
`submit_approval`** — i.e., the old workflow-debate tool surface, exactly
matching `backend/README_MCP_SERVER.md`. So those two backend docs are
*accurate to the code that runs today*; they're just describing the old
product, not the one `demo.md` says is shipping. `packaging/README.md`
(SHIP lane, current) states this honestly in passing — "8 tools over the
bi-temporal knowledge/task graph."

**Net effect for a first-time reader:** open `README.md` → get the old
product. Open `demo.md` → get a different, newer product with no link back
explaining the relationship. Nothing in either file tells you they're
describing the same repo at different points in its history. An investor
reading both back to back would reasonably conclude the team can't agree on
what it's building.

**Recommendation:** replace root `README.md`'s content with the current
positioning (done directly, see "What I changed" below — this is a
repo-root docs file, not inside any lane's owned paths, same posture CORE-A
used for SECURITY.md/DATA_STATEMENT.md). `backend/README.md` and
`backend/README_MCP_SERVER.md` are out of scope for this pass (inside
`backend/**`) but are accurate to the code that runs today, so they're not
actively misleading the way the root README is — they just need the same
"this describes the current server; the earned-memory MCP tools are not
wired in yet" framing once someone owns that file for a docs pass. Flagging
as a follow-up, not doing it here.

## Finding 2 (critical) — demo.md's headline capability isn't code-real yet

`demo.md`'s own rule (§0, first line): *"If a claim below lacks its proving
command passing on the release commit, the slice does not ship."* Capability
**C5 — "Refusal with receipts"** — is the company's actual differentiator
claim (the thing `CLAUDE.md`'s kickoff for this lane's prior task called
"the company's actual differentiator claim"). Its table row states:

> `check_procedure` → `ALLOW` / `WOULD_REFUSE` citing the exact superseded
> claim ids + changesets.

I grepped the entire repo (not just `backend/app`, everywhere) for both
identifiers:

- `check_procedure` as a Python identifier: **zero matches in `backend/`**.
  The only hit anywhere is `.scratch/freetoken-demo/capture_proxy.py`, an
  unrelated capture script.
- `WOULD_REFUSE` as a literal string: **zero matches anywhere in the
  codebase.** The only place that string exists is inside `demo.md`'s own
  example JSON block.

This doesn't mean the underlying mechanism is fake — it very much isn't.
The board shows extensive, well-tested machinery that this capability
depends on: evidence tables and capability bands (CORE-A db/24, db/30,
CORE-B capability.py), the applicability/precondition-gate cascade
(`applicability.py`, `precondition_gate.py`), failure classification and
routing (db/27, failure_handlers.py), and — most directly relevant — a real,
independently-verified live sweep (`model-decides-verification.md`, this
lane's own prior work) showing a model genuinely deciding to refuse stale
procedures with situation-specific reasoning. That sweep is real evidence
the *idea* works. But it runs inside `experiments/harness/`'s
`mcp_surface.py` — a **fixture stub built for offline/harness evaluation**,
not the production MCP server. `check_applicability()`, the function that
sweep actually calls, lives at `experiments/harness/mcp_surface.py:34`, and
nothing in `backend/app/mcp_server/server.py` calls it or anything with an
equivalent name. `commLLM.md`'s own tool table marks `check_procedure` as
`**new**` — i.e., it already discloses this isn't built. `demo.md`'s C1–C5
table just doesn't carry that same disclosure forward; C1–C4 read as
present-tense fact and C5 reads exactly the same way, with no visual
distinction for "this one's the differentiator claim and it's not wired
into the tool a user would actually call yet."

**Second, smaller instance of the same gap — C1 ("Install & boot"):** the
row states `docker compose up -d` as the install path, with a named
`pgvector/pgvector:pg15` requirement. There is **no `docker-compose.yml` (or
`.yaml`) anywhere in the repository** — checked root, `backend/`,
`packaging/`, everywhere. The ship checklist's own `[ ]` box for "Migration
chain applied clean on a throwaway container" is honestly unchecked, so the
gap isn't hidden at the checklist level — but the C1 *table row* above it
reads as an already-working command.

**Recommendation:** this is a product decision (build the thin
`check_procedure` MCP wrapper before claiming C5 ships, vs. relabel the
table to distinguish "engine exists, proven in the harness" from "reachable
by an actual agent through the actual server today") — not something a docs
pass should silently resolve either way. I added a visible, non-restructuring
note directly under the C1–C5 table (see "What I changed") rather than
editing the table's claims myself, so the gap is visible to any reader
without the doc taking a position on which fix is right.

## Finding 3 (moderate) — Apache-2.0 claimed, no LICENSE file exists

`demo.md` §2.5 (production posture, non-negotiable at ship): *"Apache-2.0 +
plain-language data statement."* `DATA_STATEMENT.md` exists (landed
2026-08-27, CORE-A). There is **no `LICENSE` file anywhere in the repo
root.** Picking and adding a license file is a legal decision, not mine to
make unilaterally — flagging it here rather than fabricating one.

## Finding 4 (bonus, found while checking demo.md's "Companion docs" link) — commLLM.md was corrupted

`demo.md` §0 points readers to `commLLM.md` as the positioning companion.
Opening it: the file was saved as **UTF-16LE** and every tool that assumes
UTF-8 (including a plain-text open) renders it as unreadable spaced-out
garbage — every letter separated by a null byte artifact. Confirmed via
`file commLLM.md` → `Unicode text, UTF-16, little-endian`.

**Fixed the outer encoding** (re-saved as UTF-8, content otherwise
untouched) — the file is now readable prose instead of unreadable noise,
which was the main problem for a reader following the link from `demo.md`.
One residual issue I did **not** attempt to fix: a number of special
characters inside the file (em-dashes, arrows, the `§`/interpunct section
markers) show signs of an *earlier*, separate corruption — likely a
UTF-8-bytes-reinterpreted-as-Latin-1-then-re-encoded round trip — that
predates the UTF-16 save and produces mojibake sequences like `çÆ`, `åÆ`,
`Çö` in place of `—`/`→`/`·`. I catalogued roughly a dozen distinct garbled
sequences and could not confidently map all of them back to their intended
character without guessing, so I left them as-is rather than risk silently
introducing wrong text into a positioning document. Whoever owns
`commLLM.md` next should regenerate the punctuation from source (or ping me
if a byte-level recovery pass is wanted — it's mechanical, just not fast to
verify safely).

## What I changed directly

- `commLLM.md`: re-saved UTF-16LE → UTF-8. Content unchanged aside from the
  encoding; residual punctuation mojibake noted above, not touched further.
- `demo.md`: added a short, clearly-labeled "Known doc-accuracy gaps" note
  directly under the C1–C5 table (finding 2 above) — doesn't alter the
  table itself, doesn't take a position on the product decision, just makes
  the gap visible to a reader instead of silent.
- `README.md` (repo root): replaced entirely. New content describes the
  actual current product truthfully at two levels — what a first-time user
  can literally run today (the 8-tool workflow-debate MCP server + the
  packaging CLI, both accurate to the code) — and what the project is
  positioned as building toward (the earned-memory v0.1 slice from
  `demo.md`), explicitly separated so neither claim is stated as the other.
  Links out to `demo.md`, `commLLM.md`, `SECURITY.md`, `DATA_STATEMENT.md`,
  `backend/README_MCP_SERVER.md`, and `packaging/README.md` rather than
  duplicating their setup detail (some of which is in `backend/**`, outside
  this lane, and I didn't want to re-derive/verify it a second time here).

This is a repo-root docs-only edit, same posture the board already used for
CORE-A's SECURITY.md/DATA_STATEMENT.md landing (no lane owns `README.md`
today). Not committed to `lane/research` — flagging for the founder to route
(own commit, or a scoped grant like CORE-B's `observations.py` exception) 
rather than assuming this lane should land it.
