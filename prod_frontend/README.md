# keळ — production website

Standalone Next.js 15 (App Router) + TypeScript site. Built from scratch; shares no code or assets with `frontend/` or `frontendv1/`.

```bash
cd prod_frontend
npm install
npm run dev      # http://localhost:3100
npm run build && npm start
```

Copy `.env.example` to `.env.local` to connect a backend (`NEXT_PUBLIC_KEL_API_URL`) and a sign-in entry point (`NEXT_PUBLIC_KEL_SIGNIN_URL`). Without them, `/problems`, `/search` and `/sign-in` show explicit “not connected” states — nothing is fabricated.

## Routes
`/` (Hero, install, `#about`, how it works, knowledge, in action, execution, learning, use it) · `/problems` · `/search` · `/docs` · `/sign-in`. About is a homepage section (`/#about`), not a page.

## Decisions
- **Install command** is the real one from the repo README (`pip install -e packaging/` from a clone). No hosted installer / PyPI release exists, and the site says so.
- **Fonts:** `Bahnschrift` first in the stack (present on Windows; its licence doesn’t allow redistributing it as a web font), falling back to bundled **Barlow** (SIL OFL, DIN-like). To ship Bahnschrift itself, add a licensed webfont via `@font-face`.
- **Logo:** `public/kel-logo-source.webp` is the supplied artwork untouched. `kel-wordmark.png` / `kel-mark.png` are crops of it with the flat cream background made transparent (colours and shapes unchanged), so it sits on paper or ink without a box.
- **Motion:** Lenis (smooth scroll + `/#about` glide) and CSS/IntersectionObserver reveals. No GSAP — nothing here needs scrubbing or pinning (`position: sticky` covers the one pinned column). All motion is off under `prefers-reduced-motion`.
- **Walkthrough** on the homepage is labelled illustrative; routes in it are placeholders, not recorded Runs.
