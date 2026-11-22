"use client";
import { useEffect, useRef } from "react";

/**
 * The foot of a scrolling catalogue. It watches a sentinel near the bottom and asks for the next page
 * before the reader reaches it; the button is the same action for keyboard users and for browsers
 * without IntersectionObserver. The parent's busy flag makes a burst of scroll events one request.
 */
export default function LoadMore({ hasMore, busy, onMore }: {
  hasMore: boolean;
  busy: boolean;
  onMore: () => void;
}) {
  const sentinel = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasMore || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) onMore(); },
      { rootMargin: "400px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, onMore]);

  if (!hasMore) return null;
  return (
    <div ref={sentinel} className="load-more">
      <button type="button" onClick={onMore} disabled={busy}>
        {busy ? "Loading more Ways…" : "Load more Ways"}
      </button>
    </div>
  );
}
