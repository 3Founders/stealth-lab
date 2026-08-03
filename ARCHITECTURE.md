# Architecture — agentic literature graph

Scope: claims made in agent/LLM research. Not all of science. Draft, for editing.

## Nodes

| Type | Is |
|---|---|
| Paper | one document. arXiv / open access only. |
| Method | a named approach — ReAct, Reflexion, ToT |
| Benchmark | a suite — SWE-bench, GAIA, τ-bench, OSWorld |
| TaskInstance | one item inside a suite. This is the micro-eval. |
| Scaffold | the harness the method ran inside |
| Model | base LLM, versioned |
| Claim | one examinable proposition. The atom. |
| Result | a Claim of type `measurement` |
| Artifact | repo, dataset, container image |

## The atom is a claim, not a paper

A paper is a container. The claim is what gets verified, disputed, superseded,
cited and reused, so it is the thing that needs an address.

The atom is **not the sentence as written**. Sentences hedge, use anaphora, carry
two claims in one clause, and split one claim across three. The node is a
normalised proposition; the sentence hangs off it as evidence.

| Field | |
|---|---|
| `text` | one proposition. No anaphora. Hedge made explicit. |
| `source` | paper + character span. Always recoverable. |
| `qualifiers` | scope conditions, as structured fields not prose |
| `claim_type` | measurement / method / interpretation / limitation |
| `status` | the ladder below |
| `strength` | asserted / hedged / speculative — signal, not noise |

`claim_type` decides adjudication. Measurements get re-run; interpretations go to
the panel.

Not every sentence is a claim. Background, motivation and related work are text.
Something has to classify, and false positives pollute the graph.

## A claim carries its conditions

`qualifiers` is the field that matters, and the same principle governs Results.

A Result is **not** `(method, benchmark) → score`. It is
`(method, scaffold, model, benchmark_version, config) → score`.

GAIA moves 30–50 points on scaffold alone. A graph that drops the scaffold stores
a number that means nothing. "ReAct improves accuracy 10%", atomised without which
model, which benchmark and which baseline, is confident nonsense. Atomising
without qualifiers is worse than not atomising.

Prior art: nanopublications, ~2010, life sciences — this idea with a name. It
never reached adoption because authors wouldn't author them and no consumer
appeared. We extract rather than ask, and the consumer is our own router.

## Edges

`REPORTS` (paper→result) · `EVALUATED_ON` (result→benchmark) · `RAN_IN`
(result→scaffold) · `RAN_ON` (result→model) · `SUPERSEDES` · `DISPUTES` ·
`REPRODUCES` · `FAILS_TO_REPRODUCE` · `CITES`

## Admission

Every Result carries a status. Computed, never asserted.

| Status | Requires |
|---|---|
| `reported` | extracted from a paper. The default. |
| `reproducible` | carries model id+version, scaffold id+version, benchmark version, decoding params, seed |
| `reproduced` | someone re-ran and matched |
| `disputed` | a `FAILS_TO_REPRODUCE` or `DISPUTES` edge exists |

Expected headline finding: most results never leave `reported`.

## Adjudication

| Claim | Settled by |
|---|---|
| "M scores S on B" | re-execution |
| "the gap between M1 and M2 is real" | Welch + Benjamini-Hochberg (existing Layer 2) |
| "M is the better approach" | debate panel + human approval |

Only the first is cheap. The third is where the existing debate engine earns its
place.

## Temporal

SOTA is a validity window, not a flag. `SUPERSEDES` closes the previous holder's
window. "Who held SOTA on B in March, and what displaced them" is a query.

## Maps onto the existing schema

- Paper, Method, Benchmark, Model, Scaffold → `knowledge_nodes`
- TaskInstance → `task_nodes` (has an interface and a success criterion)
- Result → `knowledge_nodes`, status in `properties`
- Everything else → `edges`
- `provenance = prior_library` for ingested literature
- Retrieval: existing hybrid search + bounded expansion, unchanged

