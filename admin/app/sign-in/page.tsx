"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Hero } from "@/components/Admin";
import { getSupabase, supabaseConfigured } from "@/lib/supabase";
import { isSafeRedirectPath } from "@/lib/session";

function Inner() {
  const router = useRouter();
  const params = useSearchParams();
  const raw = params.get("redirect");
  const destination = isSafeRedirectPath(raw) ? raw : "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const configured = supabaseConfigured();

  async function emailSignIn(e: React.FormEvent) {
    e.preventDefault();
    const client = getSupabase();
    if (!client) return;
    setBusy(true);
    setMessage("");
    const { error } = await client.auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) setMessage("Those credentials were not accepted.");
    else router.push(destination);
  }

  async function oauth(provider: "google" | "github") {
    const client = getSupabase();
    if (!client) return;
    await client.auth.signInWithOAuth({ provider, options: { redirectTo: `${window.location.origin}${destination}` } });
  }

  return (
    <>
      <Hero marker="ADMIN" title="Sign in" lead="Operators only. Access is decided by the backend from your account's scopes, not by this page." />
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        {!configured ? (
          <div className="empty" style={{ gridColumn: "1 / span 8" }}><b>Sign-in isn&rsquo;t configured.</b><p>Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code>.</p></div>
        ) : (
          <div className="signin">
            <div className="oauth-row">
              <button type="button" className="oauth-btn" onClick={() => oauth("google")}>Continue with Google</button>
              <button type="button" className="oauth-btn" onClick={() => oauth("github")}>Continue with GitHub</button>
            </div>
            <form onSubmit={emailSignIn} className="cform" style={{ gridColumn: "auto" }}>
              <div className="cfield"><label htmlFor="email">Email</label><input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
              <div className="cfield"><label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
              <div><button type="submit" className="btn-ink" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button></div>
              {message && <p className="small" role="alert">{message}</p>}
            </form>
          </div>
        )}
      </section>
    </>
  );
}

export default function SignInPage() {
  return (<Suspense fallback={null}><Inner /></Suspense>);
}
