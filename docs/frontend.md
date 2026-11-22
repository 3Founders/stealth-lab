# Frontend: what to learn from, and how not to get stuck

**Research findings — 2026-11-20**

Two questions: whose frontend code is worth reading, and what makes a frontend
codebase so hard to change that the team gives up. The second matters more. The
repos below are references; the traps below are the ones that actually cost months.

---

## 1. The honest finding about award-winning sites

Before the repo list: **Awwwards winners are a poor model for a product site**, and
the data is unambiguous.

[DappaSol measured 12 recent winners live](https://dappasol.com/guides/award-winning-websites/)
(Site of the Year / Site of the Month, November 2026) and read the jury sheets of 23:

- **Usability was the lowest of the four scores on all 23 winners.** Not most — all.
  The best usability score of the 23 was 7.90, and it was still that site's weakest
  category.
- Accessibility was the **lowest or joint-lowest** on **21 of 23** developer sheets
  (average 6.92, against 8.59 for animations).
- **Four of the twelve send fewer than 30 words of HTML before JavaScript runs**;
  one sends a single word. "Google renders JavaScript eventually; many AI crawlers
  don't run it at all."
- Weight goes unpunished: one winner moved **38.4 MB** on the phone pass and still won.
- The main score has **no line for speed, search, or sales**. Design is 40%, usability
  30%, creativity 20%, content 10% ([judging criteria](https://www.hontran.dev/blog/awwwards-judging-criteria)).

So: walkthrough sites are worth studying for **craft** — type, motion, one coherent
idea carried all the way through — and are a bad template for a site that must be
scanned, indexed and operated. Our `/pricing`, `/docs` and procedure pages are the
opposite kind of page.

**What is worth stealing from them:** the discipline of *one idea carried through*
(the strongest winners win on a single sustained concept, not on accumulated effects),
and the motion restraint that comes from honouring `prefers-reduced-motion`.

---

## 2. Repositories actually worth reading

Most winners keep code private. These are the ones that don't, plus the ones that are
worth reading for engineering rather than looks.

### Design and craft

| Repo | Why read it |
|---|---|
| [juliangarnier/anime](https://github.com/juliangarnier/anime) | 74k+ stars. The library behind its own award-winning site. Best public example of an animation engine with a small, disciplined API. |
| [brunosimon/folio-2019](https://github.com/brunosimon/folio-2019), [folio-2025](https://github.com/brunosimon/folio-2025) | Site of the Year 2019, and its 2025 successor. MIT. Read for how a WebGL scene is structured so it can be *turned off*. |
| [Giats2498/giats-portfolio](https://github.com/Giats2498/giats-portfolio) | Awwwards Honorable Mention. Shows the three-layer approach (3D background / content / fluid overlay) written legibly. |
| [raphaelbleier/design-inspiration-skills](https://github.com/raphaelbleier/design-inspiration-skills) | Not a site — 50+ Awwwards winners **measured and written down** as design tokens, motion specs, and "why it works". The most useful item on this list. |
| [DavidHDev/react-bits](https://github.com/DavidHDev/react-bits) | Animated React/Next components, each self-contained. Good for studying how to package an effect you can drop in and drop out. |

### Engineering and structure

| Repo | Why read it |
|---|---|
| [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | The distribution model is the lesson, not the components. See §3.1. |
| [satnaing/shadcn-admin](https://github.com/satnaing/shadcn-admin) | A clean, complete dashboard on shadcn/ui. The closest public analogue to our app surfaces. |
| [PanJiaChen/vue-element-admin](https://github.com/PanJiaChen/vue-element-admin) | Older, but the canonical large admin app: routing, permissions, and layouts separated cleanly. |
| [vercel/commerce](https://github.com/vercel/commerce) | The reference Next.js App Router app. Read it for cache and data-fetching discipline. |
| [OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) | A real, heavy client-side app in TypeScript. Useful for state and timeline UI. |
| [Anthropic agentskills](https://agentskills.io) / SKILL.md format | Relevant to us: the format we are designing around. |

### Our own, for reference

- `routing_kell_frontend` — the site. `app/globals.css` is a single design-token block
  (`--paper`, `--ink`, `--rule`, `--max`, `--pad`) with section rules after it. That is
  already the right shape; see §3.3.
- `desktop` — the Electron app, sharing a token vocabulary but with its own `dk-` prefix.
- `admin` — the dashboard.

---

## 3. How not to get stuck

Six failure modes, each with the evidence behind it and the specific rule I'd adopt.

### 3.1 The copy-paste debt that nobody names

shadcn/ui is the most-starred UI project in the ecosystem, and it is **not a library** —
there is no package, no version, nothing to upgrade. You copy the source
([shadcn docs](https://ui.shadcn.com/docs); [Redmonk](https://redmonk.com/kholterhoff/2025/04/22/ui-component-libraries-shadcn-ui-and-the-revenge-of-copypasta/)).

The upside is real: Supabase's frontend lead says they moved to it precisely because
"it transfers the ownership of the code to the user… the previous approach was
deprecated mainly because we owned the code for the UI, so it was an endless wishlist."

**The trap, stated honestly by its own reviewers:** "the unsolved problem is
customization divergence." Once you edit a component, an upstream fix arrives as a
conflict, not a merge — and there is **no per-component semantic versioning**. A GitHub
discussion asking for an automated update has been open since 2023 and was still
unresolved in May 2026 ([toolchew](https://toolchew.com/en/review-shadcn-2026/)).

**Rule:** copied components live in **one directory with one owner** (`components/ui`),
receive **no business logic**, and every edit is either (a) a design token upstream, or
(b) a new wrapper beside it. The moment a copied component grows app-specific branches,
it is no longer a component — it is a page fragment, and it should move.

### 3.2 Shared packages are harder than shared apps

The strongest single source found is a three-year Next.js monorepo postmortem
([Share Packages, Not Applications](https://xinjiangshao.com/blog/2026-02-14-share-packages-not-applications)).
Five brand teams shared one app; the problems were **organisational, not technical**:

- **Deployment coupling** — a bug in one brand blocked another's release
- **Merge queue congestion** — one flaky test in one brand's suite held up everyone
- **Performance budget bleed** — one brand's analytics inflated every brand's bundle
- **Feature flag sprawl** — "shared" components accumulated conditional branches

Their conclusion is the line worth memorising:

> "A shared package is consumed at build time — each consumer gets its own copy, its own
> bundle, its own deploy. A shared application is a runtime coupling — all consumers
> live or die together."

And a counterweight from the other direction
([4 Next.js apps in one repo](https://www.michaelouroumis.com/blog/posts/lessons-running-4-nextjs-apps-monorepo)):
after a year of ambitious sharing, they had **one** shared package, because "every
time I wanted to change a shared component, I had to check four apps."

**Rules for us:**
1. **Start with duplication.** Extract on the **third** identical implementation, never
   the second. (Both posts say this independently.)
2. Share **tokens and primitives**, not **screens**. `globals.css` tokens: yes. A
   shared `<PricingTable>`: no.
3. Three apps (`routing_kell_frontend`, `desktop`, `admin`) sharing a token vocabulary is
   fine *because they don't share a deploy*. Keep it that way — do not merge apps.

### 3.3 Design tokens are a gate, not a migration

The single highest-leverage anti-decay rule, from Frontend Foundations
([design tokens](https://frontend-foundations.crayonsandco.de/architecture/design-tokens/)):

> "If a global rebrand takes a find-and-replace on six files, your tokens are working.
> Audit new UI for hard-coded values before merge. **Tokens are a gate, not a migration
> you finish someday.**"

The anti-patterns it lists are exactly what accumulates under deadline:

- magic numbers beside tokens (`padding: 13px` next to a spacing scale)
- duplicate tokens with different names for the same value
- mixing rem, px and arbitrary gaps
- **presentation names in tokens** — `--hero-pink` breaks when the brand shifts

And the Thoughtworks write-up
([Martin Fowler, design-token architecture](https://martinfowler.com/articles/design-token-based-ui-architecture.html))
adds the one that ages worst: **`z-index` magic numbers across teams**. Once two
independent surfaces stack (our site and an embedded widget, or a modal and a toast),
untracked `z-index` becomes unfixable without cross-team archaeology.

**Rules for us:**
1. **No hard-coded colour, spacing, radius, or z-index outside the token block.** The
   lint that matters is a review checklist line, not a tool.
2. **Semantic names only** — `--colour-accent`, never `--hero-pink`.
3. **A z-index scale as tokens** (`--z-modal`, `--z-toast`, `--z-menu`). We already have
   ad-hoc values (86, 85, 60, 40); they should be named before there are twenty.

**Where we actually stand today** (checked against `routing_kell_frontend`):

| Check | Result |
|---|---|
| Token block | ✅ `:root` holds the full vocabulary (`--paper`, `--ink`, `--rule`, `--max`, `--pad`, `--gap`, `--ease`) |
| `prefers-reduced-motion` | ✅ honoured — **9** separate media blocks |
| z-index values in use | ⚠️ **8 distinct** values (0, 1, 2, 20, 40, 50, 60, 100) with no scale |
| Hard-coded hex outside the token block | ⚠️ **38** occurrences |
| `components/ui` | none — 8 section-named folders (`billing`, `chat`, `console`, `org`, …) |

The 38 hex values are the one number that will hurt later. They are almost all
one-off tints; each is a small leak, and together they are why a rebrand becomes a
`find` across files rather than a token edit. This is the item I would fix while the
site is small, not after the procedure page doubles it.

### 3.4 The FT's four causes of decay

The best documented postmortem is the FT rebuilding their front-end tooling
([Medium, "Designing a sustainable front-end"](https://medium.com/ft-product-technology/designing-a-sustainable-front-end-for-ft-com-f37c59d27eeb)).
They found the old system had decayed because its team disbanded, and identified four
themes — each of which applies to any frontend that outlives its authors:

| Cause | The symptom they found |
|---|---|
| **Scope and responsibility unclear** | "littered with undocumented, untested and mysterious stuff"; developers "added application-specific code because they didn't know what else to do" |
| **Internal architecture hard to navigate** | "we struggled to map out the tangled relationships"; colleagues "avoided looking inside and treated it as a black box" |
| **Releases were scary** | the test suite no longer covered the system, so "people didn't feel comfortable making changes" |
| **Documentation and history insufficient** | features existed with no context: "we did not know if they were still needed… it was hard to delete things" |

Their fix is transferable: **split into small packages, each with a one-or-two-sentence
README defining what it is**, and use that description to decide whether new features
are in scope. "We've created some really tiny packages just to increase the visibility
of important information."

**Rules for us:**
1. **One sentence of purpose at the top of every directory's main file.** Not a
   restatement of the name — a scope statement that lets you refuse a feature.
2. **Deletion must be normal.** "It was hard to delete things" is the specific symptom
   that precedes a rewrite. If a component has no callers, delete it in the same PR
   that removes the last caller.

### 3.5 The framework-coupling trap

From [The infinite legacy cycle in front-end](https://techhub.iodigital.com/articles/the-infinite-legacy-cycle-in-front-end):

> "Framework-specific logic is completely intertwined with non-framework-specific
> logic. This makes it hard to migrate to new (versions of) frameworks. By decoupling
> this logic into smaller modules you can encapsulate features into standalone features."

The same post names the other three: the startup mentality ("nothing is as permanent
as a temporary solution"), the difficulty of getting time for invisible work, and —
the one that stings — **very smart developers building unnecessarily complex solutions**
that nobody else can maintain.

**Rules for us:**
1. **Domain rules don't import React.** The routing, verification and economy logic
   already live in the backend; the frontend should not grow a second copy. Where it
   must, keep it in plain `.ts` modules with no framework imports — ours already mostly
   do (`lib/billing.ts`, `lib/plans.ts`), which is why they're testable.
2. **Say no to complexity you cannot explain in one sentence.** The K.I.S.S. point is
   not moralising: the specific failure is a clever component whose author leaves.

### 3.6 Performance and accessibility as budget, not taste

The award data (§1) and the judging criteria agree: **jank costs more usability than
an animation earns in creativity**, and accessibility is where heavy-animation sites
consistently lose — "the fix is cheap and jurors notice its absence."

Targets worth writing down while the site is small
([Utsubo](https://www.utsubo.com/blog/award-winning-website-design-guide)):

| Metric | Target |
|---|---|
| LCP | < 1.5 s |
| CLS | < 0.05 |
| INP | < 100 ms |
| Sustained animation | 60 fps |

**Rules for us:**
1. **All motion behind `prefers-reduced-motion`** — the site already does this; keep it
   as a hard rule, not a nice-to-have.
2. **Server-render the text.** Four award winners send under 30 words of HTML before
   JavaScript; for a docs/pricing/procedure site that is a self-inflicted wound on both
   search and AI crawlers, which matters directly for the procedure pages we are about
   to build.
3. **A page-weight budget on the procedure page specifically**, because it will want
   to grow charts and a dependency graph.

---

## 4. The one-page version

If only five things survive from this document:

1. **Don't model the product site on award winners.** Study them for craft; their
   usability and accessibility scores are consistently the worst part of them.
2. **Duplicate first, extract third.** Shared packages are cheap; shared *applications*
   couple deployment, CI and bundle size.
3. **Tokens are a gate.** No hard-coded colour, spacing, radius or z-index; semantic
   names only; a z-index scale before it has twenty entries.
4. **Every directory states its purpose in one sentence**, so scope can be refused —
   and deleting things stays normal.
5. **Keep domain logic out of React**, so a framework upgrade remains a chore and not
   a rewrite.

---

## 5. Open questions

1. Do we want `components/ui` at all, or do we keep the current "sections own their
   CSS" approach? It works today at this size; it will not at three times the size.
2. Is a z-index token scale worth introducing now, or after the procedure page ships?
3. Do `routing_kell_frontend` and `desktop` share tokens by copy or by a package? Copy
   is fine today and is the reason they can diverge; the third copy is when to ask.

---

## Sources

- [DappaSol — Award Winning Websites (2026): 12 Winners Measured Live](https://dappasol.com/guides/award-winning-websites/)
- [Awwwards Judging Criteria (2026)](https://www.hontran.dev/blog/awwwards-judging-criteria)
- [Utsubo — Award-Winning Web Design: Judging Criteria Decoded](https://www.utsubo.com/blog/award-winning-website-design-guide)
- [Share Packages, Not Applications — 3 years of a multi-brand Next.js monorepo](https://xinjiangshao.com/blog/2026-02-14-share-packages-not-applications)
- [Lessons from Running 4 Production Next.js Apps in a Monorepo](https://www.michaelouroumis.com/blog/posts/lessons-running-4-nextjs-apps-monorepo)
- [Next.js Monorepo Ownership Boundaries for Multi-Brand Platforms](https://www.pathtoproject.com/blog/20231121-nextjs-monorepo-ownership-boundaries-for-multi-brand-platforms)
- [FT — Designing a sustainable front-end toolset](https://medium.com/ft-product-technology/designing-a-sustainable-front-end-for-ft-com-f37c59d27eeb)
- [The infinite legacy cycle in front-end](https://techhub.iodigital.com/articles/the-infinite-legacy-cycle-in-front-end)
- [Some things I learnt from working on big frontend codebases](https://dev.to/noriste/some-things-i-learnt-from-working-on-big-frontend-codebases-1e0a)
- [shadcn/ui docs](https://ui.shadcn.com/docs) · [Redmonk on the copy-paste model](https://redmonk.com/kholterhoff/2025/04/22/ui-component-libraries-shadcn-ui-and-the-revenge-of-copypasta/) · [shadcn in 2026: is the registry model working?](https://toolchew.com/en/review-shadcn-2026/)
- [Martin Fowler — Design Token-Based UI Architecture](https://martinfowler.com/articles/design-token-based-ui-architecture.html)
- [Frontend Foundations — Design Tokens](https://frontend-foundations.crayonsandco.de/architecture/design-tokens/)
- [Penpot — the developer's guide to design tokens and CSS variables](https://penpot.app/blog/the-developers-guide-to-design-tokens-and-css-variables/)
- [raphaelbleier/design-inspiration-skills](https://github.com/raphaelbleier/design-inspiration-skills)
- [bestgithubrepositories.com — top frontend repos](https://bestgithubrepositories.com/top/frontend)
