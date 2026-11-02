"use client";
import { CopyIcon, RetryIcon } from "@/components/chat/icons";

/**
 * Copy and Retry as hover-revealed icon buttons for a chat message (the icons are Lucide's, ISC, drawn on the
 * house stroke - components/chat/icons.tsx). Copy is always offered. Retry is offered only when a re-run makes
 * sense: your own prompt re-sends the message; an agent's answer re-runs what produced it (its prompt).
 */
export default function MessageActions({ copyText, retryText, onCopy, onRetry, retryLabel = "Ask again" }: {
  /** The text Copy takes. */
  copyText: string;
  /** The text Retry re-runs; without it no retry button is drawn. */
  retryText?: string;
  onCopy: (text: string) => void;
  onRetry?: (text: string) => void;
  retryLabel?: string;
}) {
  return (
    <span className="dg-acts">
      <button type="button" className="dg-act" aria-label="Copy message" onClick={() => onCopy(copyText)}><CopyIcon /></button>
      {retryText !== undefined && retryText !== "" && onRetry && (
        <button type="button" className="dg-act" aria-label={retryLabel} onClick={() => onRetry(retryText)}><RetryIcon /></button>
      )}
    </span>
  );
}
