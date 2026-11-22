# Glass menus and translucent surfaces: what is beautiful AND cheap (research, 2026-11-02)

For the founder. The ask: the best and most efficient glass designs for menus and the like - the liquid-glass look,
including renderers like jeantimex's WebGPU one - and it must be efficient on devices WITH a GPU and WITHOUT one
(CPU-only, old phones, battery). Sources: repositories and measurements read directly (V), engineering guides (S), the
OpenAlex literature at the bottom. Not legal or security advice.

## What the reference renderer does (V - jeantimex/glass-effect-webgpu)

A real-time liquid-glass renderer in WebGPU (WGSL) with HTML-in-canvas backgrounds: true refraction with adjustable
thickness and bezel, specular highlights, progressive blur, spring-based liquid deformation on press and drag, and
presets for circle, switch, slider, split menu and player controls. Live DOM backgrounds are sampled through
`copyElementImageToTexture()` when supported, with `html2canvas` as the fallback. It is the most faithful version of
Apple's Liquid Glass (WWDC 2025: "translucent material that reflects and refracts its surroundings") on the web - and
the most expensive, because it renders the whole scene through a shader pipeline. That cost is the reason the rest of
this doc exists.

## The measurement that answers "efficient with AND without the GPU" (V - web-glass, L. Loehr)

The decisive published measurement compares two architectures on identical pixels:

- **Whole-scene filter** (the naive WebGPU/effect-everywhere approach): GPU utilisation **72-90%** while glass moves,
  main thread busy **566-1007 ms per second**, dropped frames.
