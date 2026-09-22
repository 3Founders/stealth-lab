"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getMyProfile } from "@/lib/kel-api";
import { getSession } from "@/lib/session";

// Routes onboarding itself must never redirect away from, and routes that
// don't need a completed profile to render (sign-in, legal, public pages
// generally just don't call this at all -- but keeping this list small and
// explicit is safer than trying to enumerate every public route).
const EXEMPT_PREFIXES = ["/onboarding", "/sign-in"];

/**
 * V1 identity spec §11: no speculative is_new_user JWT claim, no
 * middleware auth rewrite, no new cookie infrastructure -- just the
 * existing authenticated profile API, checked once per navigation. A
 * signed-in visitor whose profile isn't onboarding_complete yet is sent to
 * /onboarding; everyone else is left alone.
 */
export default function OnboardingGate() {
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (EXEMPT_PREFIXES.some((p) => pathname.startsWith(p))) return;
    let cancelled = false;
    (async () => {
      const session = await getSession();
      if (!session || cancelled) return;
      const profile = await getMyProfile();
      if (cancelled || profile.kind !== "ok") return;
      if (profile.data.onboarding_required) {
        router.replace(`/onboarding?redirect=${encodeURIComponent(pathname)}`);
      }
    })();
    return () => { cancelled = true; };
  }, [pathname, router]);

  return null;
}
