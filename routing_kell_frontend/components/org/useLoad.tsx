"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export type Loaded<T> = { state: "loading" } | { state: "error"; message: string; status?: number } | { state: "ok"; data: T };

/** Runs `fn` when `key` changes (and on reload()). Late answers from an earlier run are dropped. */
export function useLoad<T>(fn: () => Promise<T>, key: string): { loaded: Loaded<T>; reload: () => void } {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: "loading" });
  const [n, setN] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let live = true;
    setLoaded({ state: "loading" });
    fnRef.current().then((data) => { if (live) setLoaded({ state: "ok", data }); })
      .catch((e: unknown) => { if (live) setLoaded({ state: "error", message: e instanceof Error ? e.message : "Something went wrong.", status: (e as { status?: number })?.status }); });
    return () => { live = false; };
  }, [key, n]);
  return { loaded, reload: useCallback(() => setN((x) => x + 1), []) };
}

/** The shared look for a failed or waiting load. */
export function LoadState({ loaded, reload, what }: { loaded: Loaded<unknown>; reload: () => void; what: string }) {
  if (loaded.state === "loading") return <p className="small dim" role="status">Loading {what}…</p>;
  if (loaded.state === "error") {
    return (
      <div className="empty" role="alert">
        <b>Couldn&rsquo;t load {what}.</b>
        <p>{loaded.message}</p>
        <div className="cluster"><button type="button" className="btn-line" onClick={reload}>Try again</button></div>
      </div>
    );
  }
  return null;
}
