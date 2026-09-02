"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  beginOidcSignIn,
  completeOidcSignInIfPending,
  getAuth,
  isOidcConfigured,
  signInAsViewer,
  signOut,
} from "@/lib/auth";

export default function AuthPage() {
  const [auth, setAuthState] = useState<ReturnType<typeof getAuth>>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [viewerId, setViewerId] = useState("");
  const oidcReady = isOidcConfigured();

  useEffect(() => {
    setAuthState(getAuth());
    // Return leg of the OIDC redirect: /auth?code=...&state=...
    completeOidcSignInIfPending()
      .then((signedIn) => {
        if (signedIn) setAuthState(signedIn);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err))
      );
  }, []);

  async function handleOidc() {
    setError(null);
    setBusy(true);
    try {
      await beginOidcSignIn(); // redirects to the provider
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-center pt-32">
      <h1 className="text-xl font-semibold tracking-tight">Stealth Lab</h1>
      <p className="mt-3 max-w-sm text-center text-sm text-neutral-500">
        Sign in to contribute, save repositories, and build your personal
        procedural memory.
      </p>

      {auth ? (
        <div className="mt-8 flex flex-col items-center gap-3 text-sm">
          <p className="text-neutral-700">
            Signed in{" "}
            <span className="font-medium">
              {auth.name || auth.email || auth.token}
            </span>{" "}
            <span className="text-xs text-neutral-400">
              ({auth.mode === "oidc" ? "verified" : "dev viewer identity"})
            </span>
          </p>
          <button
            type="button"
            onClick={() => {
              signOut();
              setAuthState(null);
            }}
            className="rounded-lg border border-neutral-200 bg-white px-5 py-2.5 text-sm text-neutral-700 hover:bg-neutral-50"
          >
            Sign out
          </button>
        </div>
      ) : (
        <>
          {oidcReady ? (
            <button
              type="button"
              onClick={handleOidc}
              disabled={busy}
              className="mt-8 rounded-lg bg-neutral-900 px-5 py-2.5 text-sm font-medium text-neutral-50 hover:bg-neutral-800 disabled:opacity-60"
            >
              {busy ? "Redirecting…" : "Continue with Google"}
            </button>
          ) : (
            <div className="mt-8 flex w-full max-w-xs flex-col gap-3">
              <label
                htmlFor="viewer-id"
                className="text-xs text-neutral-500"
              >
                Developer sign-in (public posture — unverified identity sent
                as X-Viewer-Id)
              </label>
              <input
                id="viewer-id"
                value={viewerId}
                onChange={(e) => setViewerId(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && viewerId.trim()) {
                    setAuthState(signInAsViewer(viewerId));
                  }
                }}
                placeholder="your-name"
                className="h-9 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
              />
              <button
                type="button"
                disabled={!viewerId.trim()}
                onClick={() => setAuthState(signInAsViewer(viewerId))}
                className="rounded-lg bg-neutral-900 px-5 py-2.5 text-sm font-medium text-neutral-50 hover:bg-neutral-800 disabled:opacity-50"
              >
                Continue
              </button>
            </div>
          )}

          {error ? (
            <p className="mt-4 max-w-xs rounded-lg bg-red-50 px-4 py-3 text-xs text-red-700">
              {error}
            </p>
          ) : null}

          {!oidcReady ? (
            <p className="mt-6 max-w-xs text-center text-xs text-neutral-400">
              Real sign-in activates when{" "}
              <code className="font-mono">NEXT_PUBLIC_OIDC_AUTHORITY</code> and{" "}
              <code className="font-mono">NEXT_PUBLIC_OIDC_CLIENT_ID</code> are
              set and the backend has matching{" "}
              <code className="font-mono">OIDC_ISSUER</code>/
              <code className="font-mono">OIDC_AUDIENCE</code>.
            </p>
          ) : null}
        </>
      )}

      <Link href="/" className="mt-8 text-xs text-neutral-500 underline">
        Back to search
      </Link>
    </div>
  );
}
