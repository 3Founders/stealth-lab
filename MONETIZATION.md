# Monetization Model

## Tier architecture

| Tier | What | Price anchor | Rationale |
|---|---|---|---|
| **Open core** | Library + evidence format + self-hosted registry | Free forever | Adoption wedge; the commons layer; Lightcone-compatible framing |
| **Hosted cloud** | Per verification-run, per stored procedure, per evidence query | Mirrors proven tiers: Mem0 free→$19→$249/mo; Zep Flex $125 credit-based; Letta usage-based | Buyers already pay for this layer; usage scales with value |
| **Procedure CI** | Candidate procedures run against real task suites before promotion | Priced above raw compute; own GPUs keep COGS low | Nobody else sells this; hardest to replicate; our unfair advantage is the harnesses already exist |
| **Enterprise** | Self-hosted/VPC, SSO, audit exports, private registries, SLAs | Six-figure contracts, Series-A era | Chainguard/Zep enterprise pattern |
| **Registry economics** (later) | Take-rate on third-party verified procedure packs | ~20% (EnterOS precedent) | Only after network effects |

## Cost structure

Verification compute (mitigated: own General Compute GPUs + IndiaAI IAICF
subsidised access), embeddings (local BGE/GTE/E5 — zero marginal cost),
hosting (minimal pre-launch). Unit economics improve as local inference
replaces paid embedding APIs entirely.

## Facing-specific versions

- **Investor one-liner:** "Usage-based API like the category leader, plus a
  verification service nobody else can sell."
- **Lightcone-facing:** "Revenue sustains open infrastructure; the evidence
  layer stays public."
- **YC verbal:** "Mem0 proved enterprises pay $19–249/month for memory storage.
  We charge for the thing storage vendors structurally can't provide — proof."

## Growth levers

1. APAC expansion (fastest-growing region, ~68.5% CAGR — home advantage)
2. Vertical procedure libraries (banking policy first — τ³ corpus already built)
3. Cross-domain transfer (coding → policy → ops) as upsell
4. Developer adoption flywheel: OSS core → team plans → hosted cloud

## What we deliberately do NOT charge for early

Evidence queries against the public registry (network effects need open reads);
format tooling (adoption requires zero-friction SKILL.md import/export);
community/self-hosted deployments (they compound the moat).
