"use client";
import { useCallback, useEffect, useRef, type RefObject } from "react";
import { SPRINGS, SpringValue } from "@/lib/chat/spring";

const NEAR_END = 120; // px from the bottom that still counts as "reading the latest"

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Spring scrolling for a message viewport, in place of `scroll-behavior: smooth`:
 *  - `scrollToEnd` moves with a spring that keeps its velocity if it is called again mid-flight, and it follows content
 *    that grows while it runs. Wheel, touch or keys hand control back to the user at once.
 *  - New content scrolls into view only if the reader was already at the bottom.
 * The top and bottom ends are left to the browser (`overscroll-behavior: contain` on the viewport). A custom wheel
 * rubber-band used to translate the content there; it fought trackpad momentum and made both ends snap and jitter.
 * With reduced motion all of it is instant.
 */
export function useSpringScroll(viewport: RefObject<HTMLDivElement | null>, content: RefObject<HTMLDivElement | null>, resetKey: string) {
  const scroll = useRef<SpringValue | null>(null);
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
    const el = viewport.current;
    if (!el) return;

    let lastTop = el.scrollTop;
    const track = () => {
      const top = el.scrollTop;
      const nearEnd = el.scrollHeight - el.clientHeight - top <= NEAR_END;
      // Moving up by any real amount is the reader taking over, even while still within NEAR_END: the next streamed
      // token must not pull the view back down (2026-11-08).
      if (top < lastTop - 1) atEnd.current = false;
      else if (nearEnd) atEnd.current = true;
      lastTop = top;
    };
    const stop = () => scroll.current?.cancel();
    const onWheel = (e: WheelEvent) => { stop(); if (e.deltaY < 0) atEnd.current = false; };
    const onKey = (e: KeyboardEvent) => {
      if (["ArrowUp", "PageUp", "Home"].includes(e.key)) atEnd.current = false;
      stop();
    };

    track();
    el.addEventListener("scroll", track, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchstart", stop, { passive: true });
    el.addEventListener("keydown", onKey);
    return () => {
      el.removeEventListener("scroll", track);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", stop);
      el.removeEventListener("keydown", onKey);
      scroll.current?.cancel();
    };
  }, [viewport, content, resetKey]); // the content element is replaced when the room changes

  return { scrollToEnd, follow };
}
