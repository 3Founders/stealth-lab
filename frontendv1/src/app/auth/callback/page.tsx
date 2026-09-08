"use client";

/**
 * OAuth / email-confirmation return leg. The Supabase browser client
 * (detectSessionInUrl + PKCE) exchanges the `?code=` for a session on load;
 * this page just waits for that to settle and forwards the user on.
 */
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { authReady, getAuth, onAuthChange } from "@/lib/auth";

export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get("error_description") || params.get("error");
    if (err) {
      setError(err);
      return;
    }
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      router.replace(getAuth() ? "/submit" : "/auth");
    };
    const off = onAuthChange(finish);
    authReady().then(() => setTimeout(finish, 300));
    return off;
  }, [router]);

  return (
    <div className="flex flex-col items-center pt-32 text-sm text-neutral-500">
      {error ? (
        <>
          <p className="text-red-700">Sign-in failed: {error}</p>
          <a href="/auth" className="mt-4 underline">
            Try again
          </a>
        </>
      ) : (
        <p>Completing sign-in…</p>
      )}
    </div>
  );
}
