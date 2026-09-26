# keळ — Agent Instructions

## Mission

Build the actual production website for **keळ**.

Do not build a generic SaaS template.

keळ is a knowledge product for discovering, preserving, evaluating and reusing the best ways of solving problems.

The product loop is:

**Problem → Procedure → Evidence → Verification / Ranking → Contribution → Better Knowledge**

The website must make this understandable quickly while feeling visually distinctive.

---

# 1. Tool hierarchy

When available, use these tools deliberately:

### Figma
Figma is the **visual source of truth**.

Use it for:

- brand identity
- colour
- typography
- spacing
- layout
- components
- design tokens
- visual composition

### Mobbin
Mobbin is **UX research**, not a visual source of truth.

Use it to understand:

- interaction patterns
- navigation
- search
- filtering
- detail pages
- account flows
- setup flows
- empty states
- mobile UX

Never copy Mobbin's branding or visual design.

### Storybook
Storybook is the **coded component source of truth**.

Before creating a UI component:

1. inspect existing Storybook components
2. inspect their documented props
3. inspect their stories/states
4. reuse them when appropriate

Never hallucinate component APIs.

### Playwright
Use Playwright/browser tooling to verify the actual rendered website.

Do not assume that code which compiles is visually correct.

---

# 2. Critical design principle

The previous website direction was too generic.

Do not preserve generic visual patterns simply because they already exist.

The goal is not:

> polished AI SaaS

The goal is:

> unmistakably keळ

If the finished site could easily be renamed to another AI startup without changing the visual system, the design has failed.

---

# 3. Brand identity

The identity is built around:

- banana
- Marathi `ळ`
- accumulated intelligence / knowledge

The banana is the central physical metaphor.

The `ळ` is a genuine part of the brand identity.

Do not simply put a banana icon next to the word `keळ`.

The banana and `ळ` should share geometry, curves, negative space or visual language.

---

# 4. Logo requirements

Treat the logo as a serious identity-design problem.

Explore multiple directions before settling.

At minimum explore:

1. banana silhouette incorporating `ळ`
2. banana / `ळ` negative-space mark
3. abstract `ळ` transformed into banana-like geometry
4. sculptural banana mark with `ळ` hidden in its geometry

The mark must work as:

- wordmark
- standalone symbol
- favicon
- app icon
- navigation mark
- monochrome mark
- large hero artwork

Someone unfamiliar with Marathi should see a beautiful abstract symbol.

Someone familiar with Marathi should eventually notice:

**ळ**

Do not use:

- banana emoji
- cartoon banana
- stock icon
- generic yellow blob
- generic AI sparkle
- infinity symbol
- generic startup monogram

---

# 5. Colour

Yellow is a primary brand colour.

Use a **bright, vibrant banana yellow**.

Do not use dull mustard or washed-out beige as the main yellow.

Core palette direction:

- vivid banana yellow
- warm paper/off-white
- deep ink/charcoal
- botanical green
- strong cobalt/blue
- restrained orange
- optional plum/burgundy

Do not use all colours simultaneously.

Yellow should be immediately recognisable as keळ.

Use it strategically for:

- hero sections
- primary actions
- brand surfaces
- highlights
- selected knowledge states
- important visual moments

Do not make every page yellow.

---

# 6. Backgrounds are part of the design

Do not treat the background as an empty white rectangle behind cards.

Develop a coherent vocabulary of backgrounds.

Use combinations of:

- warm paper
- vivid yellow
- deep ink
- botanical green
- occasional blue
- subtle texture
- abstract banana geometry
- geometry derived from `ळ`

Create visual rhythm between sections.

For example:

**paper → yellow → paper → ink → paper → green → ink footer**

Do not use the exact sequence everywhere; compose it according to content.

Use full-bleed sections.

Use large cropped shapes.

Use oversized typography.

Use asymmetric compositions.

Use negative space deliberately.

