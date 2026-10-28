"use client";
import Link from "next/link";
import { notFound } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import "@/app/org/org.css";
import { orgAdminEnabled, orgApi, type Joined } from "@/lib/org-admin";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

/** Redeem an invite code to join an organization. Behind the same flag as the organization pages. */
export default function JoinPage() {
  if (!orgAdminEnabled()) notFound();
  return <Join />;
}

function Join() {
  const api = useMemo(() => orgApi({ token: getAccessToken }), []);
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [joined, setJoined] = useState<Joined | null>(null);

  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);

  async function redeem(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) { setError("Paste the code you were sent."); return; }
    setBusy(true); setError("");
    try { setJoined(await api.join(code.trim())); setCode(""); }
    catch (err) { setError(err instanceof Error ? err.message : "That code didn't work."); }
    finally { setBusy(false); }
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>JOIN</b></div>
        <h1 className="display"><AnimatedHeading>Join an organization</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="org-wrap">
          {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
          {session === null && <div className="empty" role="status"><b>Sign in to join.</b><p><Link href="/sign-in?redirect=/join" style={{ textDecoration: "underline" }}>Sign in</Link>, then come back and paste your code.</p></div>}
          {session && !joined && (
            <form className="profile-card cform" onSubmit={(e) => void redeem(e)}>
              <div className="cfield"><label htmlFor="join-code">Invite code</label>
                <input id="join-code" type="text" value={code} onChange={(e) => { setCode(e.target.value); setError(""); }} autoComplete="off" spellCheck={false} />
                <span className="hint">The code your organization&rsquo;s admin sent you.</span></div>
              <div className="cform-submit"><button className="btn-ink" type="submit" disabled={busy}><span>{busy ? "Joining…" : "Join"}</span><span className="sq" aria-hidden="true">→</span></button></div>
              {error && <p className="small" role="alert">{error}</p>}
            </form>
          )}
          {joined && (
            <div className="profile-card" role="status">
              <h2 className="h3">You&rsquo;ve joined{joined.name ? ` ${joined.name}` : ""}.</h2>
              {joined.gateway && (
                <dl className="org-dl">
                  <dt>Address</dt><dd><code>{joined.gateway}</code></dd>
                  {joined.models && joined.models.length > 0 && <><dt>Models</dt><dd>{joined.models.map((m) => <code key={m} className="org-chip">{m}</code>)}</dd></>}
                </dl>
              )}
              <p className="small dim">Point your tool at this address and sign in with your keळ account when it asks.</p>
              <div className="cluster"><Link className="btn-ink" href="/org"><span>Go to your organization</span></Link></div>
            </div>
          )}
        </div>
      </section>
    </>
  );
}
