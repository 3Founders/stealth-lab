"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Settings was the old name of the profile page; keep any saved link working.
export default function AccountSettingsRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/account/profile"); }, [router]);
  return null;
}
