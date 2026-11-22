# Blur: the efficient versions (research, 2026-11-02)

For the founder. Companion to docs/UIeffects.md (glass menus): the same question for blur itself - what blur is,
what it costs, which techniques give the frosted/progressive look efficiently on devices with a GPU and without one,
and where blur genuinely helps or hurts perception. Sources: browser-maker documentation and measurements (V),
engineering articles (S), OpenAlex at the bottom. Not legal or security advice.

## What blur costs, mechanically

Blur is a convolution: for every output pixel, a number of input pixels are considered, so cost grows with
**area x radius** (Chrome for Developers, V). Two CSS primitives differ in what they blur:

- `filter: blur()` blurs the element ITSELF (its text, its border, everything).
- `backdrop-filter: blur()` blurs what is BEHIND the element. WebKit, which introduced it to replace hand-rolled
  stacked-image hacks (the "Sisyphean techniques" of 2015), documents the pipeline: snapshot the backdrop, apply the
  filter in the engine with hardware support, composite - and warns plainly that the effect is expensive, more so
  the larger the element and the bigger the radius.

A promoted (composited) layer's blur runs on the GPU as a shader with no CPU repaint per frame (paint flashing
shows nothing) - the danger is not repainting but spending the GPU frame budget every frame while the blur is on
screen and moving.

## The technique ladder, cheapest first

1. **Static, small, deliberate.** A frosted bar or menu costs nothing while nothing changes, if the same two paint
   rules from UIeffects.md hold: small area, and only `transform`/`opacity` animate. Choose radii deliberately -
   vision research shows humans discriminate blur finely ("Bothersome blur", OpenAlex), so a too-small radius reads
   as a dirty render, not a design. Commit to clearly-blurred, or stay sharp.
2. **Never animate the radius; crossfade pre-blurred states.** Chrome's own guidance: animating a blur directly is
   "very slow" - a large GPU convolution every frame blows the 16 ms budget. The published alternative is to
   precompute a few increasingly-blurred versions (images, or stacked layers) and crossfade between them - the
   crossfade animates opacity, which is free. Any blur-on-scroll or blur-in transition in kell should be built this
   way.
