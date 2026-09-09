# Cookies & Tracking

**STATUS: DRAFT — REQUIRES LEGAL REVIEW.**

This document describes only what was actually found in the `frontendv1` codebase during this
audit (2026-09-09) — it does not describe an aspirational or planned tracking program.

## What we found

- **No cookie usage found.** A repo-wide search of `frontendv1/src` for `document.cookie` found
  no matches.
- **`sessionStorage` — used for one thing: a fallback "viewer id" identity value** used before
  full authentication is wired in (`frontendv1/src/lib/auth.ts`). This is functional/essential
  storage (it identifies your session to the backend), not tracking or analytics.
- **Supabase Auth session client** (`frontendv1/src/lib/supabase/client.ts`) persists your
  authentication session and handles token auto-refresh, using Supabase's standard browser client
  behavior (typically `localStorage`-backed). This is essential to staying signed in and is not
  used for tracking third parties.
- **No analytics or tracking SDKs found.** A search for common analytics/tracking tooling
  (Google Analytics/`gtag`, PostHog, Segment, Mixpanel, Amplitude, Hotjar, LogRocket) across
  `frontendv1/src` returned no matches.
- **No `frontendv1/package.json` dependency** on any analytics package was found either.
- **Sentry (error observability) exists on the backend only, and only if configured.**
  `backend/app/observability.py` initializes Sentry only when a `SENTRY_DSN` environment variable
  is set; otherwise it is a no-op. This is server-side error/trace telemetry, not a browser
  tracking cookie or client-side analytics pixel, and it was not found wired into `frontendv1` at
  all.

## What this means

As shipped today, StealthLab's frontend does not run any third-party advertising or analytics
tracking, and does not set tracking cookies. The only client-side persistence found is
functional: keeping you signed in (Supabase session) and a legacy fallback identity value
(`sessionStorage`) used ahead of full auth rollout.

**This can change** as the product adds features (e.g. product analytics, crash reporting in the
frontend). If and when it does, this document must be updated to reflect what's actually added —
do not let this document go stale relative to the code. **[FOUNDER: re-run this audit before
adding any analytics/tracking dependency, and update this document and any required consent
banner at that time.]**

## No cookie-consent banner needed today

Because no non-essential cookies or tracking were found, no cookie-consent banner currently
appears in `frontendv1`, and none was added by this audit — adding one prematurely would overstate
what the product does. If tracking is added later, a consent mechanism appropriate to that
tracking (and the jurisdictions involved) will need to be added at that time, per
[Privacy Policy](./PRIVACY_POLICY.md).

---
*Generated from repository state on 2026-09-09. Cross-references: `frontendv1/src/lib/auth.ts`,
`frontendv1/src/lib/supabase/client.ts`, `backend/app/observability.py`.*
