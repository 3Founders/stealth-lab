# keळ — Codex MCP / Plugin / Tool Setup

This guide is for building the **full production keळ website with OpenAI Codex**.

## Recommended stack

1. **Figma MCP / Figma plugin** — brand, visual system, design context, implementation reference
2. **Mobbin MCP** — UX research and interaction-pattern research
3. **Storybook MCP / Codex plugin** — coded component and design-system source of truth
4. **Playwright / browser tooling** — real browser and visual verification
5. **Existing StealthLab MCP** — keep the existing product/backend knowledge connection

Do not install a collection of website generators. These tools are enough.

---

# 1. Figma — REQUIRED

Figma's current Codex setup recommends the **remote Figma MCP server**. It gives Codex structured access to Figma design context, including components, variables, layout data and other design information.

## Codex app

1. Open Codex.
2. Open **Plugins**.
3. Find **Figma**.
4. Click `+` / Install.
5. Authenticate with Figma.
6. Return to Codex.
7. Verify that Figma is connected.

## Codex CLI

```bash
codex mcp add figma --url https://mcp.figma.com/mcp
```

Authenticate when prompted, then:

```bash
codex mcp list
```

Prefer the remote server. Figma's current documentation says it has the broadest feature set.

## Figma file

Create:

`keळ — Brand & Product System`

Suggested pages:

```text
00 — Brand
01 — Foundations
02 — Components
03 — Website
04 — Explorations
```

Figma is the **visual source of truth** for:

- logo
- banana / ळ identity
- colours
- typography
- spacing
- components
- layout
- visual composition
- design tokens

---

# 2. Mobbin — UX RESEARCH

Mobbin is for **UX research**, not copying another product.

Open:

`https://mobbin.com/mcp`

Select **Codex** and follow Mobbin's current Codex-specific installation/authentication flow.

Do not blindly use a Claude Code command if Mobbin presents a different Codex command.

Use Mobbin for:

- search UX
- discovery
- filtering
- detail pages
- rankings
- account flows
- setup flows
- navigation
- empty states
- mobile/responsive UX

Rule:

> Borrow interaction principles, not visual designs, layouts, branding, illustrations or proprietary UI.

Mobbin MCP access requires an eligible paid plan.

---

# 3. Storybook — CODED COMPONENT SOURCE OF TRUTH

Storybook should become the **coded component source of truth**.

## Codex plugin

Storybook currently provides an official Codex plugin. The documented setup is:

```bash
codex plugin marketplace add storybookjs/mcp --ref main
codex plugin add storybook@storybook
```

Verify:

```bash
codex plugin marketplace list
codex plugin list --marketplace storybook
```

If your Codex version exposes the plugin through its normal plugin UI, use that instead.

## Storybook MCP addon

If Storybook already exists:

```bash
npx storybook add @storybook/addon-mcp
```

Enable the components manifest in `.storybook/main.ts`:

```ts
features: {
  componentsManifest: true,
}
```

Start Storybook:

```bash
npm run storybook
```

The MCP endpoint is normally:

```text
http://localhost:6006/mcp
```

If needed, Storybook documents:

```bash
npx mcp-add --type http --url "http://localhost:6006/mcp" --scope project
```

You can also configure the HTTP MCP server using Codex's own MCP UI/CLI.

## If Storybook does not exist

Do not install it blindly before inspecting the repository.

First let Codex determine whether Storybook is appropriate. If yes, initialise it using the existing framework/package manager, add `@storybook/addon-mcp`, enable the manifest, and establish the initial component stories.

---

# 4. Playwright / BROWSER VERIFICATION

Use existing browser-testing infrastructure if present.

If Playwright already exists, reuse it.

If absent, add the lightest appropriate Playwright setup for the existing frontend.

The desired workflow is:

```text
implement
→ run website
→ inspect browser
→ screenshot / verify
→ fix
→ repeat
```

Pay special attention to:

- logo
- banana artwork
- vibrant yellow
- backgrounds
- typography
- search
- problems
- mobile
- loading states
- empty states
- errors

Do not declare UI complete merely because the build/tests pass.

---

# 5. EXISTING STEALTHLAB MCP

Keep the existing StealthLab MCP.

It is separate from the design/research tools.

Do not replace or remove it.

Conceptually:

```text
Figma
  = visual source of truth

Mobbin
  = UX research

Storybook
  = coded component source of truth

Playwright
  = browser / visual verification

StealthLab MCP
  = product/backend knowledge
```

---

