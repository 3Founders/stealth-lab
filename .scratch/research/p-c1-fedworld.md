# P-C1 — FedWorld mechanics verification

**Ticket:** RESEARCH_INTEGRATION_PLAN.md P-C1 `[papers]` · **Date:** 2026-08-25 · **Lane:** research
**Method:** arXiv abstract + full HTML text v1.

## Verdict: CONFIRMED — and richer than the plan assumed

Source: <https://arxiv.org/abs/2608.01561> ("FedWorld: Scope-Aware Federation of Agent World Models", Yuchao Hou, 3 Aug 2026, cs.HC; full text <https://arxiv.org/html/2608.01561v1>). Single author; no code link on the landing page.

## Mechanics (verified against full text)

Protocol = `(φ, rel, σ, Compat, Compile)`:

1. **M1 Abstract rule mapping** — each client normalizes private transitions into abstract records `z=(a,x,c,e,ε)` (action, pre-state, condition set, effect, exception structure); identity key = normalized (pre-state, action, conditions). Raw trajectories, entity ids, tool args never leave the client.
2. **M2 Cross-client rule alignment** — relation labels `match / compatible / conflict / unresolved`; only match+compatible proceed. Explicitly *not* nearest-neighbor similarity: "FedWorld does not admit a candidate based on similarity alone" (§M2).
3. **M3 Scope inference** — per rule: Laplace-smoothed confidence `q_r=(n⁺+1)/(n⁺+n⁻+2)` over observations; client-level support ratios `p_r=|K_r⁺|/|K_eligible|` (eligibility = precondition can occur under that client's config); cluster analysis for cluster-specific rules; scope ∈ `shared / cluster-specific / private / unresolved`. Unresolved rules stay in the store but cannot affect execution.
4. **M4 Gap detection + local-first compilation** — a local rule with reliability ≥ τ_loc always wins (Eq. 13); federated candidates admitted only for uncovered keys whose scope is compatible; ranked by relation type → scope specificity → q_r; abstain if nothing passes.
5. **Theorem 1 (protection–transfer trade-off):** NetTransfer = PTR − NTR = (1−β)p₊ − αp₋; scope filtering beats naive pooling when the harmful overwrites prevented exceed the useful transfers withheld.

## Numbers (τ-bench: 11 clients from 6 retail + 5 airline policy profiles; ALFWorld: 12 clients; 5 partition seeds; 400/600 online episodes per seed)

- Offline strict-conflict split (majority contradicts target truth): naive pooling (B3) nets **−0.138** (τ) / **−0.222** (ALFWorld) transfer vs local-only B1; alignment-only (B4) still below local. FedWorld (B5) recovers **+16.6 EM points over B3 on τ-bench and +25.0 on ALFWorld**, and beats local-only by **+2.8 points on both**.
- Cross-client gap-filling (S3): +12.4 pts (τ) / +21.2 (ALFWorld) over local-only; held-out S5: +12.2 / +20.0.
- Online execution vs local-only (B1→B5): state regressions ↓44.5% (τ) / ↓48.2% (ALFWorld); repeated actions ↓48.1% / ↓50.2%; excess steps ↓38.4% / ↓42.8%; task success **0.512→0.624** (τ) and **0.447→0.593** (ALFWorld) (+11.2 / +14.6 pts).
- Centralized detailed retrieval (CDR), with strictly more information, is *not* consistently better than scoped federation — access to more data does not replace target-conditioned scope control.
- Thresholds used: |K⁺|≥3, p_r≥0.60, ρ⁻≤0.10 (recalibratable on small validation sets).

## Our differentiation check

Plan says: "resolution by *execution* (deterministic benchmarks), not peer vote." Verified accurate as positioning: FedWorld resolves scope by cross-client evidence counts (peer evidence), assumes deterministic abstract transitions, and lists automatic abstraction for web/software agents plus stochastic/partial-observability handling as future work (Conclusion). Its own limitation section leaves exactly our lane open: execution-based resolution, lifecycle states beyond its four-way scope tag, non-deterministic effects.

## Board deltas

1. Theme C claim survives verification intact; add hard numbers above to any deck slide.
2. Note venue oddity: cs.HC classification, single author, no released code found — cite mechanics, treat as protocol paper not artifact.
3. Product mapping upgrade: FedWorld's `unresolved` ≈ our quarantined state; their local-first gap-fill policy ≈ our applicability gate's "no coverage → abstain" path. Their Eq. 13 constraint (local verified rule always wins) is a citable precedent for our non-compensatory gating default.
