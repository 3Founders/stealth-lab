# Deviations from the preregistration

Dated entries, each with its reason. Nothing here may be removed.

## 2026-10-07 -- before the design was frozen (nothing built, run or graded)

1. **Local tier provider `stub` -> `survey`.** `experiment.json` said to switch once workstream C's scanner
   merged and to log the switch here before any scored notes are built. It merged on main (`8afc793`), so the
   frozen design uses `providers.local = "survey"`. No note has been built with either provider.
2. **`analyze.py` syntax error fixed.** The committed file had a string literal broken across lines (it did not
   compile), so `analyze.py` could not have run. Only the literal was repaired; no analysis logic changed.
3. **Scanner's default history window.** The scanner's own default window changed from "2 years before today" to
   "2 years before HEAD's commit" (3-hard.md, fork C item 1). This experiment passes its window explicitly
   (730 days before the base commit, `survey_runner.mjs`), so its notes are unaffected.
