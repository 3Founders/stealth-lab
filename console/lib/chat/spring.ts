/**
 * Spring motion, after the iOS model (blog.justinbuilds.mov/ios): a damped harmonic oscillator
 *   m x'' = -k (x - target) - b x'
 * with mass 1, k = (2π / response)² and b = 2 · dampingRatio · √k. A timed ease restarts from zero speed when it is
 * interrupted; a spring keeps its current velocity and bends toward the new target, so motion never stops dead.
 * Rubber-banding uses the same post's curve: overscroll = c·x·d / (d + c·x).
 */
export interface SpringSpec { response: number; damping: number }

/** `micro` for hovers and presses, `smooth` for entrances (critically damped, no overshoot), `bouncy` for pop-ins. */
export const SPRINGS = {
  micro: { response: 0.2, damping: 0.85 },
  smooth: { response: 0.5, damping: 1 },
  snappy: { response: 0.5, damping: 0.85 },
  bouncy: { response: 0.5, damping: 0.7 },
} as const satisfies Record<string, SpringSpec>;

export const coefficients = (s: SpringSpec) => {
  const k = (2 * Math.PI / s.response) ** 2;
  return { k, b: 2 * s.damping * Math.sqrt(k) };
};

const SUBSTEP = 1 / 240;

/** Advances a spring by `dt` seconds toward `target` (fixed substeps, semi-implicit Euler: stable at any frame rate). */
export function stepSpring(state: { x: number; v: number }, target: number, spec: SpringSpec, dt: number): { x: number; v: number } {
  const { k, b } = coefficients(spec);
  let { x, v } = state;
  let left = Math.min(dt, 0.1); // a backgrounded tab must not fire one giant step
  while (left > 1e-9) {
    const h = Math.min(SUBSTEP, left);
    v += (-k * (x - target) - b * v) * h;
    x += v * h;
    left -= h;
  }
  return { x, v };
}

/** Seconds until the spring is within a thousandth of its range and nearly still, starting from rest at distance 1. */
export function settleTime(spec: SpringSpec): number {
  let s = { x: 1, v: 0 };
  for (let t = 0; t < 4; t += SUBSTEP) {
    s = stepSpring(s, 0, spec, SUBSTEP);
    if (Math.abs(s.x) < 0.001 && Math.abs(s.v) < 0.01) return t + SUBSTEP;
  }
  return 4;
}

/** A spring as a CSS `linear()` timing function: progress 0 to 1 (a bouncy one overshoots 1), plus how long it lasts. */
export function springCss(spec: SpringSpec, points = 40): { easing: string; ms: number } {
  const T = settleTime(spec);
  const out: string[] = ["0"];
  let s = { x: 1, v: 0 };
  for (let i = 1; i < points; i++) {
    s = stepSpring(s, 0, spec, T / points);
    out.push((1 - s.x).toFixed(3).replace(/\.?0+$/, "") || "0");
  }
  out.push("1");
  return { easing: `linear(${out.join(", ")})`, ms: Math.ceil(T * 1000) };
}

/** CSS custom properties for every preset: `--dg-<name>` is the timing function, `--dg-<name>-d` its duration. */
export function springVars(): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const [name, spec] of Object.entries(SPRINGS)) {
    const { easing, ms } = springCss(spec);
    vars[`--dg-${name}`] = easing;
    vars[`--dg-${name}-d`] = `${ms}ms`;
  }
  return vars;
}

/** How far content moves when dragged `x` past an edge of a view `d` long: resistance grows, and it never passes `d`. */
export function rubberBand(x: number, d: number, c = 0.55): number {
  if (x <= 0 || d <= 0) return 0;
  return (c * x * d) / (d + c * x);
}

/**
 * One number chasing a target with spring physics. `retarget` keeps the velocity, so an interrupted move bends toward
 * its new target instead of restarting. `tick` is exposed so tests and the frame loop use the same code.
 */
export class SpringValue {
  x: number;
  v = 0;
  private target: () => number;
  private raf = 0;
  private last = 0;
  constructor(x: number, private spec: SpringSpec, private onUpdate: (x: number) => void, private onDone: () => void = () => {}) {
    this.x = x;
    this.target = () => x;
  }
  get running() { return this.raf !== 0; }

  /** `to` may be a function, for a target that moves while the spring runs (content growing under a scroll). */
  retarget(to: number | (() => number)) {
    this.target = typeof to === "function" ? to : () => to;
    if (!this.raf && typeof requestAnimationFrame === "function") {
      this.last = 0;
      this.raf = requestAnimationFrame(this.frame);
    }
  }

  /** Stops where it is; the velocity is dropped (the user took over). */
  cancel() {
    if (this.raf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.v = 0;
  }

  set(x: number) { this.x = x; this.v = 0; }

  /** Returns true once it has come to rest on the target. */
  tick(dt: number): boolean {
    const t = this.target();
    const s = stepSpring({ x: this.x, v: this.v }, t, this.spec, dt);
    const rest = Math.abs(s.x - t) < 0.4 && Math.abs(s.v) < 8; // pixels, pixels per second
    this.x = rest ? t : s.x;
    this.v = rest ? 0 : s.v;
    this.onUpdate(this.x);
    return rest;
  }

  private frame = (now: number) => {
    const dt = this.last ? (now - this.last) / 1000 : 1 / 60;
    this.last = now;
    if (this.tick(dt)) { this.raf = 0; this.onDone(); return; }
    this.raf = requestAnimationFrame(this.frame);
  };
}
