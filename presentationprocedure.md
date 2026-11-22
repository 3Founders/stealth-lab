# Elite Academic Research Presentation Procedure

**Version:** 1.0.0 · **Status:** ready for use · **Owner:** the presenter (human) · **Executor:** an AI authoring
agent with rendering tools · **Audience of this document:** the agent that will run it, and the human who will
approve its gates.

**Goal.** Turn either (A) a research idea or (B) existing research material into a defensible, accessible,
assertion-evidence presentation in LaTeX Beamer or HTML, with a synchronized speaker transcript, a terminology
ledger with zero forward-dependency violations, a persona-based visual audit, and a final holistic gate that
refuses to deliver a deck that fails cohesivity, contextuality, or sense-making.

**Why this is a procedure and not a prompt.** A prompt produces a deck. A procedure produces the same quality of
deck on the tenth run as on the first, because every judgment is either automated with a pass/fail check or
assigned to a named human checkpoint with a specific question. The loops below exist because the known failure
mode of AI-generated academic slides is not bad prose; it is **lost dependencies, orphan slides, and a confident
voice covering a missing logical step.**

---

## 0. How this procedure was authored (the meta-procedure)

No upload channel was available, so this document was written with the **procedure-for-creating-procedures**
(the `skill-creator` loop), recorded here so the document can be audited and regenerated:

1. **Capture intent.** The eight requirement clusters in the brief were converted into hard rules R1–R12 (§1.2),
   each with a measurable check. Nothing in the brief was softened into "consider".
2. **Draft.** The work was decomposed into nine phases (P0–P8), each with one artifact, one automated check, and
   one gate. Every gate has an owner and a pass/fail statement.
3. **Test cases.** Three realistic prompts were written (§15): a from-scratch conference talk, a thesis-defense
   distillation, and a lightning talk from lab notes. They exercise both entry modes, all three audience types,
   and the hardest persona.
4. **Evaluate.** The draft was scored against six criteria: completeness against the brief, enforceability
   (can a machine check it?), repeatability (same output from a cold start?), artifact specificity, HITL clarity,
   and failure routing. The weak points were numeric thresholds, the dependency ledger, and the retry guardrail;
   those became §7, §11, and §12.
5. **Improve.** The revision added the exact word/time budgets, the slide-spec schema, the render pipeline, the
   persona question taxonomy, and the diagnostic routing table. The result is version 1.0.0.

**Way shape.** The same procedure is expressed in kell's Way schema so it can be submitted as a procedure:

```
Goal:   Create an elite academic research presentation from an idea or existing material
Steps:  P0 Intake (human) → P1A/P1B Research or Distill (automated + human) →
        P2 Narrative (human approval) → P3 Draft (automated) →
        P4 Dependency audit (automated + human) → P5 Render + persona audit (automated + human) →
        P6 Transcript (automated + human) → P7 Final gate (automated + human) → P8 Deliver
Verify: gates G0–G8; the final gate is three pass/fail checks, not a score.
```

---

## 1. Operating model

### 1.1 Roles

| Role | Who | Responsibility |
|---|---|---|
| **Presenter** | the human | Supplies intake facts, owns the claims, signs every HITL checkpoint, decides trade-offs. |
| **Author** | the agent | Runs P1–P6, produces artifacts, fixes what the audits route back. |
| **Auditor** | a separate agent (or a fresh context) | Runs the automated checks and the persona critique; must not be the same context that wrote the slides. |
| **Persona reviewer** | a simulated reviewer defined at intake | Judges each rendered slide as the named professor/committee member/conference reviewer would. |
| **Gatekeeper** | the presenter | Reads the gate report and signs, rejects, or escalates. |

The Author/Auditor split matters: a model asked to critique its own slides will defend them. The audit prompts in
§13 are written for a context that did not draft the deck.

### 1.2 Hard rules (each maps to a gate)

| Rule | Requirement | Checked at |
|---|---|---|
| **R1 Dual entry** | The run starts in Mode A (idea) or Mode B (material); the mode is recorded before any slide exists. | G0 |
| **R2 Intake facts** | Speaking time, Q&A time, audience level, and a named Target Reviewer Persona (field, biases, pet peeves, what convinces them) are captured verbatim. | G0 |
| **R3 No invention** | Every number, baseline, metric, dataset, and citation maps to a source ID. Missing evidence is marked `UNVERIFIED` and omitted or escalated; it is never guessed. | G1, G7 |
| **R4 Format** | LaTeX Beamer or HTML (Reveal/Slidev/Quarto); equations typeset; slide numbers on every slide; citation footnotes with DOI/URL on the slide. | G3 |
| **R5 Accessibility** | Light/warm background, dark text, ≥4.5:1 contrast, color-blind-safe palette, sans-serif, title ≥28 pt, body ≥18–22 pt, labels ≥14 pt, no flashing, alt text. | G3, G5 |
| **R6 Assertion-Evidence** | Every content headline is one declarative sentence of 8–14 words; body is one visual plus ≤2 callouts; no bullet walls. | G3 |
| **R7 Archetypes** | No generic outline; hook within 2–3 minutes with a ≤25-word finding; progressive disclosure; take-home closing; numbered backup after the close. | G3, G7 |
| **R8 Cohesivity** | One logical arc, zero orphan slides, re-entry anchors, proportional allocation. | G2, G7 |
| **R9 Dependency audit** | A slide-by-slide ledger (term → first used → first defined) with zero violations, signed by a human dedicated to that check. | G4 |
| **R10 Persona visual audit** | Every slide rendered to an image and critiqued against the persona, including the obvious-question stress test, with each question resolved (edit / transcript / backup). | G5 |
| **R11 Delivery kit** | 130–150 wpm, finish at ~90% of the slot, 45 s verbatim open, 30 s verbatim close, figure narration cues, 1–2 skippable slides, 3–5 toughest Q&A with backup links. | G6 |
| **R12 Final gate + routing** | Cohesivity, contextuality, and sense-making are pass/fail; failure routes to the earliest responsible phase, re-runs downstream, and re-enters the gate; max 2 retries per gate, 3 cycles total, then human escalation. | G7 |

### 1.3 Workspace layout

