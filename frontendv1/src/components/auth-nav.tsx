"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getAuth, onAuthChange, signOut } from "@/lib/auth";

const pillClass =
  "rounded-md border border-neutral-200 px-3 py-1.5 text-sm text-neutral-700 transition-colors hover:bg-neutral-50";

/**
 * Header identity slot. Server render and the first client render both show
 * "Sign in" (auth state lives in the browser only), then this swaps to the
 * signed-in view once the Supabase session resolves or on any auth change.
 */
export function AuthNav() {
  const [auth, setAuth] = useState<ReturnType<typeof getAuth>>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setAuth(getAuth());
    return onAuthChange(() => setAuth(getAuth()));
  }, []);

  if (!mounted || !auth) {
    return (
      <Link href="/auth" className={pillClass}>
        Sign in
      </Link>
    );
  }

  const label =
    auth.name ||
    auth.email ||
    (auth.mode === "viewer" ? auth.token : "Account");

  return (
    <div className="flex items-center gap-3">
      <Link
        href="/me"
        className="max-w-[12rem] truncate text-sm text-neutral-600 transition-colors hover:text-neutral-900"
        title={label}
      >
        {label}
      </Link>
      <button type="button" onClick={() => void signOut()} className={pillClass}>
        Sign out
      </button>
    </div>
  );
}