# 6. RECOMMENDED SCOPE

Prefer:

```text
User/account:
  Figma
  Mobbin

Project:
  Storybook
  StealthLab
  other project-specific MCPs
```

Do not register the same MCP twice at different scopes.

---

# 7. VERIFICATION

After configuration, verify:

### Figma
- authenticated
- design file accessible
- design context can be retrieved

### Mobbin
- authenticated
- research/search works

### Storybook
- Storybook starts
- `/mcp` endpoint is available
- components manifest is enabled
- Codex can query components/stories

### Browser
- frontend starts
- browser can load the site
- visual inspection/screenshots work

### StealthLab
- existing MCP still connects
- no unnecessary configuration changes

---

# 8. FIGMA FILE STRUCTURE

Create:

`keळ — Brand & Product System`

Pages:

```text
00 — Brand
01 — Foundations
02 — Components
03 — Website
04 — Explorations
```

### 00 — Brand

Explore:

- banana / ळ logo concepts
- wordmark
- standalone mark
- favicon
- monochrome versions
- light/dark versions

### 01 — Foundations

Define:

- colour tokens
- typography
- spacing
- grid
- radius
- borders
- shadows
- icon style
- background treatments

### 02 — Components

Define:

- navigation
- buttons
- links
- inputs
- search
- cards
- lists
- procedure result
- problem object
- evidence
- verification
- rankings
- tabs
- dialogs
- alerts
- skeletons
- empty states
- errors

### 03 — Website

Design:

- home/about
- search
- search results
- problems
- problem detail
- account
- setup
- docs
- mobile variants

### 04 — Explorations

Keep rejected/experimental directions here.

---

# 9. TOOL HIERARCHY

Do not let the tools become competing sources of truth.

```text
Mobbin
  ↓
UX research

Figma
  ↓
visual/design source of truth

Storybook
  ↓
coded component source of truth

Codex
  ↓
implementation

Playwright
  ↓
rendered-product verification
```

---

# 10. FIRST CODEX SESSION

Do **not** immediately ask Codex to build everything.

First send:

> Read `AGENTS.md`. Do not modify code yet. Inspect the entire repository and all connected MCPs/plugins/tools. Understand the existing frontend, backend/API contracts, routing, components, styling, authentication, setup, billing, docs, tests, and current website. Inspect the available Figma and Mobbin context. Also inspect Storybook if it exists. Give me a concise assessment of what can be reused, what is missing, and your proposed implementation/design plan for keळ. Pay particular attention to the brand identity, logo, backgrounds, colour system, typography, responsive design, and existing architecture. Stop after presenting the assessment and plan.

The first pass should **not modify files**.

---

# 11. SECOND CODEX MESSAGE

After reviewing the assessment, send ONE execution message containing:

1. the instruction to proceed
2. the complete updated keळ implementation prompt

Use:

> Proceed with the plan. Read and follow `AGENTS.md` throughout. Now execute the complete implementation brief below. Do not stop at planning, mockups, or a design proposal. Build the actual production-ready keळ website with real frontend/backend integration, responsive behaviour, loading/error/empty states, accessibility, performance, and browser verification. Use Figma as the visual source of truth, Mobbin for UX research, Storybook as the coded component source of truth, and browser tooling to visually verify the rendered result.
>
> [PASTE THE FULL UPDATED keळ PROMPT HERE]

Do not send a separate "proceed" message before pasting the implementation prompt.

---

# 12. FINAL WORKFLOW

```text
                 MOBBIN
              UX RESEARCH
                   │
                   ▼
                FIGMA
       BRAND + VISUAL SYSTEM
                   │
                   ▼
                CODEX
          IMPLEMENTATION AGENT
             │       │
             │       └───────────┐
             ▼                   ▼
         STORYBOOK           PLAYWRIGHT
       COMPONENT TRUTH       BROWSER TRUTH
             │                   │
             └─────────┬─────────┘
                       ▼
                      keळ
                       │
                       ▼
                STEALTHLAB MCP
              real product context
```

The goal is not merely:

> "generate a website."

The goal is:

> **build a complete, fast, production-ready website that is unmistakably keळ.**

The visual identity should centre on:

**vibrant banana yellow + banana geometry + Marathi ळ + strong typography + art-directed backgrounds + simple product UX.**

Avoid generic AI SaaS aesthetics, dull yellow, white-card-on-white-background layouts, purple AI gradients, excessive glassmorphism, cartoon bananas, generic startup logos, and unnecessary visual effects.
