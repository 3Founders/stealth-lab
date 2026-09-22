"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import InteractiveLogo from "@/components/interactive-logo/InteractiveLogo";
import { getSupabase, supabaseConfigured } from "@/lib/supabase";
import { isSafeRedirectPath } from "@/lib/session";

type Mode = "signin" | "signup";
type Status = "idle" | "loading" | "check-email" | "error";

function destinationFrom(searchParams: URLSearchParams): string {
  const raw = searchParams.get("redirect");
  return isSafeRedirectPath(raw) ? raw : "/";
}

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.9c1.7-1.57 2.7-3.88 2.7-6.62z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.26c-.8.54-1.83.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.9v2.33A9 9 0 0 0 9 18z" />
      <path fill="#FBBC05" d="M3.95 10.7A5.4 5.4 0 0 1 3.67 9c0-.59.1-1.16.28-1.7V4.97H.9A9 9 0 0 0 0 9c0 1.45.35 2.83.9 4.03l3.05-2.33z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.46 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .9 4.97l3.05 2.33C4.66 5.17 6.65 3.58 9 3.58z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.5 7.5 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8 8 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

function SignInInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const configured = supabaseConfigured();

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");

  const destination = destinationFrom(searchParams);

  async function oauth(provider: "google" | "github") {
    const client = getSupabase();
    if (!client) return;
    setStatus("loading");
    const { error } = await client.auth.signInWithOAuth({
      provider,
      options: { redirectTo: `${window.location.origin}${destination}` },
    });
    if (error) {
      setStatus("error");
      setMessage("Could not start sign-in with that provider. Please try again.");
    }
    // On success the browser navigates away to the provider; nothing more to do here.
  }

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    const client = getSupabase();
    if (!client) return;
    setStatus("loading");
    setMessage("");

    if (mode === "signup") {
      if (password !== confirmPassword) {
        setStatus("error");
        setMessage("Those passwords don't match.");
        return;
      }
      const { data, error } = await client.auth.signUp({ email, password });
      if (error) {
        setStatus("error");
        setMessage(/already registered|already exists/i.test(error.message) ? "An account with that email already exists — sign in instead." : "Could not create an account with those details.");
        return;
      }
      if (!data.session) {
        setStatus("check-email");
        return;
      }
      router.push(destination);
      return;
    }

    const { error } = await client.auth.signInWithPassword({ email, password });
    if (error) {
      setStatus("error");
      setMessage("That email and password don't match an account.");
      return;
    }
    router.push(destination);
  }

  if (!configured) {
    return (
      <div className="signin">
        <p className="lead">Sign-in isn&rsquo;t connected in this build.</p>
        <p className="small dim">
          Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to this deployment&rsquo;s
          Supabase project and this page will work. No password is ever stored by this app itself — Supabase Auth owns
          credentials, OAuth, and email confirmation.
        </p>
      </div>
    );
  }

  if (status === "check-email") {
    return (
      <div className="signin">
        <p className="lead">Check your email to finish creating your keळ account.</p>
        <p className="small dim">We sent a confirmation link to {email}. Once you confirm, come back and sign in.</p>
      </div>
    );
  }

  return (
    <div className="signin">
      <h2 className="h3" style={{ marginBottom: 4 }}>{mode === "signin" ? "Sign in to keळ." : "Create your keळ account"}</h2>

      <div className="oauth-row">
        <button type="button" className="oauth-btn" onClick={() => oauth("google")} disabled={status === "loading"}>
          <GoogleIcon /> Continue with Google
        </button>
        <button type="button" className="oauth-btn" onClick={() => oauth("github")} disabled={status === "loading"}>
          <GitHubIcon /> Continue with GitHub
        </button>
      </div>

      <div className="divider">or continue with email</div>

      <form className="cform" style={{ gridColumn: "auto", gap: 16 }} onSubmit={submitPassword}>
        <div className="cfield">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
        </div>
        <div className="cfield">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8}
                 autoComplete={mode === "signup" ? "new-password" : "current-password"} />
        </div>
        {mode === "signup" && (
          <div className="cfield">
            <label htmlFor="confirm">Confirm password</label>
            <input id="confirm" type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required minLength={8} autoComplete="new-password" />
          </div>
        )}
        <div className="cform-submit">
          <button className="btn-ink" type="submit" disabled={status === "loading"}>
            <span>{status === "loading" ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}</span>
            <span className="sq" aria-hidden="true">→</span>
          </button>
          {status === "error" && <span className="small dim">{message}</span>}
        </div>
      </form>

      <p className="small dim">
        {mode === "signin" ? (
          <>New to keळ? <button type="button" className="link-btn" onClick={() => { setMode("signup"); setStatus("idle"); }}>Create account</button></>
        ) : (
          <>Already have an account? <button type="button" className="link-btn" onClick={() => { setMode("signin"); setStatus("idle"); }}>Sign in</button></>
        )}
      </p>

      <p className="small dim">Reading public knowledge doesn&rsquo;t require an account. Your own private library stays on your machine unless you explicitly publish from it.</p>
    </div>
  );
}

export default function SignIn() {
  return (
    <>
      {/* Stays "page-hero" so the heading sits at the same height as every
          other inner page's title. The logo's position is pinned
          separately: .hero-art (rendered by InteractiveLogo) computes its
          offset purely from the --hero-pad-top custom property, so setting
          that one variable to the homepage's own value -- without adopting
          the homepage's ".hero" padding for the heading too -- lands the
          logo at the exact same pixel spot and size as on "/", with no
          effect on where the heading text falls. */}
      <section className="page-hero frame grid" style={{ position: "relative", "--hero-pad-top": "clamp(140px, 16vw, 220px)" } as React.CSSProperties}>
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>SIGN IN</b></div>
        <h1 className="display">Pick up where your work left off.</h1>
        <InteractiveLogo />
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <Suspense fallback={<div className="signin"><p className="lead">Loading…</p></div>}>
          <SignInInner />
        </Suspense>
      </section>
    </>
  );
}