3. **Blur small, scale up: the downscale trick.** Blur is a low-pass filter, so blurring at quarter resolution and
   upscaling loses almost nothing visible (Intel's investigation of fast real-time GPU blur algorithms, S). This is
   the standard game-engine answer to "large blur kernel on a mobile GPU", and it applies to any custom blur we
   ever ship in a canvas: render the blurred pass at 1/4 or 1/8, upscale, composite. CPU fallbacks (and no-GPU
   devices) use the same trick with a fast CPU blur.
4. **Fast Gaussian = three box blurs.** Where blur is computed in our own code (canvas previews, thumbnails, the
   artifact renderer), the settled algorithm is: box blur is separable (two 1D passes) and fast via integral
   images for large kernels, and repeating it ~3 times converges to a true Gaussian by the central limit theorem
   (MIT's computer-vision text; the FastGaussianBlur implementation is the reference). Kawase's iterated
   downsample-upsample filter is the GPU-side equivalent for bloom-class effects. Nobody should hand-roll a naive
   O(radius^2) Gaussian in 2026.
5. **Progressive blur (the iOS-style gradient edge)** (V - IchenDEV/framed-blur): a single `backdrop-filter` is
   uniform, so a gradient of blur is built by STACKING layers - each with a larger radius, each masked by a
   linear-gradient that limits how far it reaches (blur doubling per layer, mask ramps staggered; 4 layers gives
   0.5/1/2/4px, capped with a surface tint). This is the technique behind the frosted sticky headers in ChatGPT,
   Figma and Linear: content under the bar blurs MORE the closer it gets to the edge. `pointer-events: none` on the
   stack. Cost is N small blurs, not one huge one - and each layer is small, which is why it stays cheap.
6. **Edge artifacts have a spec answer now.** `backdrop-filter` edges historically bled colours in from beyond the
   element; the spec went duplicate-edge (which caused visible flicker as content crossed the edge) and Chromium
   shipped **mirror edgeMode** sampling in 2024 - a smooth, gradual introduction of colours at the edge. Practical
   consequence: test glass surfaces against MOVING content under the edge (scrolling rooms), and prefer slight
   overdraw (a 1-2px larger surface) on older engines.
7. **Blur placeholders for loading media: BlurHash / ThumbHash** (V - Mux's comparison): a compact string (20-30
   bytes, DCT-based, Base83) decodes client-side into a blurry preview; ThumbHash packs more detail plus
   aspect-ratio and alpha in the same space. For kel's file/image work (docs/adding_files.md) this is the zero-cost
   answer to "message looks complete before the attachment downloads": store the hash string in the payload
   metadata, decode to a canvas, crossfade the real file in.

## Fallbacks (S)

- `prefers-reduced-transparency: reduce` -> the same surface fully opaque, no backdrop-filter (as in UIeffects.md).
- Engines without `backdrop-filter` support -> solid translucent tint + stronger border; the layout never depends on
  the blur existing.
- Any canvas/WebGL blur path feature-detects and falls back to a CSS class; the downscale trick keeps the CPU path
  fast.
- `prefers-reduced-motion` -> no blur crossfade animations, instant states.

## Where blur helps perception - and where it backfires (OpenAlex)

- **Blur directs attention**: Semantic Depth of Field (2004) built focus+context visualisation on exactly this -
   blurring the periphery makes the sharp centre the focus; selective-attention studies (2016) show blur modulates
   what the eye picks. The legitimate design use in a chat: blur dimmed layers BEHIND dialogs and menus to push the
   eye to the live content (kel's scrim already does this).
- **But blur is itself perceptible and priced**: humans detect small blur differences ("Bothersome blur", 2005;
   blur-discrimination metric work, 2009; the conceptual model of blur perception, 2007), and salience research
   shows texture fading registers strongly (2001). Half-blurred content reads as a rendering bug. And no-reference
   blur metrics (2010-2024 cluster) exist precisely because blur quality is judgeable without an original - users
   will judge ours.

## Map to kell (I)

1. The chat's scrims, dialog backdrops and menus stay static blur (tier 1) - verify no surface animates its radius,
   and that scrim fade-ins animate opacity, not blur.
2. If the chat gets a frosted sticky header over scrolling rooms, take the progressive-blur stack (tier 5), 3-4
   layers, pointer-events none, tint cap.
3. Wire BlurHash/ThumbHash into the file/attachment plan: a 30-character placeholder in the payload metadata is the
   difference between a room that looks loaded and one that pops in.
4. If a blur ever must move, crossfade pre-blurred layers (tier 2) - the rule the Chrome team ships to everyone.

## OpenAlex (S)

- "Bothersome blur": a functional unit of blur perception (2005, 43 citations); Conceptual model of human blur
  perception (2007, 36); Constructing a metrics for blur perception with blur discrimination experiments (2009) -
  how finely humans see blur.
- Semantic Depth of Field - Using Blur for Focus+Context Visualization (2004, 15); The effect of blur on visual
  selective attention (2016); Texture fading correlates with stimulus salience (2001, 37) - blur as an attention
  tool.
- The no-reference blur-metric cluster (2010-2024) - blur quality is judgeable without the original.
- Bokeh rendering research (2022-2026) - the computational-photography frontier, relevant only if kell ever renders
  depth-aware media.

## Decisions this doc asks for

1. Confirm the rule: no animated blur radii anywhere in kel surfaces; motion over blur is a crossfade of
   pre-blurred layers.
2. Confirm the progressive-blur stack as the pattern if/when a frosted sticky header is built.
3. Confirm BlurHash/ThumbHash goes into the attachment payload metadata (pairs with docs/adding_files.md).
