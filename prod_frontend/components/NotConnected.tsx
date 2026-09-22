import Link from "next/link";
import type { ApiState } from "@/lib/api";

/** Honest empty/failed states shared by data-backed pages. */
export default function StateNotice({ state, empty }: { state: ApiState<unknown>; empty?: string }) {
  if (state.kind === "unauthenticated")
    return (
      <div className="empty">
        <b>Sign in to see this.</b>
        <p>This is your own private data — nothing is shown until you're signed in. <Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in</Link>.</p>
      </div>
    );
  if (state.kind === "forbidden")
    return (<div className="empty"><b>Not available.</b><p>{state.message}</p></div>);
  if (state.kind === "unconfigured")
    return (
      <div className="empty">
        <b>Not connected to a keळ backend.</b>
        <p>Nothing is shown here because there is nothing to show — no data has been made up. Set <code>NEXT_PUBLIC_KEL_API_URL</code> to a running backend (see <a href="/docs#getting-started" style={{ textDecoration: "underline" }}>Getting started</a>) and this page will read from it.</p>
      </div>
    );
  if (state.kind === "error")
    return (<div className="empty"><b>Couldn’t load.</b><p>{state.message} Nothing has been substituted.</p></div>);
  if (state.kind === "loading") return <div className="empty"><p>Loading…</p></div>;
  if (empty) return (<div className="empty"><b>{empty}</b><p>An empty result is reported as empty. It doesn’t mean a goal has no way of being done — only that none is recorded here yet.</p></div>);
  return null;
}
