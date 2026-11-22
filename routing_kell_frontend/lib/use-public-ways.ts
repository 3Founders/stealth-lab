"use client";

/**
 * The catalogue as a scrolling list: one page at a time, appended, with no duplicates.
 *
 * The homepage and the task page both want the same behaviour, so the paging lives here rather than
 * in either page: `load(term)` starts a search, `more()` appends the next page. A generation counter
 * makes a slow first page unable to overwrite a newer search, and a busy flag makes a scroll that
 * fires several times in a second issue one request rather than five.
 */
import { useCallback, useRef, useState } from "react";
import { listPublicWaysPage, type PublicWay } from "./public-ways";

export type WaysState = "idle" | "loading" | "ok" | "error";

export function usePublicWays(pageSize = 12) {
  const [ways, setWays] = useState<PublicWay[]>([]);
  const [state, setState] = useState<WaysState>("idle");
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const cursor = useRef({ query: "", offset: 0, hasMore: false, busy: false, generation: 0 });

  const load = useCallback(async (term: string) => {
    const c = cursor.current;
    const generation = ++c.generation;
    c.query = term;
    c.offset = 0;
    c.hasMore = false;
    c.busy = true;
    setWays([]);
    setHasMore(false);
    setState("loading");
    const page = await listPublicWaysPage(term, pageSize, 0);
    if (generation !== c.generation) return;          // a newer search already started
    c.busy = false;
    if (page.kind !== "ok") {
      setState("error");
      return;
    }
    c.offset = page.data.nextOffset;
    c.hasMore = page.data.hasMore;
    setWays(page.data.ways);
    setHasMore(page.data.hasMore);
    setState("ok");
  }, [pageSize]);

  const more = useCallback(async () => {
    const c = cursor.current;
    if (c.busy || !c.hasMore) return;
    c.busy = true;
    const generation = c.generation;
    setLoadingMore(true);
    const page = await listPublicWaysPage(c.query, pageSize, c.offset);
    if (generation !== c.generation) {
      c.busy = false;
      setLoadingMore(false);
      return;
    }
    c.busy = false;
    setLoadingMore(false);
    if (page.kind !== "ok") return;                    // keep what is already on screen
    c.offset = page.data.nextOffset;
    c.hasMore = page.data.hasMore;
    setWays((prev) => {
      const seen = new Set(prev.map((w) => w.id));
      return [...prev, ...page.data.ways.filter((w) => !seen.has(w.id))];
    });
    setHasMore(page.data.hasMore);
  }, [pageSize]);

  return { ways, state, hasMore, loadingMore, load, more };
}
