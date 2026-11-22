# Open-source team-chat platforms whose web UI is worth borrowing (as of 2026-10-28)

## Q1. Which have a reusable component library usable standalone in React 19 / Next.js 15 static export?

### Takeaway
Two real options: Element's Compound (`@vector-im/compound-web`, AGPL-3.0 OR paid commercial; React 18/19) and Rocket.Chat's Fuselage (MIT, but SCSS + CSS-in-JS runtime, and its repo is archived into the Rocket.Chat monorepo). Only Fuselage is license-safe to embed in a proprietary app without paying. Neither is chat-specific in the way Element's new `web-shared-components` is, and that one inherits Element's AGPL/commercial terms.

### Cited Findings
- compound-web is dual-licensed AGPL-3.0 or a paid Element Commercial License (copyright 2023-2026 New Vector Ltd); licensing contact licensing@element.io — [compound-web](https://github.com/element-hq/compound-web)
- compound-web v10.4.0 published 2026-10-12 (v10.3.1 on 10-08) — active — [releases API](https://api.github.com/repos/element-hq/compound-web/releases?per_page=3)
- Package `@vector-im/compound-web` 10.4.0: peer deps `react ^18 || ^19.0.0`, `@vector-im/compound-design-tokens`, `@fontsource/inter`, `@fontsource/inconsolata`; deps Radix UI primitives, floating-ui, vaul, classnames; plain CSS (no Sass/PostCSS/CSS Modules listed), shipped as `dist/style.css`; built with Vite — [package.json](https://raw.githubusercontent.com/element-hq/compound-web/main/package.json). Works without Tailwind; fits "own CSS".
- Element Web is multi-licensed: AGPL-3.0 (or later), GPL-3.0 (or later), or paid Element Commercial License; files LICENSE-AGPL-3.0, LICENSE-GPL-3.0, LICENSE-COMMERCIAL — [element-web](https://github.com/element-hq/element-web)
- element-web `packages/` now contains `shared-components`, `module-api`, `shared-types`, `shared-utils`, `playwright-common` — [contents API](https://api.github.com/repos/element-hq/element-web/contents/packages)
- `@element-hq/web-shared-components` v0.2.2 (very early, 0.x), publishable, peers on compound-web `^8.3.5 || ^9 || ^10`, react; ships one CSS bundle; package.json has NO license field (inherits repo's tri-license; must verify) — [package.json](https://raw.githubusercontent.com/element-hq/element-web/develop/packages/shared-components/package.json)
- element-web latest releases v1.12.33 (2026-10-27), v1.12.32 (10-16) — [releases API](https://api.github.com/repos/element-hq/element-web/releases?per_page=3)
- matrix-react-sdk was moved to element-hq/matrix-react-sdk (README says developed 2015-2024 under Matrix.org Foundation); a search found no evidence of 2026 merge status beyond that, and the packages/ listing above suggests element-web now carries the shared components itself — [matrix-react-sdk](https://github.com/matrix-org/matrix-react-sdk)
- Fuselage: MIT; packages `@rocket.chat/fuselage` (components), `@rocket.chat/fuselage-tokens`; the standalone repo is ARCHIVED, packages moved to the Rocket.Chat monorepo — [Fuselage repo](https://github.com/RocketChat/Rocket.Chat.Fuselage)
- `@rocket.chat/fuselage` 0.96.0 (in monorepo), MIT; peers `@rocket.chat/fuselage-hooks`, `@rocket.chat/icons`, react, react-dom, react-virtuoso; deps `@rocket.chat/css-in-js`, react-aria, react-stately; built with Sass + PostCSS (so styling is SCSS plus a CSS-in-JS runtime) — [package.json](https://raw.githubusercontent.com/RocketChat/Rocket.Chat/develop/packages/fuselage/package.json). Pre-1.0: expect API churn.
- Rocket.Chat license: MIT outside `apps/meteor/ee/` and `ee/`; those are under a separate enterprise license; third-party parts keep own licenses — [LICENSE](https://raw.githubusercontent.com/RocketChat/Rocket.Chat/develop/LICENSE)
- Mattermost: `webapp/` (incl. subdirectories), `server/public/` etc. are Apache-2.0; server is AGPL-3.0; compiled Mattermost Inc. binaries are MIT; trademark use needs written approval — [LICENSE.txt](https://raw.githubusercontent.com/mattermost/mattermost/master/LICENSE.txt). Stack is React frontend, Go server — [repo](https://github.com/mattermost/mattermost). Not a standalone component library (app code); license is permissive for copying though.
- Signal-Desktop: `AGPL-3.0-only`; React 19.3.0, Electron 44.9.1, Sass + Tailwind 4.3.0 — [package.json](https://raw.githubusercontent.com/signalapp/Signal-Desktop/main/package.json); pushed 2026-10-26 — [API](https://api.github.com/repos/signalapp/Signal-Desktop)
- Nextcloud Talk (`nextcloud/spreed`): AGPL-3.0, Vue 3.5 + `@nextcloud/vue` 9.x (not React); pushed 2026-10-28 — [package.json](https://raw.githubusercontent.com/nextcloud/spreed/main/package.json), [API](https://api.github.com/repos/nextcloud/spreed)
- Stoat (ex-Revolt) web client `stoatchat/for-web`: AGPL-3.0, built with Solid.js (not React); pushed 2026-10-28, ~768 stars. Rename from Revolt is inferred from leftover "revolt" strings in README, not stated — [repo](https://github.com/stoatchat/for-web), [API](https://api.github.com/repos/stoatchat/for-web)
- Zulip: Apache-2.0; web frontend is Handlebars + jQuery 4 + tippy.js + TypeScript bundled with webpack, NO React; releases 12.3 (2026-09-21), 12.2 (08-10), 12.1 (06-26) — [repo](https://github.com/zulip/zulip), [package.json](https://raw.githubusercontent.com/zulip/zulip/main/package.json), [releases](https://api.github.com/repos/zulip/zulip/releases?per_page=3)
- Chatwoot: GitHub API reports license "NOASSERTION/Other"; LICENSE text = MIT (Expat) except `enterprise/` dir (separate license) and third-party parts; very active (pushed 2026-10-28, ~38.5k stars). It's customer-support inbox (Vue), not team chat — [LICENSE](https://raw.githubusercontent.com/chatwoot/chatwoot/develop/LICENSE), [API](https://api.github.com/repos/chatwoot/chatwoot). (Vue stack is from my background knowledge, not verified here.)
- Rocket.Chat monorepo releases at 9.0.0-rc.1 (2026-10-28) — active — [releases API](https://api.github.com/repos/RocketChat/Rocket.Chat/releases?per_page=3)
- Mattermost releases: v12.0.1 (2026-10-22), v12.1.0-rc2 (10-26), v11.7.15-rc1 (10-28); monthly compiled release on the 16th — [releases API](https://api.github.com/repos/mattermost/mattermost/releases?per_page=3), [repo](https://github.com/mattermost/mattermost)
- Newcomer: `fancyboi999/open-tag` — Slack-style workspace where AI agents (Claude Code, Codex, Copilot) are teammates in channels/threads/DMs/tasks; Apache-2.0; TypeScript; created late June 2026; ~31 stars (third-party index, may be stale). Positioned as open alternative to Anthropic's Claude Tag (launched 2026-06-23 per that source) — [open-tag](https://github.com/fancyboi999/open-tag), [index](https://awesome.ecosyste.ms/projects/github.com%2Ffancyboi999%2Fopen-tag). Different, unrelated projects share the name: CopilotKit/OpenTag (MIT, Slack/Teams agent bot) — [repo](https://github.com/CopilotKit/OpenTag).

### Inferences
- License ranking for copying into a proprietary app: Zulip (Apache), Mattermost webapp (Apache), Rocket.Chat/Fuselage non-ee (MIT), Chatwoot non-enterprise (MIT) = safe. Element/Compound (AGPL or paid), Signal, Nextcloud Talk, Stoat (AGPL) = reference only unless you open-source your app or buy Element's commercial license.
- A static-export Next.js app serving the code to browsers is "conveying" it, so AGPL would apply to the combined work; treat AGPL code as design reference only.
- Compound is the best technical fit (React 19 peer, plain CSS, Radix primitives) but its license is the blocker; the same Radix + tokens approach can be replicated independently under MIT.
- Fuselage is MIT but heavier (react-aria, SCSS, css-in-js) and may be awkward under Next.js static export; check SSR/CSS-in-JS handling before adopting.
- open-tag (Apache-2.0, agents-as-teammates) is the closest conceptual match to a human + AI-agent chat, but tiny and brand new; read its UI as reference or fork candidate, vet for quality.

### Gaps
- Did not verify the Fuselage/ Rocket.Chat UI stack's behavior under Next.js static export, nor its bundle size.
- open-tag's actual frontend framework, stars, and commit activity unconfirmed (repo page fetch via my tool 404'd on a wrong URL; only search-derived data).
- Element Web's exact CSS/React stack, and whether `web-shared-components` includes timeline/composer components (vs. only small pieces), not verified.
- Mattermost `webapp/channels` UI component reuse effort not assessed; the LICENSE "Apache for webapp" reading comes from a model summary of the file, worth re-reading the raw file.
- No 2026-specific newcomers beyond open-tag were verified (Spacebar mentioned in a roundup only: [mirrorfly roundup via search](https://mirrorfly.com/blog/?p=34191), unfetched).

## Q2. What does each do well in UX that a human + AI-agent group chat should copy?

### Takeaway
I could not verify UX details from primary sources within the research budget; the points below are limited to what sources state, plus clearly-labelled background knowledge.

### Cited Findings
- open-tag models agents as teammates in channels, threads, DMs and shared tasks (the product shape you are building) — [open-tag](https://github.com/fancyboi999/open-tag)
- Zulip's differentiator (topic-per-stream threading) is background knowledge, not verified from the fetched pages.

### Inferences
- Background knowledge (UNVERIFIED here): Zulip's stream+topic model is the strongest threading design for long-running agent work; Slack-style side-panel threads (Mattermost, Rocket.Chat, Element threads) are the familiar pattern; Element has mature read receipts, typing, reactions and mention pills; Mattermost/Rocket.Chat have @mention autocomplete with channel/user/group suggestions; Signal has the cleanest minimal density. Verify with live demos/screenshots before copying.

### Gaps
- No primary-source UX evidence gathered (no docs/screenshot review). Recommend a follow-up pass on Zulip docs (topics), Element threads/read-receipts docs, Mattermost/Rocket.Chat mention docs.

## Q3. License traps

### Takeaway
Biggest traps: Element/Compound AGPL-or-commercial; Signal, Nextcloud Talk, Stoat all AGPL; Mattermost and Rocket.Chat/Chatwoot are mixed-license repos with enterprise-licensed directories and trademark limits.

### Cited Findings
- Element: AGPL-3.0 / GPL-3.0 / commercial — [element-web](https://github.com/element-hq/element-web)
- Compound: AGPL-3.0 or commercial — [compound-web](https://github.com/element-hq/compound-web)
- Signal-Desktop AGPL-3.0-only — [package.json](https://raw.githubusercontent.com/signalapp/Signal-Desktop/main/package.json)
- Stoat for-web AGPL-3.0 — [repo](https://github.com/stoatchat/for-web)
- Nextcloud Talk AGPL — [package.json](https://raw.githubusercontent.com/nextcloud/spreed/main/package.json)
- Rocket.Chat: `ee/` and `apps/meteor/ee/` excluded from MIT — [LICENSE](https://raw.githubusercontent.com/RocketChat/Rocket.Chat/develop/LICENSE)
- Chatwoot: `enterprise/` excluded from MIT — [LICENSE](https://raw.githubusercontent.com/chatwoot/chatwoot/develop/LICENSE)
- Mattermost: server AGPL, webapp Apache-2.0, trademark approval required, `LICENSE.enterprise` exists at repo root — [LICENSE.txt](https://raw.githubusercontent.com/mattermost/mattermost/master/LICENSE.txt), [repo](https://github.com/mattermost/mattermost)

### Inferences
- Do not copy code from `ee/`/`enterprise/` dirs; strip brand names, logos and fonts/icons with separate licenses (Fuselage icons, Signal assets).
- Signal and Element branding/assets are separate concerns from code license; check before reuse.

### Gaps
- Not verified: icon/font/illustration licenses for Fuselage icons, Compound tokens/icons, Mattermost assets.
- Not legal advice; AGPL-vs-static-export interpretation is my inference.
