# Q-STEP6-CCBY: should CC-BY-4.0 be on the ingestion allowlist?

Raised: 2026-09-28 · by: step 6 build · status: **OPEN — blocking step 6's Source A**
Founder ruling needed. Proceeding under the stated default meanwhile (CC-BY-4.0 stays
quarantined), which is the safe reading.

## The question

Step 6's Source A is the Zenodo GitHub Actions workflow-history corpus
(`10.5281/zenodo.10259013`, current record `20340547`). Its license is
**CC-BY-4.0**, confirmed on every version record.

`repo_license_policy.DEFAULT_ALLOWLIST` contains exactly eight ids:

    MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense, CC0-1.0

`classify_spdx("CC-BY-4.0")` therefore returns **QUARANTINE**, with the reason
*"CC-BY-4.0 is not on the disclosed permissive allowlist"*. **Source A as specified
ingests zero items.** Measured, not predicted: a 1,000-item local-shard pilot
returned `license_verdicts: {QUARANTINE: 1000}` and `would_ingest_without_license_gate: 0`.

This is the allowlist working correctly, not a bug. Its own docstring says extending
it is "a deliberate ruling about a license nobody has judged yet."

## Options

**(a) Add `CC-BY-4.0` to the allowlist.** CC-BY permits adaptation and commercial
use with attribution; it is not copyleft, not non-commercial, not no-derivatives.
`repo_license_policy.REJECT_FAMILIES` already excludes `CC-BY-NC` and `CC-BY-ND`, so
the copyleft/NC floor is unaffected. Attribution would need a recorded obligation
(creator, title, DOI) on every derived Procedure.

**(b) Leave it quarantined; drop Source A.** Costs the whole 1,000-item-per-1,000
coverage goal of step 6, and 52.9K repositories of real, permissive-licensed CI
configuration. Note the gap this leaves: a per-repo license *is* resolvable via the
GitHub API (Source B does exactly that), so (b) throws away items whose license we
could in fact establish per repository.

**(c) Treat the corpus as a discovery index only, and re-fetch per repo.** Read the
Zenodo CSV purely to learn *which* repositories and workflow files exist, then
resolve each repository's real license from GitHub before ingesting any content.
Never stores Zenodo-sourced content; the Zenodo record is provenance for *selection*,
not for *content*. This is the option that maximises coverage while keeping the
per-item license decision honest, and it reuses code step 6 already has.

**Proposed default: (c).** It is the only option that satisfies both hard rules —
"license per item, never per compilation" (Common rules) and the allowlist's
purpose — without a founder widening a frozen policy. It costs one extra API call
per repository, which step 6's rate limiter already budgets for.

## What I need

A ruling on (a) / (b) / (c). Under (c) I additionally need confirmation that
"Zenodo as a selection index, GitHub as the license authority" is acceptable, since
it is a deliberate narrowing of the plan's intent.

## Related

- The separate question this does *not* settle: whether an abstract *method*
  extracted from a CC-BY-4.0 file is a derivative work. That is a legal question,
  not an engineering one, and I have not guessed at it.
- `screening.CHECK_TYPES` cannot accept an `actionlint`/`zizmor` member without a
  migration plus a ruling, so step 6 attaches **no** step-4 verifier check. Tracked
  under step 4, not here.