```
talk/
├── intake.yaml
├── sources/                     # Mode B inputs, read-only
├── evidence/
│   ├── claims.csv               # claim inventory (source-grounded)
│   ├── citations.csv            # source id → full reference + DOI/URL
│   └── figures/                 # original or generated, with provenance
├── narrative/
│   ├── arc.md                   # the logical arc, not the experiment chronology
│   └── slide_plan.yaml          # slide ids, archetypes, time, claim ids
├── slides/
│   ├── spec/                    # one YAML per slide (the source of truth)
│   ├── main.tex  or  index.md   # generated from the specs
│   └── theme/                   # Beamer theme or Reveal CSS
├── audits/
│   ├── lint.json
│   ├── dependency_ledger.csv
│   ├── dependency_report.json
│   ├── render/                  # slide-01.png ...
│   ├── visual_report.json
│   └── final_gate.json
├── transcript/
│   ├── transcript.md
│   ├── timing.csv
│   └── qa.md
├── build/                       # PDF and/or HTML
└── MANIFEST.md                  # what was delivered, and the gate results
```

### 1.4 State machine

```
P0 Intake ──G0──▶ P1A Research (Mode A) ─┐
                  P1B Distill  (Mode B) ─┤
                                         ├─G1─▶ P2 Narrative ──G2──▶ P3 Draft ──G3──▶
P4 Dependency audit ──G4──▶ P5 Render + persona audit ──G5──▶
P6 Transcript ──G6──▶ P7 Final gate ──G7──▶ P8 Deliver
                         ▲                    │
                         └──── remediation ───┘   (routed to the earliest responsible phase)
```

A gate is **closed** only when its report is written and the named human has signed it. A phase may not start on
an unclosed gate. The one exception is P5 rendering, which needs a draft from P3; it still waits for G3.

---

## 2. P0 — Intake (both modes)

**Purpose.** Make the constraints explicit before any research or slide exists. Most bad academic talks are
caused by an unstated audience or an unstated time slot, not by bad slides.

### 2.1 Intake form (write `intake.yaml`)

```yaml
mode: A | B
title_working: "…"
speaking_minutes: 20
qa_minutes: 10
total_slot_minutes: 30
audience:
  kind: specialist_conference | mixed_department | thesis_defense | lightning | industry
  assumed_knowledge: "…"
  must_not_assume: ["…"]          # terms the audience will not know
reviewer_persona:
  name_or_role: "Prof. … / Reviewer 2 / methods chair"
  field: "…"
  methodological_biases: ["…"]    # e.g. distrusts wall-clock speedups without error bars
  pet_peeves: ["…"]               # e.g. undefined axes, unstated baselines, hand-waving proofs
  what_convinces_them: ["…"]      # e.g. ablations, confidence intervals, a held-out set
  likely_first_question: "…"
source_material:                   # Mode B only
  paths: ["…"]
  rights: "own | co-author | public | permission-granted"
constraints:
  venue_template: "…"
  required_sections: ["…"]
  forbidden: ["…"]
core_finding_one_sentence: "…"     # may be provisional in Mode A
```

### 2.2 Validation (automated + human)

Automated:
- `speaking_minutes + qa_minutes == total_slot_minutes` (or the slot is explicitly `open`).
- `speaking_minutes >= 5`; if `< 5`, force the lightning archetype and ≤6 main slides.
- Persona has at least one `methodological_bias`, one `pet_peeve`, and one `what_convinces_them`; a persona of
  "general audience" is rejected as too vague to audit against.
- Mode B has at least one source path and a rights statement.

Human **HITL-0**: the presenter reads back the time budget, the audience's assumed knowledge, and the persona's
likely first question. They sign `intake.yaml` with `approved_by` and `approved_at`.

**G0 pass:** all fields present, validation clean, HITL-0 signed. **Fail:** return to intake; no research starts.

### 2.3 Time arithmetic (fixed now, used everywhere later)

```
main_slides_target = round(speaking_minutes * 1.0)      # acceptable range 0.8–1.2 per minute
main_slides_min     = ceil(speaking_minutes * 0.8)
main_slides_max     = floor(speaking_minutes * 1.2)
word_budget         = round(speaking_minutes * 0.9 * 140)   # 130–150 wpm, finish at ~90%
```

Examples: 12 min → 10–14 main slides, ~1,510 words; 20 min → 16–24 slides, ~2,520 words; 5 min → 4–6 slides,
~630 words. Backup slides are outside these counts.

---

## 3. P1A — Mode A: from an idea to an evidence-backed finding

**Purpose.** A topic is not a talk. This phase produces the claim, the evidence, and the figures before a single
slide is drafted.

### 3.1 Scope the question

Write a one-page `narrative/scope.md` with:
- the problem in one sentence a non-specialist can repeat;
- the research question, in the form "Does X affect Y, under conditions Z?";
- the gap: what the literature does not answer (with 2–3 citations);
- the contribution claim: one sentence, falsifiable, with the comparison and the metric;
- the boundary: what this talk will not claim.

**Automated check:** the contribution sentence contains a comparison ("than", "versus", "relative to") and a
metric or observable outcome. A sentence like "we study X" fails; "X reduces Y by Z under W" passes.

### 3.2 Literature and evidence sweep

Use at least two independent sources (e.g., OpenAlex/arXiv plus a web search), and record every query:

```yaml
queries:
  - source: openalex
    q: "…"
    filters: "from_publication_date:2022-01-01"
  - source: arxiv
    q: "all:\"…\""
  - source: web
    q: "…"
```

For each included work, add a row to `evidence/claims.csv`:

```csv
claim_id,claim_text,source_id,source_locator,quote_or_number,confidence,used_in,notes
C1,"Sparse attention cuts p95 latency 40% vs dense",S3,"Table 2, p.7","40% (dense 120 ms, sparse 72 ms)",high,S07,"same hardware, same batch"
```

Rules:
- One claim per row; one source per row (split multi-source claims).
- `source_locator` is a page, table, figure, or section, not just the paper title.
- A number without a locator is `UNVERIFIED` and cannot enter a slide.
- If two sources disagree, record both rows and a `disagreement` note; the slide must show the disagreement or
  choose one with a stated reason.

### 3.3 Define the core finding and the evidence ladder

`narrative/finding.md`:
- the finding in ≤25 words (this becomes the hook and the closing);
- the strongest evidence for it (the one chart);
- the strongest counter-evidence or limitation (this becomes a slide, not a footnote);
- what would change the presenter's mind.

### 3.4 Figure plan

For every planned figure, write a spec before generating it:

```yaml
figure: latency_vs_accuracy
question: "Does the latency win survive at equal accuracy?"
kind: scatter | bar | line | diagram | equation_breakdown | before_after
data: evidence/figures/latency.csv
x: "p95 latency (ms)"          # units mandatory
y: "accuracy (%)"              # units mandatory
baseline: "dense attention"
ours: "sparse attention"
annotation: "40% lower latency at <0.5% accuracy drop"
source_id: S3
```

Rules: no chart without units; no 3D; no dual y-axis unless unavoidable and labelled; direct comparison of
baseline and ours; color-blind-safe palette; a caption that states the takeaway, not "Results".

