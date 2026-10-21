"use client";
import { useEffect, useRef } from "react";
import { getLogoGeometry } from "./logo-mask";
import { LogoParticleSystem, type ParticleConfig } from "./logo-particles";
import type { LogoGeometry } from "./types";

const DESKTOP_POINTS = 1000;
const MOBILE_POINTS = 260;
const DPR_CAP = 1.75;
// Fraction of the influence radius a point's target can be nudged by, at most (saturating).
const POINTER_STRENGTH = 0.16;

function computePointBudget(cssWidth: number): number {
  const t = Math.max(0, Math.min(1, (cssWidth - 220) / (480 - 220)));
  return Math.round(MOBILE_POINTS + (DESKTOP_POINTS - MOBILE_POINTS) * t);
}

function cssVarRgb(name: string, fallback: string): string {
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim().replace("#", "");
    if (raw.length === 6) {
      const r = parseInt(raw.slice(0, 2), 16);
      const g = parseInt(raw.slice(2, 4), 16);
      const b = parseInt(raw.slice(4, 6), 16);
      if (![r, g, b].some(Number.isNaN)) return `${r},${g},${b}`;
    }
  } catch {
    // falls through to the brand default below
  }
  return fallback;
}

function buildConfig(width: number, height: number, inkRGB: string, yellowRGB: string): ParticleConfig {
  const size = Math.min(width, height);
  return {
    width,
    height,
    margin: Math.round(size * 0.1),
    // Measured against the real mark (now at the larger box size too): with a 1000-point
    // budget and declump minDist=0.95x (see logo-mask.ts), this keeps the max mutual-
    // neighbour count at 9 everywhere, zero points reaching 10+, including the tightly
    // curved stroke caps that used to mesh into a solid blob.
    maxLinkRadius: Math.max(14, size * 0.048),
    maxLinksPerPoint: 3,
    // Near-critically damped: settles in ~0.5s with essentially no overshoot/ringing,
    // instead of the old k=0.07/d=0.9's ~1.2s with a pronounced oscillation -- a
    // disturbed area should read as decisively "snapping back," not slowly wobbling.
    springK: 0.14,
    damping: 0.78,
    noiseAmp: size * 0.0035,
    noiseFreqA: 0.00055,
    noiseFreqB: 0.00091,
    inkRGB,
    yellowRGB,
  };
}

/**
 * The keळ mark as a living line drawing: points sampled from the real logo asset
 * (kel-mark.png), springing back to their logo-derived home position, sparsely
 * connected, gently breathing, and locally disturbed by the cursor. Purely decorative
 * -- aria-hidden, no state feeding React, everything lives in refs/typed arrays so the
 * animation loop never triggers a render.
 */
export default function InteractiveLogo() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const inkRGB = cssVarRgb("--ink", "23,23,23");
    const yellowRGB = cssVarRgb("--yellow", "255,210,31");

    let system: LogoParticleSystem | null = null;
    let geometry: LogoGeometry | null = null;
    let cfg = buildConfig(1, 1, inkRGB, yellowRGB);
    let raf = 0;
    let rafScheduled = false;
    let intersecting = false;
    let destroyed = false;
    const pointer = { x: 0, y: 0, active: false };
    const smoothed = { x: 0, y: 0 };

    function drawFrame() {
      if (!system || !ctx || !canvas) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      system.draw(ctx);
    }

    function resize() {
      if (!wrap || !canvas || !ctx) return;
      const rect = wrap.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      // Clamp the LOWER bound too, not just the cap: a reported devicePixelRatio below 1
      // (browser zoom under 100%, or certain devtools emulation states) would otherwise
      // render the backing store below native resolution, so fine 1px lines/dots get
      // blurred together into denser-looking clumps when the browser upscales it to fill
      // the CSS box -- purely a resolution artifact, not a position/geometry bug.
      const dpr = Math.min(Math.max(window.devicePixelRatio || 1, 1), DPR_CAP);
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cfg = buildConfig(rect.width, rect.height, inkRGB, yellowRGB);
      if (system && geometry) {
        system.retarget(geometry, cfg);
        if (reduced) system.settle();
        drawFrame();
      }
    }

    function scheduleLoop() {
      if (rafScheduled || destroyed || reduced) return;
      rafScheduled = true;
      raf = requestAnimationFrame(loop);
    }

    function loop(t: number) {
      rafScheduled = false;
      if (destroyed || !system) return;
      if (!intersecting || document.visibilityState !== "visible") return;
      smoothed.x += (pointer.x - smoothed.x) * 0.16;
      smoothed.y += (pointer.y - smoothed.y) * 0.16;
      system.step(t, pointer.active ? smoothed : null, POINTER_STRENGTH);
      drawFrame();
      scheduleLoop();
    }

    async function init() {
      try {
        const rect = wrap!.getBoundingClientRect();
        const budget = computePointBudget(rect.width || 480);
        geometry = await getLogoGeometry(budget);
        if (destroyed) return;
        resize();
        system = new LogoParticleSystem(geometry, cfg);
        if (reduced) {
          system.settle();
          drawFrame();
          return;
        }
        drawFrame();
        scheduleLoop();
      } catch (err) {
        console.warn("InteractiveLogo: failed to initialize", err);
      }
    }

    function onPointerMove(e: PointerEvent) {
      // offsetX/Y are already canvas-local -- no getBoundingClientRect() here, which
      // would force a synchronous layout on every single pointer-move event (these can
      // fire hundreds of times a second) and starve the paint pipeline, making the canvas
      // visibly lag behind its own already-recovered simulation state.
      pointer.x = e.offsetX;
      pointer.y = e.offsetY;
      pointer.active = true;
    }
    function onPointerLeave() {
      pointer.active = false;
    }

    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) intersecting = entry.isIntersecting;
        if (intersecting) scheduleLoop();
      },
      { threshold: 0.05 },
    );
    io.observe(wrap);

    const onVisibility = () => {
      if (document.visibilityState === "visible") scheduleLoop();
    };
    document.addEventListener("visibilitychange", onVisibility);

    const ro = new ResizeObserver(() => resize());
    ro.observe(wrap);

    if (!reduced) {
      canvas.addEventListener("pointermove", onPointerMove, { passive: true });
      canvas.addEventListener("pointerleave", onPointerLeave, { passive: true });
    }

    init();

    return () => {
      destroyed = true;
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onPointerLeave);
    };
  }, []);

  return (
    <div className="hero-art" ref={wrapRef} aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>
  );
}