No new storage engine. New node types and one status field.

## Ingestion

`acquire → parse → normalise → episode → extract → resolve → review → graph`

**Acquire.** arXiv bulk (S3, requester-pays) plus OAI-PMH for metadata. Filter
hard before anything expensive runs: a keyword prefilter on the chosen benchmark
names cuts cs.AI/CL/LG down to an affordable corpus.

**Parse.** Take the LaTeX source, not the PDF. Sections, tables, captions,
citations and floats are explicit in source and have to be guessed from a PDF.
ar5iv/LaTeXML already publishes HTML for much of arXiv. PDF is the fallback path
only — GROBID for structure, MinerU or Nougat for the hard ones. That fallback is
the same table-extraction problem as the PDF→Excel work; build it once.

**Normalise.** Sections, paragraphs, sentences with character offsets, tables as
data, references resolved to arXiv id / DOI.

**Episode.** Raw file *and* parsed document both land in `episodes`, never
summarised away. This is the layer that makes re-extraction possible.

**Extract.** The LLM step. Currently unvalidated, and the whole game. Every claim
records `extractor_version`.

**Resolve.** Surface forms to canonical nodes. This is the hard part, not parsing.
`GPT-4` / `gpt-4-0613`; SWE-bench vs SWE-bench Verified vs SWE-bench Lite are
different benchmarks that papers routinely conflate.

**Review.** Nothing enters as `reproducible` or above without passing the
admission check. Sampled human review on the rest.

Re-extraction is supersession, not migration. Extraction will improve; the raw
episode stays, the extractor is versioned, and a better pass supersedes old claims
with history intact.

Licensing: arXiv metadata is open, full text is per-paper and often not CC. Store
offsets and short quotes rather than redistributed full text unless the licence is
clear.

## Entity authority

Resolution is the hard part, so don't invent vocabulary where one already exists.

| Type | Authority |
|---|---|
| Paper | arXiv id / DOI / OpenAlex |
| Model | Hugging Face model id, else Wikidata QID |
| Institution | ROR |
| Benchmark | **ours** — no authority exists |
| Method | **ours** — no authority exists |
| Scaffold | **ours** |

Only three need curating, and each is small: tens of benchmarks that matter, a few
hundred named methods. A bounded hand-curated list, not an ontology project. The
distinction is the whole reason earlier attempts at this died.

Every canonical entity carries `aliases` (surface forms, many-to-one) and `is_a`
edges. `gpt-4-0613` is_a `GPT-4` is_a `OpenAI model` is_a `LLM`. Subsumption is
required rather than decorative — a claim about GPT-4 has to be reachable from a
query about OpenAI models.

Resolution order: exact alias → embedding candidate above threshold → pending
queue.

**An unresolved reference blocks admission. It never creates a node.** Silent
duplicate creation is how this graph dies: two SWE-bench nodes, results split
across both, nothing ever deleted, nobody notices for a year.

RDF stays an export format. Pulling QIDs means touching it anyway and emitting it
is cheap, but it is not the internal model — open-world semantics break every
admission check in this document.

## Out of scope

- Hosting full text
- Competing with OpenAlex / Semantic Scholar on coverage
- Fields other than agent/LLM research
- Ranking authors or venues

## Open questions

- Who arbitrates a `DISPUTES` edge, and what closes it?
- Do we re-execute anything ourselves, or only record others' reproductions?
- Scaffold identity — how do we decide two papers used "the same" scaffold?
- Granularity — store per-benchmark scores, or per-TaskInstance?
- Does a Result supersede, or do both stay live with different configs?
- Claim dedupe — the same proposition appears in many papers, phrased differently.
  Merge into one node with many sources, or keep separate and link? Merging is
  right and hard.
- What classifies a span as a claim, and what is the false-positive cost?
- If a claim's qualifiers are incomplete in the source, do we admit it as a claim
  with unknown scope, or refuse it?
