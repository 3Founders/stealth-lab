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
 * signed-in visitor on the home page is sent to /chat the first time in
 * a tab (the rooms are home once there is an account; a later Back to
 * home shows the landing page, and public/session-home.js does the same
 * before the first paint when the session is already in this browser). A
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
      // Signed in, the rooms are home the first time in this tab: the landing page is not shown. An explicit home link
      // (?home=1) always shows it, and the sessionStorage flag makes a later "Back to home" (or a refresh) show it too;
      // onboarding is checked on the page this lands on (/chat).
      if (pathname === "/") {
        let stay = false;
        try { stay = new URLSearchParams(window.location.search).get("home") === "1"; } catch { /* no URLSearchParams */ }
        let done = false;
        try { done = window.sessionStorage.getItem("kell:home:redirected") === "1"; } catch { /* storage blocked */ }
        if (stay) {
          try { window.sessionStorage.setItem("kell:home:redirected", "1"); } catch { /* storage blocked */ }
          return;
        }
        if (done) return;
        try { window.sessionStorage.setItem("kell:home:redirected", "1"); } catch { /* storage blocked */ }
        router.replace("/chat");
        return;
      }
      const profile = await getMyProfile();
      if (cancelled || profile.kind !== "ok") return;
      if (profile.data.onboarding_required) {
        // keep the query too: an invite link (/room/join?code=...) must still carry its code after onboarding
        router.replace(`/onboarding?redirect=${encodeURIComponent(pathname + window.location.search)}`);
      }
    })();
    return () => { cancelled = true; };
  }, [pathname, router]);

  return null;
}
