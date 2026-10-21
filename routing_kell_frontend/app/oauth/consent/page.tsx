"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import { SCOPE_TEXT, decide, isLocalRedirect, loadConsent, type ConsentState } from "@/lib/oauth-consent";

// Supabase's OAuth 2.1 server sends MCP clients' sign-ins here (the
// "authorization path" in the Supabase dashboard). See lib/oauth-consent.ts.

function ConsentInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const authorizationId = searchParams.get("authorization_id");
  const [state, setState] = useState<ConsentState | { kind: "loading" }>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    loadConsent(authorizationId).then((s) => {
      if (!live) return;
      if (s.kind === "sign-in") router.replace(s.href);
      else if (s.kind === "redirect") window.location.assign(s.url);
      else setState(s);
    });
    return () => { live = false; };
  }, [authorizationId, router]);

  async function answer(approve: boolean) {
    if (state.kind !== "ask") return;
    setBusy(true);
    setFailed(false);
    const next = await decide(state.authorizationId, approve);
    if (next) {
      window.location.assign(next);
      return;
    }
    setBusy(false);
    setFailed(true);
  }

  if (state.kind === "loading") return <div className="signin"><p className="lead">Loading…</p></div>;
  if (state.kind === "not-configured") {
    return <div className="signin"><p className="lead">Sign-in isn&rsquo;t connected in this build.</p></div>;
  }
  if (state.kind === "missing-id") {
    return (
      <div className="signin">
        <p className="lead">This page is opened by an app asking to connect to keळ.</p>
        <p className="small dim">Start the connection from your app (for example Claude, Cursor or VS Code).</p>
      </div>
    );
  }
  if (state.kind === "error") return <div className="signin"><p className="lead">{state.message}</p></div>;
  if (state.kind !== "ask") return null;

  return (
    <div className="signin">
      <h2 className="h3" style={{ marginBottom: 4 }}>
        <b>{state.clientName}</b> wants to use keळ as you
      </h2>
      {state.email && <p className="small dim">Signed in as {state.email}</p>}

      <p>If you allow it, this app can:</p>
      <ul>
        <li>Find proven ways to do tasks, including your private ones</li>
        <li>Save what it learns and submit ways under your account</li>
        {state.scopes.filter((s) => SCOPE_TEXT[s]).map((s) => <li key={s}>{SCOPE_TEXT[s]}</li>)}
      </ul>
      <p className="small dim">
        It will return to <code>{state.redirectUri}</code>
        {state.clientUri && <> ({state.clientUri})</>}. You can remove its access at any time under <a href="/account/connections">Account → Connected apps</a>.
      </p>

      {state.unnamed && (
        <p className="small dim">
          This app didn&rsquo;t say what it is. Anyone can register an app, so only allow it if you just started
          this connection yourself.
        </p>
      )}
      {isLocalRedirect(state.redirectUri) && (
        <p className="small dim">
          This sends the app back to a program on <b>this computer</b>. Only allow it if you just started the
          connection yourself from {state.clientName}.
        </p>
      )}

      <div className="cform-submit">
        <button className="btn-ink" type="button" onClick={() => answer(true)} disabled={busy}>
          <span>{busy ? "Please wait…" : "Allow"}</span>
          <span className="sq" aria-hidden="true">→</span>
        </button>
        <button type="button" className="link-btn" onClick={() => answer(false)} disabled={busy}>Deny</button>
        {failed && <span className="small dim">That didn&rsquo;t go through. Please try again.</span>}
      </div>
    </div>
  );
}

export default function OAuthConsent() {
  return (
    <div className="frame">
      <section className="page-hero grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONNECT AN APP</b></div>
        <h1 className="display"><AnimatedHeading>Allow access to keळ</AnimatedHeading></h1>
      </section>
      <section className="grid" style={{ paddingBottom: 120 }}>
        <Suspense fallback={<div className="signin"><p className="lead">Loading…</p></div>}>
          <ConsentInner />
        </Suspense>
      </section>
    </div>
  );
}
