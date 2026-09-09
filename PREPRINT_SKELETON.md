# arXiv Preprint Skeleton — Verification Methodology Paper

Working title candidates:
1. *Verified Procedural Memory: Statistical Gates for Reusable Agent Knowledge*
2. *No Proof, No Procedure: Outcome-Verified Skill Libraries for LLM Agents*
   (note: Howdex uses similar slogan — check before finalizing; prefer #1)
3. *From Traces to Trust: A Claims-Procedures-Evidence Architecture for Agent Memory*

Target: arXiv (cs.AI/cs.CL) first for timestamping/prior-art; then an
agents/safety workshop (COLM/NeurIPS workshop track) with the full evaluation.

Authors: Chaitanya Deshkar¹, Anuj Bhadbhade² (¹IIT Bombay, ²IISc)

---

## Abstract skeleton (~180 words)

- Context: agents share procedural knowledge via standard formats (~40
  platforms); reuse is unverified; shared memory is now a demonstrated attack
  surface (cite MAFIA/Salami/detection-failure papers)
- Contribution 1: three-layer architecture — bi-temporal claims, compiled
  procedures with preconditions/invariants, immutable outcome evidence
- Contribution 2: promotion gate = statistical backtest (Welch t-tests +
  Benjamini-Hochberg) on train instances; retrieval honors truth states so
  superseded knowledge is filtered
- Contribution 3: paired evaluation protocol with per-capability scorecard
  (LOCATE/FIX/NOT-BREAK/COMPLETE) and directional-n reporting discipline
- Results: efficiency headline (74.4% token reduction, 34% fewer actions,
  n=15 paired); negative result reported (no accuracy gain yet) with failure-
  class analysis (right-file/wrong-fix, 12 instances × 8 repos) and the
  instrumentation fix queued for held-out measurement
- Claim discipline sentence: all numbers reproducible from released harnesses;
  nothing self-graded by LLM judges

## Section outline

1. **Introduction** — amnesia tax → sharing era → verification gap (P1–P9
   compressed); why attestation/consensus/receipt approaches (Howdex,
   TrustMemory, CleanSkills) don't substitute for outcome statistics
2. **Related work** — procedural memory line: Voyager, ExpeL, AWM, ReMe,
   MACLA, Skill-Pro, AFTER, WMT; memory systems: MemGPT/Letta, Mem0, Zep/
   Graphiti; security: MAFIA, Salami/MemCollusion, trajectory-signature
   detection; benchmarks: τ-bench pass^k, SWE-bench gold-patch protocol
3. **Architecture** — ontology (claims/procedures/evidence schema);
   compilation pipeline; adjudication for conflicting memories; lifecycle
   (promote→supersede→retire)
4. **Verification gate** — backtest design; multiple-comparison handling;
   promotion thresholds preregistered
5. **Evaluation** — harness independence argument; paired-instance design;
   results incl. negative accuracy result + failure taxonomy table;
   contamination checks (memorization risk measured, not assumed)
6. **Threat discussion** — poisoning resistance properties of evidence-gated
   registries; limits (what it does NOT defend against)
7. **Limitations** — n small; single-domain loop so far; human-in-synthesis
   step; no cross-model transfer numbers yet (AFTER suggests multi-source
   synthesis helps — future work)
8. **Ethics/openness** — full release plan

## Citation list (all previously fetched/read this session)
arXiv:2305.16291 Voyager · 2308.10144 ExpeL · 2409.07429 AWM · 2406.12045
τ-bench · 2512.10696 ReMe · 2512.18950 MACLA · 2602.01869 Skill-Pro ·
2606.23127 AFTER · 2604.27003 continual-learning-to-memory · 2604.08064
ImplicitMemBench · 2606.29824 NPM · 2501.13956 Zep · 2607.21503 ACM context
economics · 2608.13921 TANGLE · 2608.06909 attribution · 2608.03844 MAFIA ·
2608.01637 Salami · 2606.30566 detection boundary · 2607.12406 isolation
survey · 2608.20631 WMT · 2506.05690 GraphRAG-Bench · 2604.14220 CFR KG ·
2511.05991 ontology-KG RAG · 2603.04814 memory-vs-long-context

## Writing rules for this paper (our brand on record)
- Negative results appear in the abstract-level summary, not buried
- Every quantitative claim carries n and test type or is labeled directional
- No LLM-judge scores anywhere in headline tables
- Related-work section names commercial systems fairly (incl. competitors)

## Timeline
- Skeleton → full draft after Milestone 1 (loop closed) so §5 has the held-out
  measurement; v1 to arXiv target within 2 weeks of that landing
