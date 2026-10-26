# Series10 thresholds

Set by Anuj on 2026-10-24, **before the run started and before any result was seen.** docs/series10_prereg.md
applies these exactly as written; they are not to be changed after results exist.

**What these numbers are:** Anuj's judgment of "good enough" for each area, not a statistical test. With 10 issues
per repo, no threshold could be. The report applies them as written and gives each area's verdict under them. The
final reading of the results also looks at the full tables, and any judgment beyond these verdicts is written
separately and labelled as such, next to them, never in place of them.

**Rules for every area:**
- An area counts as **shown** only if its difference points the same way in **every repo**, meets the threshold below
  on the **pooled** numbers, and the **solved count does not drop** in the arm being credited.
- "Per 10" means per 10 issues: pooled over 2 repos (20 issues), +1 solved per 10 means at least +2 solved in total.
- Costs are total cost per arm (Claude + open models), including one-time costs (the setup survey) and failed cheap
  attempts.

Reduce: C costs at least 15% less than A, with at least as many issues solved.
Recall: C-R solves at least 1 more issue per 10 than C0, or costs at least 10% less than C0 with the same number solved.
Reuse: C-L solves at least 1 more issue per 10 than C-R, or costs at least 10% less than C-R with the same number solved.
Route learning: C costs at least 10% less than C-L, or has fewer wasted cheap attempts than C-L in issues 6-10, with the same number solved.
Tests decide: wrong fixes applied (graded by the hidden tests) are at most 2% of all accepted cheap fixes, across all kell arms in the whole run.

**Note on Tests decide:** 2% is a rate, so with fewer than 50 accepted cheap fixes in the run, any single wrong fix
applied exceeds it. Report the count and the denominator.
