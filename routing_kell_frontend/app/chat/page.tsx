"use client";
import Image from "next/image";
import BackButton from "@/components/BackButton";
import Link from "next/link";
import { useEffect, useState } from "react";
import RoomView from "@/components/chat/RoomView";
import { springVars } from "@/lib/chat/spring";
import { getSession, onSessionChange, type Session } from "@/lib/session";
import "./chat.css";

/**
 * Chat: group chat for people and agents. It is shown only to a signed-in visitor. That is a display gate, like
 * the rest of the site's account pages: the sandbox itself runs entirely in this tab (an in-memory relay with mock
 * agents) and calls no API, so there is no server data for the gate to protect yet. When real agents are connected,
 * the endpoints behind them must verify the token on the server; hiding this page is not what protects them.
 */
export default function ChatPage() {
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
            <b>Sign in to use Chat.</b>
            <p>It is available to signed-in accounts. <Link href="/sign-in?redirect=/chat" style={{ textDecoration: "underline" }}>Sign in</Link>.</p>
          </div>
        )}
        {session && (
          <RoomView subject={session.subject} back={<BackButton inline />} brand={<Link href="/" aria-label="keळ, back to the site"><Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} priority style={{ height: 38, width: "auto" }} /></Link>} />
        )}
      </section>
    </>
  );
}
