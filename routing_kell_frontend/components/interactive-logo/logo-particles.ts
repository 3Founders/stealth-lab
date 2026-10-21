import type { LogoGeometry } from "./types";
import { buildLocalEdges, type Edges } from "./spatial-grid";

const ALPHA_BUCKETS = 6;
// Minimum divisor for the cursor's push direction -- see step()'s comment.
const DIRECTION_FLOOR = 10;

export interface ParticleConfig {
  width: number;
  height: number;
  margin: number;
  maxLinkRadius: number;
  maxLinksPerPoint: number;
  springK: number;
  damping: number;
  noiseAmp: number;
  noiseFreqA: number;
  noiseFreqB: number;
  /** "r,g,b" strings read from the site's own --ink/--yellow custom properties. */
  inkRGB: string;
  yellowRGB: string;
}

/**
 * A logo made of points with stable home positions (`tx`/`ty`, from the real mark) that
 * they are always springing back toward. Motion and the cursor only ever displace them
 * from that target -- the geometry itself never changes after init/resize.
 */
export class LogoParticleSystem {
  readonly count: number;
  x: Float32Array;
  y: Float32Array;
  vx: Float32Array;
  vy: Float32Array;
  tx: Float32Array;
  ty: Float32Array;
  phase: Float32Array;
  radius: Float32Array;
  isContour: Uint8Array;
  isAccent: Uint8Array;
  activation: Float32Array;

  edges: Edges;
  private cfg: ParticleConfig;
  private inkBuckets: number[][];
  private accentBuckets: number[][];

  constructor(geo: LogoGeometry, cfg: ParticleConfig) {
    this.cfg = cfg;
    this.count = geo.count;
    const n = geo.count;
    this.x = new Float32Array(n);
    this.y = new Float32Array(n);
    this.vx = new Float32Array(n);
    this.vy = new Float32Array(n);
    this.tx = new Float32Array(n);
    this.ty = new Float32Array(n);
    this.phase = new Float32Array(n);
    this.radius = new Float32Array(n);
    this.isContour = geo.isContour;
    this.isAccent = new Uint8Array(n);
    this.activation = new Float32Array(n);
    this.inkBuckets = Array.from({ length: ALPHA_BUCKETS }, () => []);
    this.accentBuckets = Array.from({ length: ALPHA_BUCKETS }, () => []);

    for (let i = 0; i < n; i++) {
      this.phase[i] = ((i * 2654435761) % 1000) / 1000 * Math.PI * 2;
      // Every point sampled from the mark's own yellow region reads as accent -- the ink
      // stroke portion stays fully ink, so this still isn't "the whole object yellow,"
      // just the swirl reading in its real colour instead of being thinned to half.
      this.isAccent[i] = geo.colorClass[i] === 1 ? 1 : 0;
      const base = geo.isContour[i] ? 1.5 : 1.15;
      this.radius[i] = this.isAccent[i] ? base + 0.25 : base;
    }

    this.applyTargets(geo, cfg);
    for (let i = 0; i < n; i++) {
      this.x[i] = this.tx[i];
      this.y[i] = this.ty[i];
    }
    this.edges = buildLocalEdges(this.tx, this.ty, n, cfg.maxLinkRadius, cfg.maxLinksPerPoint);
  }

  private applyTargets(geo: LogoGeometry, cfg: ParticleConfig) {
    const innerW = cfg.width - cfg.margin * 2;
    const innerH = cfg.height - cfg.margin * 2;
    for (let i = 0; i < this.count; i++) {
      this.tx[i] = cfg.margin + geo.nx[i] * innerW;
      this.ty[i] = cfg.margin + geo.ny[i] * innerH;
    }
  }

  /** Recomputes target positions and the link network for a new canvas size. */
  retarget(geo: LogoGeometry, cfg: ParticleConfig) {
    this.cfg = cfg;
    this.applyTargets(geo, cfg);
    this.edges = buildLocalEdges(this.tx, this.ty, this.count, cfg.maxLinkRadius, cfg.maxLinksPerPoint);
  }

  /** Snaps every particle straight onto its target -- the reduced-motion static frame. */
  settle() {
    for (let i = 0; i < this.count; i++) {
      this.x[i] = this.tx[i];
      this.y[i] = this.ty[i];
      this.vx[i] = 0;
      this.vy[i] = 0;
      this.activation[i] = 0;
    }
  }

