"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  getAuth,
  isSupabaseConfigured,
  onAuthChange,
  signInAsViewer,
  signInWithGoogle,
  signInWithPassword,
  signOut,
  signUpWithPassword,
} from "@/lib/auth";

type Mode = "signin" | "signup";

export default function AuthPage() {
  const [auth, setAuth] = useState<ReturnType<typeof getAuth>>(null);
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [viewerId, setViewerId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const supabaseReady = isSupabaseConfigured();

  useEffect(() => {
    setAuth(getAuth());
    return onAuthChange(() => setAuth(getAuth()));
  }, []);

  async function withBusy(fn: () => Promise<void>) {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const submitPassword = () =>
    withBusy(async () => {
      if (mode === "signup") {
        const { needsConfirmation } = await signUpWithPassword(email, password);
        if (needsConfirmation) {
          setNotice(
            "Check your email for a confirmation link, then sign in."
          );
          setMode("signin");
          return;
        }
      } else {
        await signInWithPassword(email, password);
      }
    });

  return (
    <div className="flex flex-col items-center pt-24">
      <h1 className="text-xl font-semibold tracking-tight">Stealth Lab</h1>
      <p className="mt-3 max-w-sm text-center text-sm text-neutral-500">
        Sign in to contribute procedures, connect repositories, and build your
        personal procedural memory.
      </p>

      {auth ? (
        <div className="mt-8 flex flex-col items-center gap-3 text-sm">
          <p className="text-neutral-700">
            Signed in{" "}
            <span className="font-medium">
              {auth.name || auth.email || auth.token}
            </span>{" "}
            <span className="text-xs text-neutral-400">
              ({auth.mode === "supabase" ? "verified" : "dev viewer identity"})
            </span>
          </p>
          <div className="flex gap-3">
            <Link
              href="/submit"
              className="rounded-lg bg-neutral-900 px-5 py-2.5 text-sm font-medium text-neutral-50 hover:bg-neutral-800"
            >
              Add a procedure
            </Link>
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-lg border border-neutral-200 bg-white px-5 py-2.5 text-sm text-neutral-700 hover:bg-neutral-50"
            >
              Sign out
            </button>
          </div>
        </div>
      ) : supabaseReady ? (
        <div className="mt-8 flex w-full max-w-xs flex-col gap-3">
          <button
            type="button"
            onClick={() => void withBusy(signInWithGoogle)}
            disabled={busy}
            className="rounded-lg border border-neutral-200 bg-white px-5 py-2.5 text-sm font-medium text-neutral-800 hover:bg-neutral-50 disabled:opacity-60"
          >
            Continue with Google
          </button>

          <div className="my-1 flex items-center gap-3 text-xs text-neutral-400">
            <span className="h-px flex-1 bg-neutral-200" />
            or
            <span className="h-px flex-1 bg-neutral-200" />
          </div>

          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="h-9 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <input
            type="password"
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && email && password) void submitPassword();
            }}
            placeholder="Password"
            className="h-9 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <button
            type="button"
            disabled={busy || !email || !password}
            onClick={() => void submitPassword()}
            className="rounded-lg bg-neutral-900 px-5 py-2.5 text-sm font-medium text-neutral-50 hover:bg-neutral-800 disabled:opacity-50"
          >
            {busy
              ? "…"
              : mode === "signup"
                ? "Create account"
                : "Sign in"}
          </button>
          <button
            type="button"
            onClick={() => {
              setMode(mode === "signup" ? "signin" : "signup");
              setError(null);
              setNotice(null);
            }}
            className="text-xs text-neutral-500 underline"
          >
            {mode === "signup"
              ? "Already have an account? Sign in"
              : "New here? Create an account"}
          </button>
        </div>
      ) : (
        <div className="mt-8 flex w-full max-w-xs flex-col gap-3">
          <label htmlFor="viewer-id" className="text-xs text-neutral-500">
            Developer sign-in (public posture — unverified identity sent as
            X-Viewer-Id)
          </label>
          <input
            id="viewer-id"
            value={viewerId}
            onChange={(e) => setViewerId(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && viewerId.trim())
                signInAsViewer(viewerId);
            }}
            placeholder="your-name"
            className="h-9 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <button
            type="button"
            disabled={!viewerId.trim()}
            onClick={() => signInAsViewer(viewerId)}
            className="rounded-lg bg-neutral-900 px-5 py-2.5 text-sm font-medium text-neutral-50 hover:bg-neutral-800 disabled:opacity-50"
          >
            Continue
          </button>
          <p className="mt-2 text-center text-xs text-neutral-400">
            Real sign-in activates when{" "}
            <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> are
            set.
          </p>
        </div>
      )}

      {notice ? (
        <p className="mt-4 max-w-xs rounded-lg bg-emerald-50 px-4 py-3 text-xs text-emerald-700">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="mt-4 max-w-xs rounded-lg bg-red-50 px-4 py-3 text-xs text-red-700">
          {error}
        </p>
      ) : null}

      <Link href="/" className="mt-8 text-xs text-neutral-500 underline">
        Back to search
      </Link>
    </div>
  );
}
