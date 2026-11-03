"use client";
import { useEffect, useRef, useState } from "react";
import { AlertIcon, CheckIcon, CopyIcon, RetryIcon } from "@/components/chat/icons";

/** How long the tick (or the alert) stays before the copy icon comes back. */
export const COPY_HOLD_MS = 1600;

/**
 * Copy and Retry as hover-revealed icon buttons for a chat message (the icons are Lucide's, ISC, drawn on the house stroke -
 * components/chat/icons.tsx). Copy is always offered. Retry is offered only when a re-run makes sense: your own prompt re-sends the message;
 * an agent's answer re-runs what produced it (its prompt).
 *
 * Copy follows GitHub Primer's copy pattern (docs/connecting_agents.md, section 4): the copy icon becomes a tick in the success colour for
 * about a second and a half, or an alert icon in the critical colour when the browser refuses; the button keeps its size and focus, the
 * icons cross-fade in place (instantly with reduced motion), and a visually hidden status announces the result to screen readers. Nothing
 * is posted into the room on success; the owner's onCopy reports failure by returning false.
 */
export default function MessageActions({ copyText, retryText, onCopy, onRetry, retryLabel = "Ask again", holdMs = COPY_HOLD_MS }: {
  /** How long the tick or alert stays (a test shortens it). */
  holdMs?: number;
  /** The text Copy takes. */
  copyText: string;
  /** The text Retry re-runs; without it no retry button is drawn. */
  retryText?: string;
  /** Does the copy. Returns false (or throws) when it was refused; anything else counts as done. */
  onCopy: (text: string) => void | boolean | Promise<void | boolean>;
  onRetry?: (text: string) => void;
  retryLabel?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function copy() {
    let ok = true;
    try { ok = (await onCopy(copyText)) !== false; } catch { ok = false; }
    setState(ok ? "copied" : "failed");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), holdMs);
  }

  return (
    <span className="dg-acts">
      <button type="button" className="dg-act dg-act-copy" data-state={state} aria-label="Copy message" onClick={() => void copy()}>
        <span className="dg-act-ico" data-for="idle" aria-hidden="true"><CopyIcon /></span>
        <span className="dg-act-ico" data-for="copied" aria-hidden="true"><CheckIcon /></span>
        <span className="dg-act-ico" data-for="failed" aria-hidden="true"><AlertIcon /></span>
      </button>
      <span className="visually-hidden" role="status" aria-live="polite">{state === "copied" ? "Copied" : state === "failed" ? "Copying failed" : ""}</span>
      {retryText !== undefined && retryText !== "" && onRetry && (
        <button type="button" className="dg-act" aria-label={retryLabel} onClick={() => onRetry(retryText)}><RetryIcon /></button>
      )}
    </span>
  );
}