**G1 pass (Mode A):** every slide-bound claim has a source and locator; the core finding is ≤25 words; every
figure has a spec with units and a baseline; no `UNVERIFIED` claim is marked for the main deck. **Fail:** return
to 3.2/3.4 for the missing evidence; do not draft slides around a hole.

---

## 4. P1B — Mode B: distill existing material without inventing

**Purpose.** Cut 65–70% of paper-level detail while preserving every fact that matters. The danger is not
omission; it is **confident invention** while compressing.

### 4.1 Inventory the source

Read the paper/chapter/notes/code and produce:
- `sources/inventory.md`: sections, figures, tables, appendices, code artifacts;
- `evidence/claims.csv`: every claim, number, baseline, metric, dataset, and citation in the source, with a
  locator (page/table/figure/line);
- `evidence/citations.csv`: the source's references, with DOI/URL where available;
- `sources/open_questions.md`: things the source does not state (e.g., missing error bars, undefined baseline).

### 4.2 Classify and cut

Assign every claim exactly one class:

| Class | Meaning | Main deck? |
|---|---|---|
| **MUST** | The core finding, the method's identity, the key comparison, the main limitation. | Yes |
| **SUPPORT** | One line or one callout that makes a MUST believable. | Sometimes |
| **BACKUP** | A derivation, ablation, secondary baseline, or robustness check. | Backup slide only |
| **CUT** | Detail a listener cannot absorb in real time; experiment chronology; implementation trivia. | No |

**The 65–70% rule, operationalized:** count source claims and source words. The main deck must retain **30–35%
of source claims** and **30–35% of source words** (65–70% cut). Measure both; a deck that keeps 50% of claims but
cuts words is not a distillation, it is a summary with the same cognitive load.

**Preservation invariants (checked automatically):**
- Every number on a slide appears verbatim in `claims.csv` with the same unit and precision.
- Every baseline named on a slide exists in the source; no new baseline is introduced.
- Every citation on a slide exists in `citations.csv`; no citation is invented or re-attributed.
- Every caveat attached to a MUST claim in the source survives on the slide, in the transcript, or in a backup.
- Rounding is allowed only if it does not change the sign or the stated conclusion; record the original.

### 4.3 The missing-data register

When the source lacks something the talk needs (a baseline, an error bar, a dataset split), write it in
`sources/open_questions.md` and choose one:
1. omit the claim;
2. show it as a limitation;
3. ask the presenter for the missing evidence before drafting.

Never fill the gap with a plausible number. The persona audit will ask exactly the question the gap invites.

**G1 pass (Mode B):** claims.csv covers the source; cut ratio is 65–70% by claims and words; every slide-bound
number/citation maps to a source row; the missing-data register is resolved or explicitly carried as a
limitation. **Fail:** return to 4.1/4.2; do not draft.

---

## 5. P2 — Narrative architecture (the logical arc)

**Purpose.** Make the deck a argument, not a lab notebook. The audience should feel each slide earn the next.

### 5.0 The four cohesivity dimensions (each has a check)

| Dimension | Operational definition | Check |
|---|---|---|
| **Unified narrative arc** | One logical story, not the chronology of experiments. Each slide earns the next through cause and effect. | A one-sentence "because X, we need Y" transition exists for every adjacent pair. |
| **Slide-to-slide dependence** | No orphan slides: every slide sets up a later slide or pays off an earlier one. | Every slide has an incoming or outgoing `sets_up`/`pays_off` edge; no isolated tangent, unused definition, or unsupported claim. |
| **Re-entry anchors** | After a technical deep dive, a deliberate signpost or summary lets a listener who lost focus reconnect to the big picture. | One anchor after each deep section (see §5.4), with a one-line "where we are" and no new terminology. |
| **Proportional allocation** | Time and slide count reflect importance: background tight, core ideas/method/findings at least half the talk. | Summed minutes: background ≤15%, core ≥50%, take-home ≤15%, hook/tension the remainder. |

### 5.1 The five-beat arc

| Beat | Job | Typical share of speaking time |
|---|---|---|
| **Hook** | The problem, why it is hard, the ≤25-word finding. | 10% |
| **Tension** | What the obvious approach misses; the gap. | 10% |
| **Insight** | The one idea that resolves the tension. | 15% |
| **Evidence** | The core method + the decisive result(s), with the strongest counter-evidence. | 50% |
| **Take-home** | The finding, the limitation, the implication, the closing visual. | 15% |

Background/literature stays inside Hook + Tension and must not exceed **15%** of speaking time. Core
ideas/method/findings must receive **≥50%**. If the arithmetic does not fit, cut background, not evidence.

### 5.2 Slide plan (`narrative/slide_plan.yaml`)

```yaml
arc:
  hook: [S01, S02, S03]
  tension: [S04, S05]
  insight: [S06, S07]
  evidence: [S08, S09, S10, S11, S12]
  take_home: [S13, S14]
proportion:
  hook: 0.10
  tension: 0.10
  insight: 0.15
  evidence: 0.50
  take_home: 0.15
slides:
  - id: S01
    archetype: hook
    job: "name the problem the audience already feels"
    claim_ids: []
    sets_up: [S02]
    pays_off: []
    minutes: 1.0
  - id: S07
    archetype: insight
    job: "the one idea"
    claim_ids: [C2]
    sets_up: [S08]
    pays_off: [S05]
    minutes: 2.0
```

### 5.3 Cohesivity checks (automated)

1. **Zero orphans.** Every slide except S01 has at least one incoming `pays_off` or is the explicit setup for a
   later slide; every slide except the closing has at least one outgoing `sets_up` or `pays_off`. A slide with
   neither is an orphan and fails.
2. **Arc continuity.** For each adjacent pair, write the transition in one sentence: "Because X, we need Y."
   If the sentence cannot be written without "also" or "next", the pair is a seam, not a transition.
3. **Proportional allocation.** Sum `minutes` per beat; compare to the table; fail if background >15% or
   evidence <50%.
4. **Slide budget.** `main_slides` within the range from §2.3; backup slides are counted separately.
5. **No generic outline.** No slide whose headline is "Outline", "Agenda", "Motivation → Method → Results", or a
   list of section names. For talks with 3–5 distinct parts, use a visual signpost slide with a one-line "where
   we are" and no new terminology.

### 5.4 Re-entry anchors (placement and check)

Place an anchor:
- after every section that introduces a new formalism, system diagram, or proof step;
- after any run of two or more slides that a non-specialist would struggle to follow;
- before the take-home, so the audience reconnects the finding to the problem.

An anchor is a **signpost slide** or a one-line banner, not a new content slide. It says: "Where we are: problem
X → insight Y → evidence Z." It introduces no new term. For a 20-minute talk, 2–4 anchors is typical; more than
one per section is clutter.