  /**
   * Advances the simulation by one frame. `pointer` is smoothed canvas-space, or null when
   * idle. The cursor never injects raw velocity (that can pump energy into the system and
   * make points orbit instead of settle when the pointer lingers); instead it nudges the
   * spring's own target by a small, saturating amount, so the system is always relaxing
   * toward a well-defined point and a stationary cursor can never cause a runaway tangle.
   */
  step(t: number, pointer: { x: number; y: number } | null, pointerStrength: number) {
    const { springK, damping, noiseAmp, noiseFreqA, noiseFreqB } = this.cfg;
    const n = this.count;
    const influenceR = 96;
    const influenceR2 = influenceR * influenceR;
    const maxDisplacement = influenceR * pointerStrength;

    for (let i = 0; i < n; i++) {
      const phase = this.phase[i];
      const noiseX =
        (Math.sin(t * noiseFreqA + phase) * 0.6 + Math.sin(t * noiseFreqB + phase * 1.7) * 0.4) * noiseAmp;
      const noiseY =
        (Math.cos(t * noiseFreqA * 0.9 + phase * 1.3) * 0.6 + Math.cos(t * noiseFreqB * 1.1 + phase) * 0.4) *
        noiseAmp;

      let targetX = this.tx[i] + noiseX;
      let targetY = this.ty[i] + noiseY;

      if (pointer) {
        // Measured from the point's home position, not its current (possibly already
        // displaced) one -- "the material notices the cursor," not a feedback loop.
        const dx = this.tx[i] - pointer.x;
        const dy = this.ty[i] - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < influenceR2) {
          const d = Math.sqrt(d2);
          const falloff = 1 - d / influenceR;
          // The direction (dx,dy)/d is only numerically meaningful once d is a few pixels
          // wide -- flooring the divisor (instead of dividing by the true, possibly
          // near-zero, d) makes it fade smoothly to exactly zero as the cursor sweeps
          // directly over a point's target, rather than spinning unstably. With many
          // points packed densely (the mark's thicker regions), that instability was
          // exactly what produced a chaotic tangle under real, continuous mouse motion.
          const dDenom = Math.max(d, DIRECTION_FLOOR);
          const nx = dx / dDenom;
          const ny = dy / dDenom;
          const push = falloff * falloff * maxDisplacement;
          // Mostly tangential (the material "notices" the cursor) with a hint of push-away.
          targetX += -ny * push * 0.8 + nx * push * 0.35;
          targetY += nx * push * 0.8 + ny * push * 0.35;
          this.activation[i] = Math.min(1, this.activation[i] + falloff * 0.5);
        }
      }
      this.activation[i] *= 0.9;

      const ax = (targetX - this.x[i]) * springK;
      const ay = (targetY - this.y[i]) * springK;
      this.vx[i] = (this.vx[i] + ax) * damping;
      this.vy[i] = (this.vy[i] + ay) * damping;
      this.x[i] += this.vx[i];
      this.y[i] += this.vy[i];
    }
  }

  /** Renders the current frame. Lines are batched into a handful of alpha buckets to keep draw calls low. */
  draw(ctx: CanvasRenderingContext2D) {
    const { x, y, edges } = this;
    const maxLinkRadius = this.cfg.maxLinkRadius;
    ctx.lineWidth = 0.7;

    for (const bucket of this.inkBuckets) bucket.length = 0;
    for (const bucket of this.accentBuckets) bucket.length = 0;
    for (let e = 0; e < edges.count; e++) {
      const a = edges.a[e];
      const b = edges.b[e];
      const dx = x[a] - x[b];
      const dy = y[a] - y[b];
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist >= maxLinkRadius) continue;
      const rel = 1 - dist / maxLinkRadius;
      const bucket = Math.min(ALPHA_BUCKETS - 1, Math.floor(rel * rel * ALPHA_BUCKETS));
      // A link only reads as an accent when both ends sit in the mark's yellow region --
      // one restrained coloured patch, not scattered yellow specks over the whole mark.
      (this.isAccent[a] && this.isAccent[b] ? this.accentBuckets : this.inkBuckets)[bucket].push(e);
    }
    for (let bi = 0; bi < ALPHA_BUCKETS; bi++) {
      const list = this.inkBuckets[bi];
      if (!list.length) continue;
      const alpha = 0.1 + (bi / (ALPHA_BUCKETS - 1)) * 0.38;
      ctx.strokeStyle = `rgba(${this.cfg.inkRGB},${alpha})`;
      ctx.beginPath();
      for (const e of list) {
        ctx.moveTo(x[edges.a[e]], y[edges.a[e]]);
        ctx.lineTo(x[edges.b[e]], y[edges.b[e]]);
      }
      ctx.stroke();
    }
    for (let bi = 0; bi < ALPHA_BUCKETS; bi++) {
      const list = this.accentBuckets[bi];
      if (!list.length) continue;
      const alpha = 0.11 + (bi / (ALPHA_BUCKETS - 1)) * 0.33;
      ctx.strokeStyle = `rgba(${this.cfg.yellowRGB},${alpha})`;
      ctx.beginPath();
      for (const e of list) {
        ctx.moveTo(x[edges.a[e]], y[edges.a[e]]);
        ctx.lineTo(x[edges.b[e]], y[edges.b[e]]);
      }
      ctx.stroke();
    }

    ctx.beginPath();
    for (let i = 0; i < this.count; i++) {
      if (this.isAccent[i]) continue;
      const r = this.radius[i] + this.activation[i] * 0.5;
      ctx.moveTo(x[i] + r, y[i]);
      ctx.arc(x[i], y[i], r, 0, Math.PI * 2);
    }
    ctx.fillStyle = `rgba(${this.cfg.inkRGB},0.9)`;
    ctx.fill();

    ctx.beginPath();
    for (let i = 0; i < this.count; i++) {
      if (!this.isAccent[i]) continue;
      const r = this.radius[i] + this.activation[i] * 0.6;
      ctx.moveTo(x[i] + r, y[i]);
      ctx.arc(x[i], y[i], r, 0, Math.PI * 2);
    }
    ctx.fillStyle = `rgba(${this.cfg.yellowRGB},0.85)`;
    ctx.fill();
  }
}
