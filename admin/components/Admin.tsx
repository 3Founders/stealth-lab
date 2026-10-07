"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import type { ApiState } from "@/lib/api";

export function Hero({ marker, title, lead }: { marker: string; title: string; lead?: string }) {
  return (
    <section className="page-hero frame grid">
      <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>{marker}</b></div>
      <h1 className="display"><AnimatedHeading>{title}</AnimatedHeading></h1>
      {lead && <p className="lead">{lead}</p>}
    </section>
  );
}

export function Stat({ label, value, note, bad }: { label: string; value: ReactNode; note?: ReactNode; bad?: boolean }) {
  return (
    <div className="stat" data-tone={bad ? "bad" : undefined}>
      <span className="caption dim">{label}</span>
      <span className="v">{value}</span>
      {note && <span className="small dim">{note}</span>}
    </div>
  );
}

/** Admin-specific honest states: signed-out and not-an-admin read differently from "no data". */
export function AdminNotice({ state, empty }: { state: ApiState<unknown>; empty?: string }) {
  if (state.kind === "unauthenticated")
    return (<div className="empty"><b>Sign in to continue.</b><p>The admin console only shows data to a signed-in operator. <Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in</Link>.</p></div>);
  if (state.kind === "forbidden")
    return (<div className="empty"><b>This account is not an admin.</b><p>The backend refused the request: the signed-in user does not hold the <code>admin:ops</code> scope. Nothing here is hidden client-side; it is simply not served.</p></div>);
  if (state.kind === "unconfigured")
    return (<div className="empty"><b>Not connected to a keळ backend.</b><p>Set <code>NEXT_PUBLIC_KEL_API_URL</code> (and the Supabase variables) in <code>admin/.env.local</code>.</p></div>);
  if (state.kind === "error")
    return (<div className="empty"><b>Couldn’t load.</b><p>{state.message}</p></div>);
  if (state.kind === "loading") return <div className="empty"><p>Loading…</p></div>;
  if (empty) return <div className="empty"><b>{empty}</b></div>;
  return null;
}

export function str(v: unknown): string {
  if (v == null) return "";
  return typeof v === "string" ? v : typeof v === "object" ? JSON.stringify(v) : String(v);
}
