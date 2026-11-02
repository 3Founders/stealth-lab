"use client";
import Wordmark from "@/components/Wordmark";
import BackButton from "@/components/BackButton";
import Link from "next/link";
import { useEffect, useState } from "react";
import RoomView from "@/components/chat/RoomView";
import { springVars } from "@/lib/chat/spring";
import { getSession, onSessionChange, type Session } from "@/lib/session";
import "./chat.css";

/**
 * Chat: group chat for people and agents. It is shown only to a signed-in visitor. That is a display gate, like
 * the rest of the site's account pages: rooms, relay tasks and connector tool calls verify the session on the
 * server (room_routes.py, delegate_routes.py, the room tools in mcp_server/server.py); hiding this page is not
 * what protects them. In-tab assistants call a model with the person's own key (or the opt-in kel pilot) and keep
 * no server history beyond the room events this page syncs.
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
          <RoomView subject={session.subject} back={<BackButton inline />} brand={<Link href="/" aria-label="keळ, back to the site"><Wordmark priority style={{ height: 38, width: "auto" }} /></Link>} />
        )}
      </section>
    </>
  );
}
