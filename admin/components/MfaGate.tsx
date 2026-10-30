"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { mfaRequired, mfaState, nextStep } from "@/lib/mfa";
import { getSession } from "@/lib/session";

const OPEN = ["/sign-in", "/security"];

/**
 * When the deployment requires a second factor (NEXT_PUBLIC_ADMIN_REQUIRE_MFA=on), a signed-in person whose session has not passed
 * it is sent to /security first. Signed-out visitors are left alone (the pages show their own sign-in notice). This only steers the
 * page: the backend refuses billing requests without the second factor on its own.
 */
export default function MfaGate() {
  const path = usePathname();
  const router = useRouter();
  useEffect(() => {
    if (!mfaRequired() || OPEN.some((p) => path.startsWith(p))) return;
    let live = true;
    (async () => {
      const session = await getSession();
      if (!session || !live) return;
      const s = await mfaState();
      if (live && s.configured && nextStep(s)) router.replace(`/security?redirect=${encodeURIComponent(path)}`);
    })();
    return () => { live = false; };
  }, [path, router]);
  return null;
}
