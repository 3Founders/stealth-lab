"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import { forgetInvite, recallInvite, rememberInvite } from "@/lib/chat/pending-invite";
import { roomsApi } from "@/lib/chat/rooms-api";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

/** Redeem a room invite link. The room then appears in your Chat on every device you sign in on. */
export default function JoinRoomPage() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ROOM</b></div>
        <h1 className="display"><AnimatedHeading>Join a room</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="profile-wrap">
          <Suspense fallback={<p className="small dim" role="status">Loading…</p>}><Join /></Suspense>
        </div>
      </section>
    </>
  );
}

function Join() {
  const fromAddress = useSearchParams().get("code") ?? "";
  // The address can lose its ?code= on the way through sign-in and onboarding; the code is kept for that (lib/chat/pending-invite.ts).
  const [code, setCode] = useState(fromAddress);
  useEffect(() => {
    if (fromAddress) { rememberInvite(fromAddress); setCode(fromAddress); }
    else setCode(recallInvite());
  }, [fromAddress]);
  const api = useMemo(() => roomsApi({ token: getAccessToken }), []);
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [joined, setJoined] = useState<{ title: string; already: boolean } | null>(null);

  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);

  async function join() {
    setBusy(true); setError("");
    try { const r = await api.join(code); forgetInvite(); setJoined({ title: r.title, already: r.already_member }); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(false); }
  }

  if (!code) return <div className="empty" role="alert"><b>This link is missing its invite code.</b><p>Ask whoever invited you for a new link.</p></div>;
  if (session === undefined) return <p className="small dim" role="status">Checking your session…</p>;
  if (session === null) {
    const back = `/room/join?code=${encodeURIComponent(code)}`;
    return <div className="empty" role="status"><b>Sign in to join.</b><p><Link href={`/sign-in?redirect=${encodeURIComponent(back)}`} style={{ textDecoration: "underline" }}>Sign in</Link>, then you&rsquo;ll come back here to join.</p></div>;
  }
  if (joined) {
    return (
      <div className="profile-card" role="status">
        <h2 className="h3">{joined.already ? `You're already in ${joined.title}.` : `You've joined ${joined.title}.`}</h2>
        <p className="small dim">It shows up in your Chat, on every device you sign in on. Rooms are open: keळ can read them, and connectors like Claude and ChatGPT can join. They aren&rsquo;t end-to-end encrypted.</p>
        <div className="cluster"><Link className="btn-ink" href="/chat"><span>Open Chat</span></Link></div>
      </div>
    );
  }
  return (
    <div className="profile-card">
      <h2 className="h3">You&rsquo;ve been invited to a room</h2>
      <p>Joining adds the room to your account and shows you its history. The people in it will see your name.</p>
      <div className="cluster"><button type="button" className="btn-ink" onClick={() => void join()} disabled={busy}><span>{busy ? "Joining…" : "Join the room"}</span><span className="sq" aria-hidden="true">→</span></button></div>
      {error && <p className="small" role="alert">{error}</p>}
    </div>
  );
}
