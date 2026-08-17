# State model

Type: grilling
Status:
Blocked by: 02

## Question

How are `state_before`, `state_after` and `state_delta` represented?

spec.md requires every meaningful episode to attempt S_before → execution → S_after. For coding, state may include repository, branch, commit, working tree status, relevant files, symbols, tests, build status, dependency state, issue/task state. It explicitly forbids snapshotting the entire world on every event, and requires relevant local state, references to immutable artifacts, deltas, hashes and temporal validity. The architecture must eventually support mobile/personal state, so state must not be hardcoded to code repositories.

The relevant existing facts:

- **No state model exists.** Nothing in the repo represents a before/after world state.
- The nearest analogues are all coding-specific and all ephemeral: `RepoSandbox` byte-fingerprinting in `htn_agent.py` to attribute `files_edited`, and `code_index.py`'s tree-sitter symbol extraction which is deliberately byte-exact and never LLM-summarized.
- Bi-temporal columns already exist on the graph tables and could carry state validity.
- `graph_ingest.py` stores up to 20 KB of raw patch text per row, which is the repo's only precedent for storing large content inline — and ticket 18 flags it as a privacy problem.

Decide:

- Is state a **snapshot** record, a **delta** record, or both? spec.md lists all three (`state_before`, `state_after`, `state_delta`), but storing all three per episode triples the write cost and creates a consistency obligation between them.
- What is the granularity — one state pair per episode, or per procedure execution, or per meaningful segment?
- How is "relevant local state" scoped? The whole point is not snapshotting the world, so something must decide what is relevant. Is it derived from the episode's touched artifacts, from the procedure's declared required-state, or supplied by the domain adapter?
- What is the immutable-artifact reference format — content hash, git object SHA, blob-store URI? Commits and blobs are already immutable and addressable; test output and build output are not.

Grill these:

- **Is a genuine world-state model needed in milestone 1, or is state actually just "the facts an applicability check reads"?** Ticket 12 needs `applicability(P, S_current)`. If the only consumer of state is the applicability check, then state should be shaped by what that check queries — not by an aspiration to represent the world.
- Domain-neutrality is the hardest constraint and the least immediately useful. What is the concrete cost of writing a coding-specific state model now and generalizing when a second domain arrives? Compare against the `tenant_id` precedent.
- Temporal validity on state is strange: state is *by definition* time-sensitive claims about the current situation (spec.md's own definition of STATE). Does that mean state *is* a claim with a short validity window, and there is no separate state table at all? Grill this — it would substantially simplify the model, and it follows directly from spec.md's own definitions.
- What is captured when state is unavailable or partial? spec.md's testing section explicitly names "missing state" and "partial state" as cases, so absence must be representable rather than assumed.