**Automated check:** every slide marked `archetype: signpost` has no `claim_ids`, no new glossary term, and one
sentence; every deep section in `slide_plan.yaml` has an anchor within two slides of its end.

**HITL-1 (narrative approval):** the presenter reads `arc.md` + `slide_plan.yaml` and answers:
- Can you state the finding in one sentence? Is it the sentence on S01?
- Does each slide earn the next? Point to the transition you cannot defend.
- Is the proportion right for *this* audience and *this* reviewer?
- Which slide would you cut first if you were 3 minutes behind?

**G2 pass:** all five checks pass, HITL-1 signed. **Fail:** return to §5.1/5.2; do not draft.

---

## 6. P3 — Slide drafting (assertion-evidence)

**Purpose.** Turn each planned slide into one claim plus one visual, in a format that compiles.

### 6.1 The slide spec (source of truth)

Every slide is a YAML file; the LaTeX/HTML is generated from it. This is what makes the audits possible.

```yaml
id: S07
section: evidence
archetype: evidence          # hook | tension | insight | evidence | method | limitation | signpost | take_home | backup
headline: "Sparse attention cuts p95 latency 40% at under 0.5% accuracy loss"
headline_words: 12
claim_ids: [C3]
visual:
  kind: chart
  file: evidence/figures/latency_vs_accuracy.pdf
  alt: "Scatter plot: sparse attention is 40% lower in p95 latency at equal accuracy."
  callouts:
    - "40% lower p95 latency"
    - "<0.5% accuracy drop"
sources:
  - id: S3
    footnote: "Chen et al., NeurIPS 2025, doi:10.…"
sets_up: [S08]
pays_off: [S04]
reentry_anchor: false
skip_if_behind: false
backup: false
```

### 6.2 Headline rules

- One complete declarative sentence, **8–14 words**, ≤2 lines at the chosen font.
- Contains a verb and a result, comparison, or mechanism. No topic labels ("Method", "Results", "Data").
- No question headlines; no colon-only constructions ("Results: …" fails).
- If the sentence needs "and" to hold two claims, split the slide.
- The headline must be the slide's takeaway, not its category. A reader who reads only headlines should get the
  argument.

Automated lint rejects: `outline|agenda|motivation|method|results|discussion|conclusion|thank you|questions|
backup` as the whole headline; word count outside 8–14; no verb (a small POS heuristic or a required
result/comparison token).

### 6.3 Body rules

- **One visual** per content slide: chart, diagram, equation breakdown, before/after, or concrete example.
- At most **two** call-out annotations; each ≤12 words; no paragraph.
- No bullet list longer than two items; no full-sentence bullets; if the content is a list, make it a diagram or
  a table with ≤4 rows.
- Equations typeset in LaTeX/KaTeX, numbered if referenced later; define every symbol on the slide or earlier.
- A dense table is allowed only when precision matters and the table has ≤4×4 cells; otherwise chart the trend.
- Every slide numbered (including backup), bottom-right, same position.
- Every borrowed figure/number/citation has a footnote on that slide with author-year and a clickable DOI/URL.
  A references slide may exist in backup as an index, but it is never the only citation mechanism.

### 6.4 Archetypes (use deliberately)

| Archetype | What it does | Avoid |
|---|---|---|
| **Hook** | Names the problem in the audience's terms; ends with the ≤25-word finding. | A history of the field. |
| **Tension** | Shows why the obvious approach fails; one visual of the gap. | A literature review. |
| **Insight** | The one idea, revealed in one diagram. | Three ideas at once. |
| **Method** | The minimum mechanism needed to believe the result; progressive disclosure. | The full system diagram. |
| **Evidence** | One chart, baseline vs ours, units, delta annotated. | A table of every metric. |
| **Limitation** | The strongest counter-evidence, stated before the reviewer asks. | A token "limitations" bullet. |
| **Signpost** | "Where we are" after a deep section; no new terms. | A generic agenda. |
| **Take-home** | The finding + 2–3 small visual reminders; stays up during Q&A. | "Thank you" / "Questions?" |
| **Backup** | Derivation, ablation, secondary baseline, extra data; numbered B1…Bn. | New main-arc claims. |

**Progressive complexity:** build a complex diagram in 2–4 overlays (Beamer `\pause`/`\onslide`, Reveal
fragments) with no motion; each overlay adds one element and one sentence. If the audience cannot reconstruct the
whole from the last overlay, the decomposition is wrong.

**Closing:** the last visible slide is the take-home summary, not a thank-you. It repeats the finding and shows
small thumbnails of the two decisive figures. Q&A stays on it.

**Backup:** after the closing, numbered B1…Bn, each with a full assertion headline and a source footnote. The
Q&A map in §10 points to them by number.

### 6.5 Format and theme

Choose one:
- **LaTeX Beamer** (preferred for equations and print): 16:9, `\documentclass[aspectratio=169]{beamer}`,
  a minimal theme (Metropolis or a custom one), `\setbeamertemplate{navigation symbols}{}`, slide numbers in the
  footline.
- **HTML**: Reveal.js, Slidev, or Quarto. Use KaTeX for equations, a print stylesheet, and a PDF export for the
  archive.

**Theme tokens (both formats):**

```css
--bg:        #FAF9F6;   /* warm cream; lit rooms */
--ink:       #1A1A1A;
--muted:     #5A5A5A;
--accent-1:  #0072B2;   /* Okabe–Ito blue */
--accent-2:  #D55E00;   /* vermillion */
--accent-3:  #009E73;   /* bluish green */
--accent-4:  #CC79A7;   /* reddish purple */
--font:      "Source Sans 3", "Inter", "IBM Plex Sans", sans-serif;
```

- Title 28–36 pt; body 18–24 pt; diagram labels ≥14 pt; footnotes 12–14 pt (the one allowed exception, still
  legible).
- Contrast ≥4.5:1 for body, ≥3:1 for large text; verify with the script in §11.2.
- Never encode meaning by color alone: pair color with shape, line style, or a direct label.
- No flashing, no auto-advance, no decorative stock imagery, no background patterns behind text.
- Alt text for every figure; captions state the takeaway.

**G3 pass:** every slide has a spec; headline lint clean; one visual + ≤2 callouts; slide numbers; footnotes for
every borrowed number/citation; fonts and palette within tokens; closing is take-home; backup numbered; the deck
compiles. **Fail:** return to the offending spec; do not render for audit until clean.

---

## 7. P4 — Cross-slide forward-dependency and terminology audit (HITL required)

**Purpose.** Catch the failure mode models are worst at: a term used on Slide 12 that was never defined, or a
symbol introduced after it was needed.

