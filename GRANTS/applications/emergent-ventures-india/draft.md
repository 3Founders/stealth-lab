# EV India — Application Packet (Emergent Ventures / Mercatus Center)

- **Form:** https://mercatus.tfaforms.net/5099527 (verified live 2026-08-23)
- **Region dropdown:** select **India**
- **Deadline:** none — rolling; responses typically within ~1 week
- **Award:** equity-free, project-specific (typ. $1K–$50K+)
- **Request:** $12,000

## Field map

| Form field | Content |
|---|---|
| Tweet-length description | "Agents repeat mistakes and re-derive solutions every run. We give them verified procedural memory: npm + CI for what AI agents know how to do." |
| Affected Region | India |
| Project Topic | Other |
| Personal info | Chaitanya Deshkar · IIT Bombay email · [ADD phone w/ country code] · Twitter [ADD] |
| Location | Country: India → State: Maharashtra → City: Mumbai |

## Proposal (≤1,500 words, structure per form instructions)

### Part 1 — About you (REQUIRED opener; credentials don't impress) — [PERSONALIZE BEFORE SUBMITTING]
> [2–4 paragraphs of concrete personal story: how Chaitanya got from first touching code at IIT Bombay to running agents against benchmarks at odd hours; the specific moment you realized nobody was keeping score of what agents learn — watching the same failure replay across runs and realizing the agent had no memory worth trusting. Anuj's parallel path at IISc: what he built, what broke, what it taught him. One anecdote each beats ten credentials.]

### Part 2 — Consensus view (their trick question)
> We agree with the mainstream consensus that LLM-based agents are real and will be deployed at massive scale across enterprises. Most contrarian takes bet against that deployment; our entire business is downstream of the consensus being right — we make deployed agents cheaper and trustworthy rather than betting they fail.

### Part 3 — The idea
**Project:** Verified procedural memory for AI agents — the missing evidence layer beneath the new industry-standard skill format.

The idea. Both Anthropic and OpenAI independently standardized `SKILL.md` — a file format for packaging what agents know how to do — and adoption spans 40+ tools (Cursor, Copilot, Gemini CLI, Claude Code). Formats don't verify themselves. Today an agent can install a procedure written by anyone, for any environment, with zero outcome evidence: no success rates, no regression signals, no lifecycle management. Enterprises deploying agents face the failure we measured in our own research: the majority of enterprise agent failures trace to context/procedure drift, not model capability. We are building the infrastructure that proves procedures work before machines install them — versioned, signed, benchmark-scored procedural memory with telemetry-backed lifecycle management. Think npm plus CI plus provenance, for what agents know how to do.

Why us. We have already built and benchmarked the hardest part twice. Our StealthLab system extracts procedures from raw agent experience and retrieves them under budget control: on 15 paired SWE-bench-style instances it cut token cost 74.4% (219 vs 330 tool calls) while matching baseline accuracy, and its failure modes generalize across independent repos. We integrated it into tau2-bench's banking_knowledge domain as a first-class substrate, and we're preparing a public leaderboard attempt: beating the top model's pass^1 score using small open-weight models augmented with our verified-procedure retrieval. That run doubles as our public launch artifact. We read everything — Voyager, ExpeL, Agent Workflow Memory, seven 2025–26 papers on procedural memory (Skill-Pro, MACLA, ReMe), and the newest wave on agentic RL and programmatic world models (MolMem, GATS, FedWorld) — and the gap we fill (outcome evidence + lifecycle + scope-aware sharing for cross-vendor skills) appears in no paper and no product we can find.

What the grant funds. GPU/API compute for the full τ³-banking leaderboard sweep (~$4,000); OSS release engineering — registry prototype, MCP server hosting, docs, signing infra (~$3,000); benchmark compute for the public evidence pack (~$3,000); travel to meet design partners in Bengaluru (~$2,000). A dollar goes far here: this funds months of full-time building at student burn.

Trajectory. We are applying to YC (Early Decision track, designed for final-year students), The Residency Bangalore's Founders Track (fully funded deep-tech residency in Koramangala), and Antler India's direct track. EV support would let us show up to those interviews with a public leaderboard result instead of a promise. Long-term ambition: when every agent platform ships a skill marketplace — and they will, within 24 months — every one needs someone who can prove the skills work before enterprises install them. We intend to be that layer.

Working status: part-time around final-year coursework until graduation ([MONTH/YEAR]); full-time thereafter. Co-founder listed below.

Prior work: [github repo link] · spec documents available · happy to demo live over video any time.

| Budget field | Entry |
|---|---|
| Estimated Budget | $12,000 |
| Breakdown of Expenses | Compute $4,000 · Release eng. $3,000 · Evidence-pack benchmarks $3,000 · Travel $2,000 |

## Attachments
- ☐ Benchmark results chart (PNG — NO PDFs accepted)
- ☐ Architecture one-pager (DOCX)
- Multimedia URL: ☐ [demo video when recorded]
- Co-founder: list Anuj Bhadbhade + email inside proposal

## Human-only checklist
- ☐ Write Part 1 personal story (placeholder above)
- ☐ Fill all [ADD] contact fields in MASTER_PROFILE first
- ☐ Complete reCAPTCHA + submit
- ☐ Log outcome + any new questions into answer_bank.md