---

# 7. Background texture

Subtle physical texture is encouraged.

Possible treatments:

- paper grain
- very subtle noise
- tiny halftone
- fine dots
- restrained print texture
- subtle geometric patterns

Keep it extremely subtle.

Never allow texture to hurt readability.

Prefer lightweight CSS/SVG/tiny assets.

Do not ship giant background images.

---

# 8. Banana as environmental geometry

Do not always render a literal banana illustration.

Sometimes transform the banana into the environment.

Examples:

- enormous cropped banana curve
- banana-shaped negative space
- giant yellow curved form
- subtle stem-like lines
- abstract banana fragments
- repeated banana curves
- oversized logo geometry

Sometimes the user should simply see an elegant curve.

Only after looking should they realise:

> that's the banana.

---

# 9. `ळ` as a design language

Do not repeatedly paste the literal `ळ` around the website.

Instead derive visual geometry from it.

Its curves/strokes can influence:

- decorative lines
- section dividers
- icon geometry
- illustrations
- background patterns
- logo geometry

The relationship should feel sophisticated and subtle.

---

# 10. Typography

Typography is a major part of the identity.

Use:

- expressive display typography for major statements
- extremely readable interface typography

Test the actual brand:

**keळ**

at:

- desktop logo size
- navigation size
- mobile size
- bold
- regular
- monochrome

The Marathi glyph must render correctly.

---

# 11. Layout

Do not make every section:

```text
heading
subtitle
three cards
```

Do not make every object a rounded card.

Use:

- editorial layouts
- lists
- dividers
- typography
- colour fields
- full-bleed sections
- asymmetric compositions
- selective cards

Cards should represent meaningful objects, not merely provide decoration.

---

# 12. Product pages

Primary routes:

- `/`
- `/about`
- `/search`
- `/problems`
- `/problems/[id]`
- `/account`
- `/setup`
- `/docs`
- `/docs/[slug]`

Reuse actual backend/API contracts.

Do not invent endpoints.

Do not invent data.

---

# 13. Home / About

Communicate within approximately 10 seconds:

1. What keळ is
2. Why it matters
3. What the user can do

The primary action should be search.

Use:

**What's the best way to…?**

The hero should combine:

- brand
- banana
- strong typography
- vibrant yellow
- concise product explanation
- search

Avoid meaningless AI marketing language.

---

# 14. Search

Search is a core product experience.

Results should expose real available information such as:

- recommended procedure
- why it is recommended
- evidence
- verification
- applicability
- problem
- alternatives

Design:

- initial
- typing
- loading
- skeleton
- results
- no results
- weak results
- error
- unavailable
- slow network

Search should feel like a knowledge engine rather than a generic search page.

---

# 15. Problems

Problems are meaningful knowledge objects.

A Problem represents a family of related procedures.

Problem listing should feel like a knowledge commons.

Problem detail should expose real available information such as:

- description
- best procedure
- alternatives
- evidence
- verification
- contributors
- history
- rankings

Do not invent rankings or counts.

---

# 16. Account

Use real backend data only.

Possible areas:

- identity
- workspace
- contributions
- procedures
- evidence
- problems
- rankings
- activity

If data is unavailable, design a beautiful honest empty state.

---

# 17. Setup

Setup should feel like a polished product control centre, not a legacy admin page.

Where supported, cover:

- workspace
- MCP
- integrations
- API/connection
- billing
- usage
- security
- preferences

Never expose secrets.

Never invent integrations.

Never invent billing functionality.

---

# 18. Docs

Build a fast, readable documentation system.

Support:

- document index
- search
- categories
- individual documents
- navigation
- table of contents where appropriate

Policy/legal content should prioritise readability and trust.

---

# 19. UX states

Every important page must consider:

- loading
- skeleton
- empty
- no results
- error
- 404
- 403
- offline
- permission denied
- unavailable
- not configured
- slow request