- **Per-surface backdrop filter** (only the glass element's own area is filtered): GPU **34-40%**, main thread
  **82-117 ms/s**, zero dropped frames, SSIM 0.99 - the look is the same.
- **The no-GPU path**: when no WebGPU adapter exists, the same library computes the displacement maps **on the CPU**
  and the page looks identical (largest full-page difference: 3 of 255 levels; SSIM 1.000). The fallback is not a
  degraded look - it is the same output, computed by a different engine.

The lesson in one line: **glass cost is per-sampled-area, not per-effect** - filter the element's footprint, never
the scene, and pre-compute what can be pre-computed.

## The technique ladder, cheapest first

1. **Plain frosted CSS** (S): translucent background + `backdrop-filter: blur(...) saturate(...)` on a small element.
   No JS, composited by the browser. The cost model is paint (web.dev: paint is the most expensive part of the pixel
   pipeline): keep the glass AREA small, and while anything animates, change only `transform`/`opacity` - any other
   property repaints every frame.
2. **Refraction without sampling: a single `feDisplacementMap` on the element itself** (V - tomagranate/liquid-glass):
   the content's own pixels bend at the rim via one SVG filter primitive - no backdrop sampling at all, works in
   every browser (Chromium, Firefox, Safari), optional WebGL texture backend. The cheapest way to get the
   "liquid rim" look, and it degrades to just a blur if filters are disabled.
3. **Backdrop + displacement chain with a budget** (V - kube.io's physics-based displacement field; liqui.design's
   handbook): compute a displacement vector field (orthogonal to the border, magnitude falling off from the rim),
   pre-render it, and chain it with a mild blur. The documented budget:
   - the expensive parts are the **canvas-generated displacement map** and the **backdrop-filter chain**, in that
     order;
   - **cache maps module-wide, keyed by size + optics** - ten buttons of the same size cost one map;
   - **chromatic aberration costs ~3x the filter work** - leave it at 0 except on a hero surface;
   - rim width (bezel) ~28px for menus, ~11px for buttons; and ship a `material: auto` tier that **refracts where
     supported and frosts everywhere else**.
4. **A real GPU pipeline (WebGPU/WGSL)**, only for hero surfaces and only while interacting: jeantimex's renderer is
   the ceiling - real refraction, specular, progressive blur, spring liquid deformation. The rule from the
   measurements: per-surface scope, not whole-scene; and **idle must cost nothing** - a closed menu or a parked
   slider should run zero frames (the energy literature backs this: mobile web energy tracks rendering work, and
   frame-coherent rendering is measurably cheaper than brute-force per-frame rendering).

## Fallbacks and the people the effect can hurt (S)

- **Feature-detect, do not UA-sniff**: ask for a WebGPU adapter; on refusal, drop to tier 3 or 2; on no SVG filter
  support, drop to tier 1. Every tier should render the same menu, same position, same content - only the material
  changes (this is exactly liqui.design's `material: auto` and web-glass's CPU path).
- **`prefers-reduced-transparency: reduce`** is a real OS setting now: replace frosted glass with the SAME hue fully
  opaque and no backdrop-filter. Keep the colour, drop the translucency.
- **`prefers-reduced-motion: reduce`**: no spring deformation, no liquid wobble - instant open/close. kel's dialogs
  already honour this; the shader path must too.
- **Contrast is the law, glass is not**: WCAG 2.2 AA needs 4.5:1 for normal text (3:1 for large). Glass never carries
  contrast by itself - a tint or scrim under text does. The oldest finding in the field (OpenAlex: the 1995
  experimental evaluation of transparent UI tools, 94 citations; the AR legibility studies) says translucent
  backgrounds measurably hurt reading when content bleeds through - blur plus tint is not decoration, it is the
  legibility mechanism.

## Menus specifically (S)

- Glass menus are the BEST case for the effect: small area, transient, sitting above content - and the WORST case is
  a full-screen glass sheet or an always-on shader. Budget the hero treatment for one or two surfaces.
- Portal the menu to `document.body` and position from the trigger rect, so an ancestor `overflow: hidden` never
  clips it (documented in glass dropdown implementations); keep full keyboard support (arrow keys, Enter, Escape,
  outside-click) - the glass must not cost the a11y basics.
- A common, cheap menu recipe that reads well: `backdrop-filter: blur(12px) saturate(1.4)` + a translucent tint +
  a hairline border + a small shadow; add the refractive rim only when measurements say the surface can afford it.

## Map to kell (I)

kel's chat already has the tier-1 base: glass variables, blurred dialog backdrops, frosted panels. The doc's
recommendations, in order:

1. Keep tier 1 as the default for every menu, dialog and popover; verify the two paint rules hold (transform/opacity
   only while they animate; no full-screen glass).
2. Add the `prefers-reduced-transparency` block for the glass surfaces (same hue, opaque, no blur).
3. If the liquid look is wanted on a hero surface (say the Add-agent dialog or the attachment tray), take tier 3:
   cached, size-keyed displacement maps, dispersion off, `material: auto`, and pause everything when idle. Do not
   put a WebGPU pipeline behind menus - the measurements show per-surface filters give the same look at a third of
   the GPU for exactly the moving-menu case.

## OpenAlex (S; thin on glassmorphism itself, good on energy and legibility)

- An experimental evaluation of transparent user interface tools and information content (1995, 94 citations) - the
  foundational result: transparency costs reading performance; blur+contrast are the remedies.
- The Effects of Text Drawing Styles, Background Textures, and Natural Lighting on Text Legibility in Outdoor
  Augmented Reality (2006, 178 citations) - legibility over busy backgrounds, the AR twin of the same problem.
- Navigating the Manifold of Translucent Appearance (2024) - current work on translucent appearance modelling.
- Investigating the Correlation between Performance Scores and Energy Consumption of Mobile Web Apps (2020,
  36 citations); Energy-efficient mobile Web computing (2017); Exploiting frame coherence in real-time rendering for
  energy-efficient GPUs (2020) - the energy grounding for "idle must cost nothing".

## Decisions this doc asks for

1. Confirm tier 1 (frosted CSS) stays the default everywhere, with the two paint rules enforced in review.
2. Pick at most one or two hero surfaces for the refractive treatment (tier 3), with cached maps and `material: auto`.
3. Confirm both reduced-preference media queries are applied to every glass surface, shader path included.