### 7.1 Extract the ledger

For every slide, extract every **term, acronym, symbol, metric, dataset, baseline name, and domain concept** that
carries meaning. For each, record:

```csv
term,kind,first_used_slide,first_defined_slide,definition_evidence,status,fix
"p95 latency",metric,S07,S07,"S07 callout: p95 = 95th percentile",ok,
"sparse attention",concept,S06,S04,"S04 diagram label + one-line definition",ok,
"FLOPs",acronym,S11,,,violation,"define on S04 or spell out on S11"
"α",symbol,S09,S09,"S09 equation 2, defined inline before use",ok,
```

Rules:
- `first_defined_slide` must be **≤** `first_used_slide`.
- A same-slide definition is allowed only if the definition appears **before** the first argumentative use on
  that slide (e.g., an equation line above the prose), and the reviewer confirms it visually.
- A term defined only in the transcript is a **violation for the slide deck**, because the deck must stand alone
  during Q&A; move the definition to the slide or a backup.
- A term defined only in backup is a violation for the main deck unless the main slide explicitly says "defined
  in B3" and the term is not needed to follow the main argument.
- Every violation gets a `fix` naming the slide and the exact change.

### 7.2 Automated extraction and check

Run `dependency_audit.py` (§11.3). It:
1. tokenizes slide text, headlines, figure labels, and equations;
2. matches against the glossary of defined terms and symbols;
3. builds the ledger and flags `missing` or `defined_after_use`;
4. writes `audits/dependency_report.json` with a violation count and the exact slides.

The script is necessary but not sufficient: it will miss synonyms, implicit metrics, and visual-only labels.
That is why the next step is human.

### 7.3 HITL-2: the dedicated dependency review

This checkpoint is **only** about dependencies. Do not combine it with narrative or visual review.

The reviewer:
1. reads the ledger, not the slides first;
2. opens every flagged slide and the slide where the definition should be;
3. for every `ok` row, spot-checks 20% to confirm the definition is real and precedes use;
4. signs `audits/dependency_report.json` with `reviewed_by`, `reviewed_at`, and `violations: 0`.

**G4 pass:** zero violations, 20% spot-check clean, HITL-2 signed. **Fail:** route the exact fixes to P3 (slide
drafting), re-run the ledger, and re-enter G4. Do not proceed to rendering with an open violation.

---

## 8. P5 — Render and persona-based visual audit

**Purpose.** See the deck as the room sees it, and let the named reviewer ask the questions they will actually
ask.

### 8.1 Render every slide

