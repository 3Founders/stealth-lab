# researchplanlight.md — sequential execution plan

One item executes per "next". Each item ends with written output committed to
a file (or an explicit skip note). Ordered by leverage; re-order anytime.

## Queue

1. [x] **Lightcone Round 2 draft** → `FundingGrants/LIGHTCONE_DRAFT.md` (done)
2. [x] **Seldon Lab RFS Batch 2 application draft** →
       `FundingGrants/SELDON_DRAFT.md`
       ($500K/startup, agent-trust infra named in RFS, SF 3 months, intl OK;
        deadline-bearing — highest urgency after Lightcone)
3. [x] **Accel Atoms AI application answers** → ~~`ACCEL_ATOMS_DRAFT.md`~~
       **deleted by parallel session Aug 26** (content survives in
       `FundingGrants/RESIDENCIES.md` + `FUNDING_APPLICATIONS.md`; recreate
       only if user asks) (year-round; $2M co-investment w/ Google AI Futures Fund)
4. [x] **Emergent Ventures India application answers** →
       `FundingGrants/EV_INDIA_DRAFT.md`
       (rolling, monthly cohorts; zero-to-one framing)
5. [x] **Antler India direct-outreach package** →
       `FundingGrants/ANTLER_OUTREACH.md`
       (cold email + one-page brief referencing ₹4Cr/11% residency)
6. [x] **arXiv preprint skeleton** → `PREPRINT_SKELETON.md`
       (verification-methodology paper: claims→procedures→evidence, Welch+BH
        gate, paired results incl. negative accuracy result — feeds IP
        prior-art position + Lightcone/YC credibility)
7. [x] **Provisional patent assessment outline** → `PATENT_ASSESSMENT.md`
       (claims around statistical verification gate + bi-temporal lifecycle;
        includes prior-art sweep via Exa/OpenAlex on Howdex/SkillFab/CleanSkills
        + EDDOps arXiv:2607.00345 + 2 granted US patents + 1 application)
8. [x] **Deep traction pass on competitor cluster** → appended `COMPETITORS.md`
       (TrustMemory pricing live $49–499/mo + PyPI SDK; CleanSkills run by
        autonomous AI per its own ToS + point-in-time disclaimer quoted;
        Howdex solo velocity benchmark; none has announced institutional money)
9. [x] **DPIIT + incorporation runbook** →
       `FundingGrants/INCORPORATION_RUNBOOK.md`
       (SPICe+ steps incl. INC-20A trap, costs directional, DPIIT writeup
        drafted, same-week credit batch, PRAYAS sequencing warning, ~4-wk
        timeline, CA question list)
10. [x] **UK Sovereign AI decision memo** → `FundingGrants/UK_SOVEREIGN_MEMO.md`
       (verdict DEFER / India-only default; four explicit revisit triggers;
        GPU-hours gap bridged meanwhile by NVIDIA Inception + IndiaAI + credits)
11. [x] **Semantic Scholar decision** → DROPPED from active stack (three hard
       429s unauthenticated on Aug 26); formal research stack = Exa MCP +
       keyed OpenAlex + arXiv API. Re-entry path documented in `.env`
       comment (free key at semanticscholar.org/product/api); note already
       lives in `FundingGrants/FUNDRAISE_TARGETS.md` § Standing workflow.
12. [x] **Git commit** → `fundraising-strategy` branch, commit cac6186
       (49 files, +3760/−618), pushed to origin Aug 26 on user request.
       NOTE: repo visibility unverified from this machine (gh blocked by
       proxy) — if 3Founders/stealth-lab is PUBLIC, this package exposes
       competitor intel + investor targets; confirm private in repo settings.

## Ground rules

- Every draft uses the measured voice: full sentences, no superlatives,
  uncertainties named, numbers labeled directional where n is small.
- Every claim in applications must trace to a repo file or a fetched source.
- Deadlines tracked here beat memory; update statuses inline as things move.

## Standing context (not queued, background)

- ALL funding/fundraising/application docs consolidated in `FundingGrants/`
  (Aug 26, user instruction): 12 root files + the parallel session's `GRANTS/`
  pipeline (now `FundingGrants/GRANTS/` — its lightcone/EV drafts still overlap
  our LIGHTCONE_DRAFT/EV_INDIA_DRAFT; reconcile before submitting either).
  grant-apply skill paths updated to the new locations. Left at root as
  non-funding: MONETIZATION, COMPETITORS, PATENT_ASSESSMENT,
  PREPRINT_SKELETON. Tracker `FundingGrants/FUNDING_APPLICATIONS.md`
  refreshed Aug 26 (Seldon added, Lightcone→R2, PRAYAS ₹40L correction).
- Restart opencode loads the Exa MCP server (wired Aug 26).
- `.env` holds EXA_API_KEY + OPENALEX_API_KEY (gitignored; rotate eventually —
  keys passed through chat once).
- Lightcone R1 missed by one day; R2 responses ~Jan 23, 2027.
