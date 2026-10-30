"use client";
import { useEffect, useRef, useState } from "react";
import { revealStep, visiblePrefix } from "@/lib/chat/smooth";

/**
 * The text of a reply that is still arriving, revealed at a steady pace (lib/chat/smooth.ts). With "reduce motion" on, or in a
 * background tab, it shows what has been received as it is. A text that is not an extension of the last one starts over.
 */
export default function SmoothText({ text }: { text: string }) {
  const [shown, setShown] = useState(0);
  const pos = useRef(0);
  const prev = useRef("");
  const target = useRef(text);
  target.current = text;

  useEffect(() => {
    if (!text.startsWith(prev.current)) { pos.current = 0; setShown(0); }
    prev.current = text;
  }, [text]);

  useEffect(() => {
    const reduce = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { pos.current = target.current.length; setShown(pos.current); return; }
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const goal = target.current.length;
      if (pos.current < goal) {
        pos.current = revealStep(pos.current, goal, now - last);
        setShown(Math.floor(pos.current));
      }
      last = now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const out = visiblePrefix(text, Math.min(shown, text.length));
  return <>{out || "…"}</>;
}