- Beamer: `latexmk -pdf` → `pdftoppm -png -r 150 build/main.pdf audits/render/slide`.
- Reveal/Slidev/Quarto: Playwright (or the tool's export) → one PNG per slide at 1600×900 or 1920×1080.
- Verify: one image per slide, including backup; no blank renders; no clipped text.

### 8.2 Automated visual checks

For each image:
- text overflow / elements outside the safe area (vision model or bounding-box check);
- contrast ratio of text vs background (sample the actual pixels; §11.2);
- minimum font size from the PDF text layer (Beamer) or the spec (HTML);
- image resolution ≥150 dpi at display size;
- slide number present;
- no more than two callouts;
- chart axes have labels and units (vision check).

Write `audits/visual_report.json` with per-slide findings and severities.

### 8.3 Persona-anchored critique (multimodal)

For **each** rendered slide, the auditor receives: the image, the slide spec, the persona, the audience, the time
slot, and the arc position. The prompt is in §13.6. It must return:

1. **Visual inspection:** layout balance, overflow, legibility, chart clarity, color-only encoding.
2. **Obvious questions:** 3–5 blunt questions the persona would ask *at this slide* (not generic).
   Examples: "What are the units on the y-axis?", "Why this baseline and not the stronger one?", "Isn't this
   confounded by the dataset size?", "Why should I care about this step?", "Where is the error bar?".
3. **Resolution per question:** `edit_slide` (with the exact edit), `transcript` (with the sentence to say), or
   `backup` (with the backup slide number and its content). A question with no resolution is a failed audit.
4. **Severity:** blocker / should-fix / nice-to-have.

Rules:
- Do not ask for "make it the best slide"; ask for the persona's specific objections.
- A blocker must be fixed before G5; a should-fix must be fixed or explicitly accepted by the presenter.
- If the persona's question reveals a missing claim or evidence (not just a visual), route to P1/P3, not to
  cosmetics.

### 8.4 HITL-3: spot-check

The presenter reviews:
- 100% of blocker slides;
- 20% random sample of the rest;
- the list of questions resolved by "transcript" (they must be true and sayable).

**G5 pass:** every slide rendered; automated checks clean or accepted; every obvious question resolved; zero
open blockers; HITL-3 signed. **Fail:** route to P3 (slide edit) or P6 (transcript answer) or P1 (missing
evidence); re-render the affected slides; re-enter G5.

---

## 9. P6 — Speaker transcript and delivery kit

**Purpose.** The deck is half the talk. This phase produces the words, the timing, and the Q&A defense.

### 9.1 Budget

```
total_words   = speaking_minutes * 0.9 * 140     # 130–150 wpm; finish at ~90%
per_slide     = minutes_for_slide * 140
```

Write `transcript/timing.csv`:

```csv
slide,minutes,words_target,words_actual,cumulative_min,skip_if_behind
S01,1.0,140,138,1.0,false
S02,1.5,210,204,2.5,false
S07,2.0,280,276,10.5,true
...
```

### 9.2 Structure per slide

- **Opening 45 seconds (S01–S02): verbatim.** Hook + the ≤25-word finding. Written word-for-word; no improvisation
  under pressure.
- **Middle: conversational gloss, not a script.** Each slide gets 3–6 cues: what the visual shows, the one
  comparison, the transition sentence. The gloss elaborates; it does not read the slide.
- **Closing 30 seconds: verbatim.** The finding, the limitation, the implication, the take-home.
- **Figure narration cue** for every chart/diagram: "The x-axis is …; the y-axis is …; the grey line is the
  baseline; our point is …; the gap is …; the reason is …." State axes before the takeaway.
- **Bridge** to the next slide in one sentence: "Because of X, the next question is Y."

Automated check: n-gram overlap between the gloss and the slide text must be <60%; above that, rewrite the gloss
(it is reading the slide). Word counts within ±10% of target; total ≤90% of the slot.

### 9.3 Live time management and Q&A

- Tag **1–2 slides** `skip_if_behind: true` with a one-line bridge that preserves the arc. Never skip the hook,
  the core evidence, or the take-home.
- Write `transcript/qa.md` with the **3–5 toughest questions** the persona will ask. For each:
  - the question in the persona's voice;
  - a ≤90-second answer;
  - the evidence slide or backup number that supports it;
  - the trap to avoid (e.g., "do not compare to the weaker baseline").
- Rehearse once with a timer; record actual minutes per slide; if over 90%, cut from the skip-tagged slides or
  from background, not from evidence.

**HITL-4 (rehearsal and timing sign-off):** the presenter rehearses aloud once, with a timer, and confirms:
- the opening 45 seconds and closing 30 seconds are sayable without reading;
- every figure narration names the axes before the takeaway;
- the total is ≤90% of the slot, and the skip-tagged slides still leave a coherent arc;
- the Q&A answers are ≤90 seconds and point to the right backup slides.

They sign `transcript/timing.csv` with `rehearsed_by`, `rehearsed_at`, and the measured total.

**G6 pass:** word budget within range; total ≤90%; 45 s open + 30 s close verbatim; every figure has a narration
cue; ≥1 skip tag with a bridge; 3–5 Q&A with backup links; overlap check <60%. **Fail:** return to the transcript
step; do not touch the deck unless the timing failure is structural (then route to P2).

---

## 10. P7 — Final holistic verification gate

**Purpose.** Judge the whole artifact as one continuous experience, not slide by slide.

### 10.1 The three checks (all must pass)

| Check | Question | Evidence required |
|---|---|---|
| **Cohesivity** | Does the deck flow from S01 to the closing with zero broken transitions, zero orphan slides, and zero unresolved forward dependencies? | Transition sentences for every adjacent pair; orphan check = 0; dependency report = 0 violations. |
| **Contextuality** | Is rigor, depth, tone, and framing calibrated to this audience, this time slot, and this persona? | Persona audit accepted; jargon list matches `must_not_assume`; time budget within 90%; the persona's top question is answered on-slide or in Q&A. |
| **Sense-making** | Could a knowledgeable researcher outside the sub-niche, seeing only these slides and hearing only the transcript, restate the problem, method, and finding without hallucinating a missing step? | A blind reader (fresh context) writes the three sentences; compare to the intended finding; any gap is a failure. |

The blind-reader test is the strongest check. Give a fresh agent the PDF/HTML and the transcript, not the source
paper, and ask for: (1) the problem, (2) the method, (3) the finding, (4) the one thing they are unsure about. If
the three sentences do not match the intended arc, the deck fails sense-making even if every slide is beautiful.

### 10.2 Diagnostic routing (mandatory on failure)

| Failure | Root cause | Route to | Re-run |
|---|---|---|---|
| Broken arc, poor proportion, orphan slide | Narrative structure | **P2** (arc/slide plan) | P3 → P4 → P5 → P6 → P7 |
| Undefined term/symbol, dependency violation | Drafting or definition placement | **P3** (slide specs) and **P4** (ledger) | P4 → P5 → P6 → P7 |
| Visual ambiguity, unaddressed skepticism, missing evidence | Slide design or evidence | **P3** (spec) and **P5** (render/persona); if evidence is missing, **P1** | P3/P5 → P6 → P7 |
| Desynchronized explanation, weak bridge, pacing | Transcript | **P6** | P6 → P7 |

Do not patch a cohesivity failure by editing one slide's text. If the arc is broken, go back to P2. If the
dependency chain is broken, go back to P3/P4. Superficial patching is how a deck ends up with a beautiful slide
that does not belong.

### 10.3 Loop guardrail and escalation

- Per gate: max **2 remediation attempts**.
- Whole deck: max **3 full cycles** (G2→G7).
- If the limit is reached without a clean pass: **stop**. Surface a one-page diagnosis to the presenter:
  - the failing check and the exact slides;
  - the root cause;
  - the attempted fixes and why they did not work;
  - the unresolved trade-offs (e.g., "the evidence for the stronger claim does not exist; either weaken the
    claim or add the experiment");
  - a recommended decision, with the two acceptable options.

**HITL-5 (escalation decision):** the presenter decides. The procedure never ships a deck that fails the gate
silently. The escalation record goes in `audits/final_gate.json` with the diagnosis, the attempts, and the
chosen trade-off.

**G7 pass:** all three checks pass with evidence, retries within limits, presenter signs. **Fail:** route as
above; do not deliver.

---

## 11. Automated checks (reference implementations)

These are deliberately small and dependency-light. Adapt paths; keep the pass/fail semantics.

### 11.1 `lint_spec.py` — headlines, budget, citations

```python
import csv, json, re, sys, yaml
from pathlib import Path

BANNED = {"outline","agenda","motivation","method","methods","results","discussion",
          "conclusion","conclusions","thank you","questions","backup"}
def words(s): return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", s or ""))

specs = [yaml.safe_load(p.read_text()) for p in sorted(Path("slides/spec").glob("*.yaml"))]
fails = []
for s in specs:
    h = (s.get("headline") or "").strip()
    if h.lower().strip(" .:") in BANNED: fails.append((s["id"], "generic headline"))
    if not 8 <= words(h) <= 14: fails.append((s["id"], f"headline words={words(h)}"))
    if not s.get("sources") and s.get("archetype") not in ("hook","signpost","take_home"):
        fails.append((s["id"], "no source footnote"))
    if len((s.get("visual") or {}).get("callouts") or []) > 2:
        fails.append((s["id"], "more than 2 callouts"))
    if s.get("backup") and s.get("archetype") != "backup":
        fails.append((s["id"], "backup flag without backup archetype"))
main = [s for s in specs if not s.get("backup")]
print(json.dumps({"slides": len(specs), "main": len(main), "fails": fails}, indent=2))
sys.exit(1 if fails else 0)
```

### 11.2 `contrast.py` — WCAG

```python
def lum(hex_):
    r,g,b = [int(hex_[i:i+2],16)/255 for i in (1,3,5)]
    f = lambda c: c/12.92 if c <= .03928 else ((c+.055)/1.055)**2.4
    return .2126*f(r)+.7152*f(g)+.0722*f(b)
def ratio(a,b):
    la,lb = sorted([lum(a),lum(b)], reverse=True)
    return (la+.05)/(lb+.05)
# body text needs >= 4.5, large text >= 3.0
assert ratio("#1A1A1A", "#FAF9F6") >= 4.5
```

### 11.3 `dependency_audit.py` — the ledger

```python
import csv, json, re, yaml
from pathlib import Path

specs = [yaml.safe_load(p.read_text()) for p in sorted(Path("slides/spec").glob("*.yaml"))]
glossary = yaml.safe_load(Path("narrative/glossary.yaml").read_text())  # term -> {definition, slide}
rows, violations = [], []
for s in specs:
    text = " ".join([s.get("headline",""), str(s.get("visual",{})), str(s.get("notes",""))])
    for term, meta in glossary.items():
        if re.search(rf"\b{re.escape(term)}\b", text, re.I):
            used = s["id"]; defined = meta.get("slide")
            ok = defined is not None and defined <= used
            rows.append([term, meta.get("kind","term"), used, defined or "", meta.get("evidence",""),
                         "ok" if ok else "violation", "" if ok else f"define on or before {used}"])
            if not ok: violations.append({"term": term, "used": used, "defined": defined})
Path("audits").mkdir(exist_ok=True)
with open("audits/dependency_ledger.csv","w",newline="",encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["term","kind","first_used_slide","first_defined_slide","definition_evidence","status","fix"]); w.writerows(rows)
Path("audits/dependency_report.json").write_text(json.dumps({"violations": violations}, indent=2))
raise SystemExit(1 if violations else 0)
```

### 11.4 `time_budget.py`

```python
import csv, sys
rows = list(csv.DictReader(open("transcript/timing.csv", encoding="utf-8")))
total = sum(float(r["minutes"]) for r in rows)
words = sum(int(r["words_actual"]) for r in rows)
slot = float(sys.argv[1])
ok = total <= 0.9 * slot and 130 <= words / max(total, 0.1) <= 150
print({"minutes": total, "slot": slot, "wpm": words / max(total, 0.1), "ok": ok})
sys.exit(0 if ok else 1)
```

### 11.5 Render commands

```bash
# Beamer
latexmk -pdf -interaction=nonstopmode -outdir=build slides/main.tex
pdftoppm -png -r 150 build/main.pdf audits/render/slide

# Reveal / Quarto (Playwright)
npx playwright screenshot --viewport-size=1600,900 build/index.html audits/render/slide-01.png
```

For a full-deck capture, use a small Playwright script that presses the next-slide key and screenshots each
fragment state; one PNG per slide, no skipped overlays.

---

## 12. Artifact schemas

### 12.1 `intake.yaml`
See §2.1. Required keys: `mode`, `speaking_minutes`, `qa_minutes`, `audience`, `reviewer_persona`,
`core_finding_one_sentence`, `approved_by`, `approved_at`.

### 12.2 `evidence/claims.csv`
`claim_id, claim_text, source_id, source_locator, quote_or_number, confidence, used_in, notes`.
`used_in` is a slide id or `backup:B3` or `cut`. A claim with `used_in` empty is a cut and must be justified by
the 65–70% rule.

### 12.3 `narrative/glossary.yaml`
```yaml
"p95 latency":
  kind: metric
  definition: "95th percentile of request latency; lower is better."
  slide: S04
  evidence: "S04 callout, one line"
"α":
  kind: symbol
  definition: "attention sparsity ratio (0 = dense, 1 = fully sparse)."
  slide: S09
  evidence: "S09 equation 2, defined above use"
```

### 12.4 `audits/dependency_report.json`
```json
{
  "violations": [],
  "reviewed_by": "…",
  "reviewed_at": "2026-11-22T…",
  "spot_check": {"sampled": 12, "clean": 12}
}
```

### 12.5 `audits/visual_report.json`
```json
{
  "slides": [
    {"id": "S07", "render": "audits/render/slide-07.png",
     "visual_issues": [{"severity": "blocker", "what": "y-axis has no units"}],
     "obvious_questions": [
       {"q": "What are the units on the y-axis?", "resolution": "edit_slide",
        "fix": "label y as 'accuracy (%)'"},
       {"q": "Why this baseline?", "resolution": "transcript",
        "say": "We use the dense kernel from the same release; the stronger baseline is in B2."}
     ]}
  ],
  "open_blockers": 0
}
```

### 12.6 `audits/final_gate.json`
```json
{
  "cohesivity": {"pass": true, "evidence": "14/14 transitions written; 0 orphans; 0 dependency violations"},
  "contextuality": {"pass": true, "evidence": "persona's top question answered on S09; 18 min of 20"},
  "sense_making": {"pass": true, "evidence": "blind reader restated problem/method/finding; one uncertainty = limitation, stated on S13"},
  "cycles": 1,
  "signed_by": "…",
  "signed_at": "…"
}
```

---

## 13. Prompt library (copy, fill, run)

Each prompt states the role, the inputs, the output schema, and the refusal rule. The refusal rule is what stops
invention.

### 13.1 Research scoping (Mode A)

> You are a research strategist. Input: `intake.yaml`. Produce `narrative/scope.md` with: the problem in one
> sentence a non-specialist can repeat; the research question "Does X affect Y under Z?"; the gap with 2–3
> citations; a falsifiable contribution claim containing a comparison and a metric; the boundary (what this talk
> will not claim). Do not invent citations. If the gap cannot be supported, say so and propose the smallest
> literature check that would settle it.

### 13.2 Distillation (Mode B)

> You are a compression editor for an academic talk. Input: the source material and `intake.yaml`. Produce
> `evidence/claims.csv` with every claim/number/baseline/metric/citation and its locator. Then classify each as
> MUST/SUPPORT/BACKUP/CUT so the main deck retains 30–35% of source claims and 30–35% of source words. Preserve
> every number, unit, baseline, and caveat verbatim. If a needed fact is missing, add it to
> `sources/open_questions.md`; never fill it with a plausible value. Return the cut ratio by claims and by words.

### 13.3 Narrative

> You are a narrative architect. Input: the claim inventory and `intake.yaml`. Produce `narrative/arc.md` and
> `slide_plan.yaml` using the five beats (hook, tension, insight, evidence, take-home), the proportional budget
> (background ≤15%, core ≥50%), and a `sets_up`/`pays_off` edge for every slide. Every adjacent pair must have a
> one-sentence "because X, we need Y" transition. Flag any slide that is an orphan. Do not add a generic outline
> slide. The hook must contain the ≤25-word finding.

### 13.4 Slide specs

> You are a slide designer working in the assertion-evidence tradition. Input: one planned slide + its claims.
> Produce a YAML spec with a declarative headline of 8–14 words, one visual, ≤2 callouts, a source footnote with
> DOI/URL for every borrowed number, and the `sets_up`/`pays_off` edges. If the visual would need more than two
> callouts, split the slide. If the headline needs "and", split the slide. Do not use a topic label as a
> headline.

### 13.5 Dependency extraction

> You are a terminology auditor. Input: all slide specs and `narrative/glossary.yaml`. For every term, acronym,
> symbol, metric, dataset, and baseline, report first use and first definition. A definition must be on the same
> slide *before* the first argumentative use, or on an earlier slide. Output the ledger CSV and a violation list
> with the exact fix. Do not accept "defined in the transcript" as a definition for the deck.

### 13.6 Persona visual audit (one slide at a time)

> You are [persona: name/role, field, methodological biases, pet peeves, what convinces them]. You are watching a
> [audience] talk of [speaking] minutes. This is slide [N] of [M], in the [beat] section. The speaker says:
> [transcript gloss]. Look at the image and answer as this person, not as a generic design critic:
> 1. What is visually unclear, overflowing, too small, or misleading? (layout, axes, units, contrast, chart)
> 2. Ask the 3–5 most obvious, blunt questions you would ask at this slide. No generic questions.
> 3. For each question, resolve it: edit the slide (exact edit), say it in the transcript (exact sentence), or
>    add/point to a numbered backup slide (what it must contain).
> 4. Severity: blocker / should-fix / nice-to-have.
> Do not ask for "perfection". If the slide's evidence is missing, say so and route it to the evidence phase.

### 13.7 Transcript

> You are a speechwriter for a scientist. Input: the slide specs, the arc, and `intake.yaml`. Write
> `transcript/transcript.md` at 130–150 wpm, finishing at ~90% of the slot. The first 45 seconds and the last 30
> seconds are verbatim; the middle is conversational cues, not a script. For every chart, state axes first, then
> the comparison, then the takeaway. Add one bridge sentence per slide. Mark 1–2 slides `skip_if_behind`. Do not
> read the slide text: keep n-gram overlap below 60%.

### 13.8 Final gate

> You are the external examiner. Input: the built deck, the transcript, the persona, and the three gate
> definitions. Answer pass/fail with evidence for cohesivity, contextuality, and sense-making. For sense-making,
> read only the deck and transcript, then write the problem, method, and finding in three sentences; compare to
> the intended finding. If any check fails, name the exact slides and route the fix to P2/P3/P4/P5/P6 per the
> routing table. Do not soften a failure into a suggestion.

---

## 14. Failure modes and the fix

| Failure | Symptom | Fix |
|---|---|---|
| **The summary deck** | Every paper section survives; 30 slides for 15 minutes. | Enforce the 65–70% rule by claims and words; cut BACKUP/CUT classes. |
| **The orphan slide** | A beautiful slide that neither sets up nor pays off. | Add the `sets_up`/`pays_off` edge or cut it; re-run the arc check. |
| **The undefined symbol** | α appears on S09, defined on S11. | Dependency ledger; move the definition to S04 or S09-before-use. |
| **The bullet wall** | Five bullets, each a full sentence. | Replace with one chart/diagram; ≤2 callouts. |
| **The generic outline** | "Motivation → Method → Results → Conclusion" on slide 2. | Delete; hook with the problem and the ≤25-word finding. |
| **The thank-you ending** | Last slide is "Thank you / Questions?". | Replace with take-home + two figure thumbnails; move thanks to the transcript. |
| **The unlabeled chart** | No units, no baseline, no delta. | Chart spec requires x/y units, baseline, ours, annotation. |
| **The confident invention** | A number that is not in the source. | Claims ledger; `UNVERIFIED` cannot enter a slide; persona audit asks for the source. |
| **The reading-aloud transcript** | Gloss repeats the slide text. | Overlap <60%; rewrite as elaboration and figure narration. |
| **The overrun** | 22 minutes for a 20-minute slot. | 90% budget; skip tags; cut background, not evidence. |
| **The cosmetic patch** | A cohesivity failure "fixed" by rewording one slide. | Route to P2; rebuild the arc. |

---

## 15. Acceptance tests (used to validate this procedure)

Run these three cold-start cases. A procedure version is accepted only if all three produce the required
artifacts and pass G7 without exceeding the retry limit.

### Case 1 — Mode A, specialist conference (20 + 10)

Prompt: "Make a 20-minute NeurIPS-style talk from my idea: sparse attention should cut inference latency without
hurting accuracy. Audience: systems + ML specialists. Reviewer persona: a systems professor who distrusts
wall-clock speedups without error bars and hates undefined axes."

Expected: `intake.yaml` with the persona; `scope.md`; ≥12 sourced claims; a figure plan with baseline/ours/units;
16–24 main slides; 0 dependency violations; a rendered visual report; ~2,520-word transcript at 130–150 wpm;
3–5 Q&A with backup links; G7 all pass.

### Case 2 — Mode B, thesis defense (12 + 8)

Prompt: "Turn Chapter 4 of my thesis (attached) into a 12-minute defense talk. Committee: mixed department, one
methods chair who asks about confounders. Cut the paper detail hard but do not change a single number."

Expected: claims inventory with locators; 65–70% cut by claims and words; preservation invariants checked; 10–14
main slides; limitation slide before the committee asks; dependency ledger signed; persona audit resolves the
confounder question on-slide or in backup; ~1,510-word transcript; G7 all pass.

### Case 3 — Mode B, lightning talk (5 + 3)

Prompt: "Make a 5-minute industry lightning talk from this paper and my lab notes. Audience: practitioners.
Reviewer persona: an industry engineer who cares about cost and deployment, not proofs."

Expected: 4–6 main slides; hook in the first 30 seconds; one cost/deployment chart; backup for the proof; 630-word
transcript; 1 skip tag; G7 all pass.

**Rubric for the procedure itself (meta):** completeness (all 8 brief clusters), enforceability (every gate has a
machine check or a named human), repeatability (cold start produces the same artifact set), artifact specificity
(paths and schemas), HITL clarity (who signs what), and failure routing (every failure has an earliest
responsible phase).

---

## 16. Definition of done (delivery manifest)

Deliver all of the following, or do not call it done:

- [ ] `intake.yaml` signed (mode, time, audience, persona)
- [ ] `evidence/claims.csv` + `citations.csv` complete; no `UNVERIFIED` in the main deck
- [ ] `narrative/arc.md` + `slide_plan.yaml` with transitions and edges; G2 signed
- [ ] `slides/spec/*.yaml` lint-clean; `build/main.pdf` or `build/index.html` compiles
- [ ] `audits/dependency_ledger.csv` + report with **0 violations**, HITL-2 signed
- [ ] `audits/render/*.png` for every slide; `visual_report.json` with 0 open blockers, HITL-3 signed
- [ ] `transcript/transcript.md` + `timing.csv` + `qa.md`; total ≤90% of slot; 45 s/30 s anchors
- [ ] `audits/final_gate.json` with all three checks pass, signed
- [ ] `MANIFEST.md` listing the files, the gate results, the retry count, and any accepted trade-offs

**The final rule.** If the deck fails cohesivity, contextuality, or sense-making, it is not delivered. It is
routed, rebuilt, and re-gated. A deck that is beautiful but cannot be restated by a knowledgeable outsider is a
failed deck.
