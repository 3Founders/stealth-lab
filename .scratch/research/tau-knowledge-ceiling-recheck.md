# τ-Knowledge ceiling re-check (arXiv:2603.04370)

**Queue item 3 (board) — required before MEASURE harness baselines freeze** · **Date:** 2026-08-25 · **Lane:** research
**Method:** arXiv abstract re-fetch + official taubench.com board check.

## Paper facts (verified 2026-08-25)

- "τ-Knowledge: Evaluating Conversational Agents over Unstructured Knowledge" — Shi, Zytek, Razavi, Narasimhan, Barres; submitted 4 Mar 2026 — <https://arxiv.org/abs/2603.04370>. τ-bench team lineage confirmed by author overlap with τ-bench/τ²-bench (Yao/Shinn/Razavi/Narasimhan line, cf. arXiv:2406.12045).
- New domain **τ-Banking**: fintech customer-support workflows; agents must coordinate **~700 interconnected knowledge documents** with tool-mediated account updates; success = verifiable, policy-compliant state changes.
- **Ceiling claim:** across embedding-based retrieval *and* terminal-based search, frontier models with high reasoning budgets reach only **~25.5% pass^1**, with reliability degrading sharply over repeated trials (pass^k). Failure attribution: retrieving the right documents from densely interlinked KB + reasoning over complex internal policies.

## Movement since publication (the reason this re-check was scheduled)

Official board now shows τ³-Banking top at **55.2% pass^1 (Qwen 3.8 Max)**, Claude Opus 5 48.7%, Grok 4.5 47.9% — <http://taubench.com/leaderboard?benchmark=knowledge> (viewed 2026-08-25). So the practical ceiling roughly doubled in ~5 months but remains the least-saturated surface in the τ family:

| Surface | Top pass^1 (official) | Saturated? |
|---|---|---|
| τ² telecom (tracker rows) | ~99% | effectively yes |
| τ² core (retail+airline+telecom avg) | 87.9% | approaching |
| τ³-Banking | 55.2% | **open** |
| τ-voice | 75.4% | partial |

Sources: <http://taubench.com/>, <https://evals.report/benchmarks/tau2-bench>.

## Consequences for harness baseline freeze

1. **Keep τ-Knowledge in scope as our headroom benchmark.** Its failure modes (wrong document retrieved from interlinked corpus → wrong policy applied → invalid write) are precisely the failures verified procedural memory + evidence-gated applicability target. A 55%-ceiling benchmark leaves room to show movement; a 99% one doesn't.
2. Baselines must record: retrieval mode (embedding vs terminal search), reasoning budget, and pass^k — the paper's own ablation axes (<https://arxiv.org/abs/2603.04370>). Without those, rows aren't comparable (same caveat as <https://benchlm.ai/benchmarks/tau2-bench>).
3. The March 25.5% figure is stale for decks; use the official-board current numbers above and cite both dates.
4. pass^k degradation ("reliability degrading sharply over repeated trials") is our single best-aligned metric — recommend the harness report pass^1 and pass^3 side by side from day one.
