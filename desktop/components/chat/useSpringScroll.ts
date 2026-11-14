"use client";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { SPRINGS, SpringValue } from "@/lib/chat/spring";

const NEAR_END = 120; // px from the bottom that still counts as "reading the latest"

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Spring scrolling for a message viewport, in place of `scroll-behavior: smooth`:
 *  - `scrollToEnd` moves with a spring that keeps its velocity if it is called again mid-flight, and it follows content
 *    that grows while it runs. Wheel, touch or keys hand control back to the user at once.
 *  - New content scrolls into view only if the reader was already at the bottom. Scrolling UP at any distance hands
 *    control back and keeps it: the next streamed token must not pull the reader back down (2026-11-08).
 *  - The "Latest" button is about where the reader is, not only about the follow flag: inside NEAR_END it stays hidden
 *    even after a nudge upward, so a 2 px reflow at the end of a reply cannot pop it up (2026-11-13).
 * The top and bottom ends are left to the browser (`overscroll-behavior: contain` on the viewport). The custom wheel
 * rubber-band this file used to have fought trackpad momentum and made both ends snap and jitter; the website's copy
 * already dropped it and the desktop now matches.
 * With reduced motion all of it is instant.
 */
export function useSpringScroll(viewport: RefObject<HTMLDivElement | null>, content: RefObject<HTMLDivElement | null>, resetKey: string) {
  const scroll = useRef<SpringValue | null>(null);
  const atEnd = useRef(true);
  const jumping = useRef(false);                    // a programmatic move to the end is in flight
  const [atBottom, setAtBottom] = useState(true);   // for the "scroll to latest" button; the ref is the hot path
  const atBottomRef = useRef(true);                 // the same value for the scroll handler, without re-subscribing

  const setBottom = useCallback((next: boolean) => {
    if (next === atBottomRef.current) return;
    atBottomRef.current = next;
    setAtBottom(next);
  }, []);

  const toEnd = useCallback(() => {
    const el = viewport.current;
    return el ? Math.max(0, el.scrollHeight - el.clientHeight) : 0;
  }, [viewport]);

  const scrollToEnd = useCallback((opts: { instant?: boolean } = {}) => {
    const el = viewport.current;
    if (!el) return;
    // Asking for the end is the reader asking for the end: hide the button now, not when the spring lands.
    setBottom(true);
    if (opts.instant || reduced()) { scroll.current?.cancel(); el.scrollTop = toEnd(); atEnd.current = true; jumping.current = false; return; }
    if (!scroll.current) scroll.current = new SpringValue(el.scrollTop, SPRINGS.smooth, (x) => { if (viewport.current) viewport.current.scrollTop = x; });
    else if (!scroll.current.running) scroll.current.set(el.scrollTop);
    jumping.current = true;
    scroll.current.retarget(toEnd);
  }, [viewport, toEnd, setBottom]);

  /** Call after content changed: follows the conversation only when the reader was at the bottom. */
  const follow = useCallback(() => { if (atEnd.current) scrollToEnd(); }, [scrollToEnd]);

  useEffect(() => {
    const el = viewport.current;
    if (!el) return;

    let lastTop = el.scrollTop;
    const track = () => {
      const top = el.scrollTop;
      const remaining = el.scrollHeight - el.clientHeight - top;
      const nearEnd = remaining <= NEAR_END;
      // Moving up by any real amount is the reader taking over, even while still within NEAR_END. This is what stops
      // the next streamed token from yanking the view back to the bottom.
      let next = atEnd.current;
      if (top < lastTop - 1) next = false;
      else if (nearEnd) next = true;
      if (next !== atEnd.current) atEnd.current = next;
      if (nearEnd) jumping.current = false;
      // The button follows proximity, not the follow flag. While we are following or a jump is still in flight, it
      // stays hidden; once the reader has left, it appears only beyond the near-end zone.
      setBottom(atEnd.current || jumping.current ? true : nearEnd);
      lastTop = top;
    };
    const stop = () => { jumping.current = false; scroll.current?.cancel() };
    const leave = () => { atEnd.current = false }; // the scroll event decides whether the button is needed
    const onWheel = (e: WheelEvent) => {
      stop();
      if (e.deltaY < 0) leave(); // scrolling up: follow no more until the reader returns to the bottom
    };
    const onKey = (e: KeyboardEvent) => {
      if (["ArrowUp", "PageUp", "Home"].includes(e.key)) leave();
      stop();
    };
    let touchY: number | null = null;
    const onTouchStart = (e: TouchEvent) => { touchY = e.touches[0]?.clientY ?? null; stop(); };
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY;
      if (touchY !== null && y !== undefined && y > touchY + 1) leave(); // finger down = content up
      touchY = y ?? touchY;
    };
    // A drag on the scrollbar is the reader taking over too; without this the spring can fight the thumb.
    const onPointerDown = () => stop();

    // A reply that streams in or an agent view that replaces the chat changes the content height without a scroll.
    // Re-track on that too, so the button reflects the new end rather than the old one.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => track()) : null;
    ro?.observe(el);
    if (content.current) ro?.observe(content.current);

    track();
    el.addEventListener("scroll", track, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("keydown", onKey);
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
    el.addEventListener("pointerdown", onPointerDown, { passive: true });
    return () => {
      ro?.disconnect();
      el.removeEventListener("scroll", track);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("keydown", onKey);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("pointerdown", onPointerDown);
      scroll.current?.cancel();
    };
  }, [viewport, content, resetKey, setBottom]);

  return { scrollToEnd, follow, atEnd: atBottom };
}