Do not leave these as browser defaults.

---

# 20. Performance

The website must work well on old hardware.

Assume:

- old CPU
- modest RAM
- mediocre network
- mobile browser

Prefer:

- server/static rendering
- minimal JavaScript
- CSS effects
- SVG
- lazy loading
- small assets
- minimal dependencies

Avoid:

- WebGL
- heavy canvas
- huge animation libraries
- autoplay video
- continuous animation
- giant images
- unnecessary client-side state

Beauty must not come at the cost of speed.

---

# 21. Animation

Animation should reinforce the brand.

Use:

- subtle transitions
- hover feedback
- search transitions
- gentle banana movement
- state transitions

Avoid:

- perpetual floating objects
- excessive parallax
- animated gradients
- constant motion
- giant page transitions

Respect `prefers-reduced-motion`.

---

# 22. Accessibility

Maintain:

- semantic HTML
- keyboard navigation
- visible focus
- screen-reader labels
- sufficient contrast
- readable text
- reduced motion support
- accessible forms

Do not sacrifice accessibility for aesthetics.

---

# 23. Responsive design

Design intentionally for:

- desktop
- laptop
- tablet
- mobile

Do not simply shrink the desktop layout.

---

# 24. Browser verification

After meaningful UI work:

1. run the application
2. inspect the rendered page
3. use browser/screenshot tooling where available
4. compare against the intended design
5. fix visual issues
6. repeat

Specifically inspect:

- spacing
- typography
- colours
- logo rendering
- banana composition
- backgrounds
- mobile layout
- loading states
- error states

Do not declare UI complete based solely on source code.

---

# 25. Figma workflow

When Figma MCP is available:

1. inspect relevant designs
2. inspect variables
3. inspect components
4. inspect layout
5. implement faithfully
6. reuse existing design-system decisions

Do not silently invent a competing design system.

If the existing Figma design is clearly incomplete, improve it deliberately rather than making arbitrary local deviations.

---

# 26. Mobbin workflow

Use Mobbin for research before designing unfamiliar interactions.

Research principles.

Do not copy visual designs.

Do not reproduce another company's branding.

---

# 27. Storybook workflow

Before creating UI components:

1. query Storybook
2. inspect existing components
3. inspect documented props
4. reuse components when appropriate

Never hallucinate props.

When adding important components:

- add stories
- include key states
- include responsive behaviour
- include loading/error/empty states where relevant

---

# 28. Engineering discipline

Before making substantial changes:

1. inspect repository
2. inspect frontend architecture
3. inspect routing
4. inspect existing components
5. inspect styling
6. inspect API contracts
7. inspect auth
8. inspect billing
9. inspect setup/configuration
10. inspect documentation infrastructure

Reuse existing infrastructure.

Do not rewrite unrelated systems.

Do not create fake APIs.

Do not create fake data.

---

# 29. Brand quality gate

Before declaring the website finished:

### Brand

Does it immediately feel like keळ?

### Logo

Would the mark work as a favicon?

Does `ळ` have a meaningful relationship with the mark?

### Colour

Is the yellow vibrant and recognisable?

### Background

Do the backgrounds feel art-directed rather than empty?

### Banana

Does the banana feel like a designed object rather than an icon?

### Typography

Does the type contribute to the identity?

### UX

Can someone understand the product quickly?

### Search

Does search feel like the centre of the product?

### Problems

Do Problems feel like knowledge objects rather than database rows?

### Performance

Does it remain fast on old hardware?

### Data integrity

Did we avoid inventing backend capabilities or fake metrics?

---

# Final rule

Do not optimise for:

> "finish the website quickly."

Optimise for:

> **"Build a production-ready website that looks unmistakably like keळ."**

The desired reaction is:

> "What the fuck is this? This is beautiful."

followed immediately by:

> "Oh, I understand what it does."

Never settle for:

> "This looks like a polished AI SaaS template."