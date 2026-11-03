"use client";
import { useCallback, useEffect, useRef, type RefObject } from "react";
import { SPRINGS, SpringValue, rubberBand } from "@/lib/chat/spring";

const NEAR_END = 120; // px from the bottom that still counts as "reading the latest"
const RELEASE_MS = 90; // no wheel input for this long: the finger has let go
const PULL = 0.55; // the post's touch coefficient

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Spring scrolling for a message viewport, in place of `scroll-behavior: smooth`:
 *  - `scrollToEnd` moves with a spring that keeps its velocity if it is called again mid-flight, and it follows content
 *    that grows while it runs. Wheel, touch or keys hand control back to the user at once.
 *  - New content scrolls into view only if the reader was already at the bottom.
 *  - Pulling past either edge with a wheel or trackpad moves the content with rubber-band resistance, then springs back.
 * With reduced motion all of it is instant.
 */
export function useSpringScroll(viewport: RefObject<HTMLDivElement | null>, content: RefObject<HTMLDivElement | null>, resetKey: string) {
  const scroll = useRef<SpringValue | null>(null);
  const band = useRef<SpringValue | null>(null);
  const atEnd = useRef(true);

  const toEnd = useCallback(() => {
    const el = viewport.current;
    return el ? Math.max(0, el.scrollHeight - el.clientHeight) : 0;
  }, [viewport]);

  const scrollToEnd = useCallback((opts: { instant?: boolean } = {}) => {
    const el = viewport.current;
    if (!el) return;
    if (opts.instant || reduced()) { scroll.current?.cancel(); el.scrollTop = toEnd(); atEnd.current = true; return; }
    if (!scroll.current) scroll.current = new SpringValue(el.scrollTop, SPRINGS.smooth, (x) => { if (viewport.current) viewport.current.scrollTop = x; });
    else if (!scroll.current.running) scroll.current.set(el.scrollTop);
    scroll.current.retarget(toEnd);
  }, [viewport, toEnd]);

  /** Call after content changed: follows the conversation only when the reader was at the bottom. */
  const follow = useCallback(() => { if (atEnd.current) scrollToEnd(); }, [scrollToEnd]);

  useEffect(() => {
    const el = viewport.current, inner = content.current;
    if (!el || !inner) return;

    const track = () => { atEnd.current = el.scrollHeight - el.clientHeight - el.scrollTop <= NEAR_END; };
    const stop = () => scroll.current?.cancel();

    let pull = 0; // finger distance past the edge: + above the top, - below the bottom
    let timer: number | undefined;
    const draw = (px: number) => { inner.style.transform = px ? `translate3d(0, ${px.toFixed(2)}px, 0)` : ""; };
    const release = () => {
      const from = Math.sign(pull) * rubberBand(Math.abs(pull), el.clientHeight, PULL);
      if (!band.current) band.current = new SpringValue(from, SPRINGS.smooth, draw, () => { pull = 0; draw(0); });
      band.current.set(from);
      band.current.retarget(0);
    };
    const onWheel = (e: WheelEvent) => {
      if (reduced()) return;
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      const top = el.scrollTop <= 0, bottom = el.scrollTop >= el.scrollHeight - el.clientHeight - 1;
      if (!pull && !((top && dy < 0) || (bottom && dy > 0))) { stop(); return; } // ordinary scrolling
      e.preventDefault();
      stop();
      band.current?.cancel();
      const next = pull - dy;
      pull = Math.sign(next) === Math.sign(pull) || pull === 0 ? next : 0; // reversing past the edge ends the pull
      draw(Math.sign(pull) * rubberBand(Math.abs(pull), el.clientHeight, PULL));
      window.clearTimeout(timer);
      timer = window.setTimeout(release, RELEASE_MS);
    };

    track();
    el.addEventListener("scroll", track, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("touchstart", stop, { passive: true });
    el.addEventListener("keydown", stop);
    return () => {
      el.removeEventListener("scroll", track);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", stop);
      el.removeEventListener("keydown", stop);
      window.clearTimeout(timer);
      scroll.current?.cancel();
      band.current?.cancel();
      draw(0);
    };
  }, [viewport, content, resetKey]); // the content element is replaced when the room changes

  return { scrollToEnd, follow };
}
