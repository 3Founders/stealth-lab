# MASTER PROFILE — Single Source of Truth for All Applications

Every grant/accelerator draft derives from this file. Update HERE first; drafts regenerate.
Last updated: 2026-08-23

---

## Founders

### Chaitanya Deshkar — Point of Contact
- Final Year AI/ML, Centre for Multidisciplinary Education, **IIT Bombay**
- Built end-to-end: procedure-extraction pipeline, benchmark harnesses (τ-bench DB-hash scoring, SWE-bench gold-patch grading), stealthlab_bridge MCP server, retrieval under budget control
- Email: [ADD] · Phone: [ADD] · Twitter/X: [ADD] · LinkedIn: [ADD] · GitHub: [ADD]

### Anuj Bhadbhade — Co-founder
- **IISc Bangalore** — systems + evaluation
- Email: [ADD] · LinkedIn: [ADD]

### Personal-story anecdotes bank (fill these — reviewers buy stories, not credentials)
- [How Chaitanya got here: 2–3 concrete moments]
- [The specific moment you realized nobody keeps score of what agents learn]
- [Anuj's path at IISc: 2 concrete moments]
- [A failure that taught you something about agents repeating mistakes]
- Graduation months: Chaitanya [MONTH/YEAR] · Anuj [MONTH/YEAR]

## Project Narratives

### One-liner (tweet-length)
> Agents repeat mistakes and re-derive solutions every run. We give them verified procedural memory: npm + CI for what AI agents know how to do.

### ~150 words
> Teams deploying AI agents watch them repeat the same mistakes forever — most enterprise agent failures trace to context drift, not model capability. StealthLab turns raw agent experience into verified procedural memory: procedures extracted from real traces, carrying provenance, success criteria, and lifecycle status, retrieved under explicit budget control at decision time. On paired benchmark instances we cut token cost 74.4% while holding accuracy flat, integrated as a first-class memory in tau2-bench's banking domain. Both AI labs just standardized the file format for agent skills (SKILL.md); nobody verifies what gets installed. We are building the evidence layer: npm plus CI plus provenance for what agents know how to do.

### ~600 words → use `GRANTS/applications/emergent-ventures-india/draft.md` body
### ~1500 words → expand the above with: failure-class generalization data, dist.md wall-clock analysis, spec v5 roadmap (learning loop, layered world model lookup, scope-aware sharing)

## Metrics Sheet (never invent numbers — copy from here)

| Metric | Value |
|---|---|
| Token reduction (15 paired instances) | 74.4% — 219 vs 330 tool calls |
| Accuracy on pairs | Flat: 2/15 vs htn 0/15 (disclose proactively) |
| Wall clock / instance | 880.9s mean; tests+docker 65%, agent loop 35% |
| Failure classes (cross-repo) | RIGHT_FILE_wrong_fix 12 inst./8 repos · NO_EDIT_at_all 11 · LOCALIZATION_miss 9–10 |
| Memory-infra market | $1.2B→$18.9B @62% CAGR (Market Intelo); APAC fastest @68.5% |
| Orchestration+memory | $6.27B→$28.45B by 2030 @35.3% (Mordor) |

## Budget Templates

### Compute-heavy round ($12K — EV-style)
GPU/API compute for public leaderboard sweep $4,000 · OSS release engineering (registry prototype, MCP hosting, docs, signing) $3,000 · benchmark compute for public evidence pack $3,000 · design-partner travel (Bengaluru) $2,000

### Residency/hardware round ($8K)
GPU dev cluster $3,500 · eval API spend $2,500 · travel $2,000

## Assets Inventory
- Repo: [github.com/…/StealthLab] · Spec docs (v4 + ideal-spec) in repo root
- Deck: slides_v2.md · Interview kit: YC_INTERVIEW_PREP.md
- Demo: [RECORD: 2-min founder video + 2-min product video — required by Conviction Embed, useful everywhere]
- Attachments policy per program: NO PDFs (EV); PDF decks OK elsewhere unless stated

## Consensus-view answer (EV trick question)
> We agree with the mainstream consensus that LLM-based agents are real and will be deployed at massive scale across enterprises. Most contrarian takes bet against that deployment; our entire business is downstream of the consensus being right — we make deployed agents cheaper and trustworthy rather than betting they fail.
