---
name: grant-apply
description: Draft tailored applications for grants, fellowships, and accelerators from the master profile + answer bank, and sweep funding sources for new programs. Use when asked to "apply to X", "draft an application", "sweep grants", or when a program URL is shared.
---

Two modes.

## Mode 1 — `apply to <program>` (or a program URL is given)

1. **Fetch the live page/form** (webfetch). Extract: questions, deadline, eligibility, deal terms, file-format rules, word limits.
   - If the form is JS-only/login-walled/blocked (401 etc.): STOP, report what's blocked, ask the user to paste the questions. Never invent questions.
2. If the page is thin on terms/deadline, run one websearch pass for third-party verification; cite it in the draft header.
3. Read `GRANTS/MASTER_PROFILE.md` (source of truth) and `GRANTS/answer_bank.md`.
4. Write `GRANTS/applications/<program-slug>/draft.md` containing:
   - Header: program · deadline · verified-terms · source URLs · date checked
   - Field-by-field draft answers mapped to the actual form fields
   - Reuse answer-bank content adapted to the question's wording
5. End every draft with a **Human-only checklist**: CAPTCHA/submission, signature, personal-story polish, attachments to upload, account creation.
6. Update the program's row in `FUNDING_APPLICATIONS.md` (status → `DRAFTED`, add draft path).
7. Feed any genuinely new Q→A back into `answer_bank.md`.

Never auto-submit. Never fabricate metrics — copy numbers only from MASTER_PROFILE.

## Mode 2 — `sweep grants`

1. Hit these sources in order: questd.ai `/residencies` `/fellowships` `/accelerators` · cleverhack.com/2026-ai-startup-founder-resources (deadlines section) · startupfunds.in (equity + grants pages) · startupgrantsindia.com/industry/ai-ml · Antler blog · websearch for "@residencyBLR OR new AI founder cohort India" recent posts.
2. Diff findings against the tracker in `FUNDING_APPLICATIONS.md` (new programs? deadline changes? closed windows?).
3. Append a dated section to `GRANTS/discovery_log.md`: NEW / CHANGED / EXPIRED rows with source links.
4. Surface only deltas in your reply — full detail goes to the log.
