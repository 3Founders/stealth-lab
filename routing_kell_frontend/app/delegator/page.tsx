"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import RoomView from "@/components/delegator/RoomView";
import { springVars } from "@/lib/delegator/spring";
import { getSession, onSessionChange, type Session } from "@/lib/session";
import "./delegator.css";

/**
 * Delegator: group chat for people and agents. It is shown only to a signed-in visitor. That is a display gate, like
 * the rest of the site's account pages: the sandbox itself runs entirely in this tab (an in-memory relay with mock
 * agents) and calls no API, so there is no server data for the gate to protect yet. When real agents are connected,
 * the endpoints behind them must verify the token on the server; hiding this page is not what protects them.
 */
export default function DelegatorPage() {
  // undefined = still resolving (Supabase's session read is async): never flash the signed-out notice at a signed-in visitor.
  const [session, setSession] = useState<Session | null | undefined>(undefined);

  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); }); // signing out in another tab or from the menu hides it at once
    return () => { live = false; off(); };
  }, []);

  return (
    <>
      <section className="frame dg-page" style={springVars() as React.CSSProperties}>
        {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
        {session === null && (
          <div className="empty" role="status">
            <b>Sign in to use Delegator.</b>
            <p>It is available to signed-in accounts. <Link href="/sign-in?redirect=/delegator" style={{ textDecoration: "underline" }}>Sign in</Link>.</p>
          </div>
        )}
        {session && <RoomView />}
      </section>
    </>
  );
}
