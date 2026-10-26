# keळ — website for the routing + .kell release

A copy of `prod_frontend/` cut down to the first release: cheaper coding tasks inside Claude Code and Cursor, and the repository's `.kell` folder. Goals, search, contributions, credits and public profiles are removed; `prod_frontend/` keeps the full version, the bigger aim.

```bash
cd routing_kell_frontend
npm install
npm run dev      # http://localhost:3100
npm run build && npm start
```

Copy `.env.example` to `.env.local` to connect a backend (`NEXT_PUBLIC_KEL_API_URL`) and a sign-in entry point (`NEXT_PUBLIC_KEL_SIGNIN_URL`). Without them, `/sign-in` and the account pages show explicit “not connected” states — nothing is fabricated.

## Routes
`/` (hero with install, then `#about` what you get, `#action` see it work, `#use` use it) · `/docs` (getting started, how to use, the .kell folder, for teams, legal) · `/sign-in` · `/account`. "About" is a homepage section (`/#about`), not a page — the nav's "About" link scrolls to the homepage's first content section.

## Decisions
- **Install command** is `npx -y stealthlab-mcp setup`, the first-time install. The npm package is not published yet, so it doesn't work for visitors until it is.
- **Fonts:** `Bahnschrift` first in the stack (present on Windows; its licence doesn’t allow redistributing it as a web font), falling back to bundled **Barlow** (SIL OFL, DIN-like). To ship Bahnschrift itself, add a licensed webfont via `@font-face`.
- **Logo:** `public/kel-logo-source.webp` is the supplied artwork untouched. `kel-wordmark.png` / `kel-mark.png` are crops of it with the flat cream background made transparent (colours and shapes unchanged), so it sits on paper or ink without a box.
- **Motion:** Lenis (smooth scroll + `/#about` glide) and CSS/IntersectionObserver reveals. No GSAP — nothing here needs scrubbing or pinning (`position: sticky` covers the one pinned column). All motion is off under `prefers-reduced-motion`.
- **See it work** on the homepage is an illustrative demo (three example tasks); its files, test counts and costs are made up and labelled so. It shows outcomes only, not how keळ decides.

## Analytics and error monitoring (optional, privacy-first)
Off unless configured at build time (see `.env.example`); both are skipped when a visitor sends Do Not Track / Global Privacy Control.
- **Website analytics:** set `NEXT_PUBLIC_PLAUSIBLE_DOMAIN` (and `NEXT_PUBLIC_PLAUSIBLE_SRC` for a self-hosted Plausible). Cookieless. Events: `install_copy`, `sign_in_click`, `web_vital` (LCP/CLS/INP/TTFB). Never send typed text, emails, ids or URLs: `track()` drops such values. Register these as *custom events* (and the props you want) in Plausible → Goals.
- **Error monitoring:** set `NEXT_PUBLIC_SENTRY_DSN` (use a separate *frontend* Sentry project; also `NEXT_PUBLIC_KEL_ENV`, `NEXT_PUBLIC_KEL_RELEASE`). The SDK is downloaded only when a DSN is set. Route (`app/error.tsx`) and root (`app/global-error.tsx`) boundaries report; no tracing, no replay, no PII, query strings stripped.
- Legal: what is collected is described in `docs/legal/COOKIES_AND_TRACKING.md` and `SUBPROCESSORS.md`. **Update them if you change any of this.**
- The backend/MCP side is separate: OpenTelemetry + Phoenix (`backend/OBSERVABILITY.md`) and backend Sentry.
