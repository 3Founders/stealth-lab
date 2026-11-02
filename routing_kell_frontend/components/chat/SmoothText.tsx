"use client";
import { useEffect, useRef, useState } from "react";
import { revealStep, visiblePrefix } from "@/lib/chat/smooth";

/** The newest words are their own spans, so each one fades in once, when it first appears. Older words fold into one plain string. */
export const FADE_WINDOW = 28;

/** Splits revealed text into words (each with its trailing space). */
export const wordsOf = (s: string): string[] => s.match(/\S+\s*|\s+/g) ?? [];

/** Shown while the first words have not arrived yet: three pulses and a shimmering label. The same indicator @kell shows. */
export function Thinking({ label = "Thinking" }: { label?: string }) {
  return (
    <span className="dg-thinking dg-think" role="status" aria-label={label}>
      <span className="dg-pulse" /><span className="dg-pulse" /><span className="dg-pulse" />
      <span className="dg-think-label" aria-hidden="true">{label}</span>
    </span>
  );
}

/**
 * The text of a reply that is still arriving, revealed at a steady pace (lib/chat/smooth.ts) with each new word fading in
 * (a short ease-out, like iOS sheets; no motion with "reduce motion"). With "reduce motion" on, or in a background tab, it shows
 * what has been received as it is. A text that is not an extension of the last one starts over. Before any text has arrived it
 * shows the thinking indicator.
 */
export default function SmoothText({ text, onRevealed }: { text: string; onRevealed?: () => void }) {
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

  // Everything received is on screen: tell the owner once (it can then swap this bubble for the finished message).
  const told = useRef(false);
  useEffect(() => {
    if (!onRevealed) { told.current = false; return; }
    if (shown >= text.length && text.length > 0 && !told.current) { told.current = true; onRevealed(); }
  }, [shown, text, onRevealed]);

  const out = visiblePrefix(text, Math.min(shown, text.length));
  if (!out) return <Thinking />;
  const words = wordsOf(out);
  const base = Math.max(0, words.length - FADE_WINDOW);
  return (
    <>
      {words.slice(0, base).join("")}
      {words.slice(base).map((w, i) => <span key={base + i} className="dg-tok">{w}</span>)}
    </>
  );
}
