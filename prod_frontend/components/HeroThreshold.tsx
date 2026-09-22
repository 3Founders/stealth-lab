import Reveal from "@/components/Reveal";

/**
 * A restrained inline signal field: alignment/indexing lines resolving into place, not a
 * particle effect. Purely decorative (aria-hidden) -- the chapter marker text is the only
 * thing that carries real information (see HeroThreshold below). Static geometry, no
 * physics, no rAF; entrance motion is CSS transform/opacity only, driven by the single
 * `.threshold-visual.in` class the existing Reveal/IntersectionObserver mechanism adds.
 */
function SignalField() {
  const lines = [
    { i: 0, y: 12, x1: 40, x2: 430, d: "0ms" },
    { i: 1, y: 28, x1: 0, x2: 1180, d: "60ms" },
    { i: 2, y: 44, x1: 180, x2: 1150, d: "30ms" },
    { i: 3, y: 58, x1: 0, x2: 760, d: "90ms" },
    { i: 4, y: 72, x1: 300, x2: 1200, d: "50ms" },
  ];
  const nodes = [
    { i: 0, cx: 430, cy: 12, r: 3.2, d: "180ms" },
    { i: 1, cx: 690, cy: 44, r: 3.6, d: "220ms" },
    { i: 2, cx: 760, cy: 58, r: 3, d: "160ms" },
  ];
  // Each element's resting opacity travels only as the --o custom property, never as a
  // plain `opacity` (inline or SVG-attribute) value -- an inline style always wins over
  // any external stylesheet rule, which would permanently defeat the .motion hidden state
  // (opacity 0) the reveal transition depends on. CSS alone (see globals.css: a base
  // `.sig-line{opacity:var(--o)}` rule for the no-JS/reduced-motion case, overridden by
  // `.motion .sig-line{opacity:0}` pre-reveal, restored by `.in{opacity:var(--o)}`) is the
  // single source of truth for all three states.
  return (
    <svg className="threshold-signal" viewBox="0 0 1200 80" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <line
        x1="820" y1="76" x2="1200" y2="76" stroke="var(--cobalt)" strokeWidth="1.25"
        className="sig-line sig-cobalt" style={{ "--d": "20ms", "--o": 0.24 } as React.CSSProperties}
      />
      {lines.map((l) => (
        <line
          key={l.i} x1={l.x1} y1={l.y} x2={l.x2} y2={l.y} stroke="var(--ink)" strokeWidth="1.5"
          className={`sig-line sig-i${l.i}`} style={{ "--d": l.d, "--o": 0.55 + (l.i % 3) * 0.08 } as React.CSSProperties}
        />
      ))}
      {nodes.map((n) => (
        <circle
          key={n.i} cx={n.cx} cy={n.cy} r={n.r} fill="var(--ink)" className={`sig-node sig-n${n.i}`}
          style={{ "--d": n.d, "--o": 0.92 } as React.CSSProperties}
        />
      ))}
      <circle
        cx={980} cy={28} r={4.2} fill="var(--yellow)" className="sig-node sig-locator"
        style={{ "--d": "320ms", "--o": 1 } as React.CSSProperties}
      />
    </svg>
  );
}

/**
 * The quiet editorial pause between the hero's proposition and the first explanatory
 * section: a chapter rule plus a signal field, not a second hero. No chapter index/label
 * here -- the About section's own <Marker n="01" label="ABOUT" /> sits immediately after
 * it and already says that; repeating it here read as a duplicate rather than an echo.
 * Reuses the site's own Reveal/IntersectionObserver mechanism -- one entrance, no
 * continuous motion -- and its 12-column grid. Looks intentional as a completely static
 * composition even with the SVG removed.
 */
export default function HeroThreshold() {
  return (
    <section className="threshold frame grid" aria-hidden="true">
      <Reveal className="threshold-visual">
        <div className="threshold-rule-wrap">
          <div className="threshold-rule-line" aria-hidden="true" />
          <SignalField />
        </div>
      </Reveal>
    </section>
  );
}
