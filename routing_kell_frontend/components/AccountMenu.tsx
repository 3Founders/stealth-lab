"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Avatar from "@/components/Avatar";
import DevicePhoto, { useDevicePhoto } from "@/components/DevicePhoto";
import { getSession, signOut } from "@/lib/session";
import { PROJECT_SYNC_ENABLED } from "@/lib/features";
import { orgAdminEnabled } from "@/lib/org-admin";

/**
 * Header account menu (V1 identity spec §20). The avatar never navigates
 * by itself — it only opens this menu; "View profile" is one of the menu
 * items, not the trigger's own click target.
 */
export default function AccountMenu({ username }: { username: string }) {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Close with the exit animation (toggle, Escape, click outside). Choosing an item still closes at once: the page is about to change. */
  function closeSoft() {
    if (closeTimer.current) return;
    setClosing(true);
    closeTimer.current = setTimeout(() => { closeTimer.current = null; setClosing(false); setOpen(false); }, 140);
  }
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const firstItemRef = useRef<HTMLAnchorElement>(null);
  const router = useRouter();
  const [subject, setSubject] = useState<string | null>(null);
  useEffect(() => { let live = true; getSession().then((x) => { if (live) setSubject(x?.subject ?? null); }); return () => { live = false; }; }, []);
  const devicePhoto = useDevicePhoto(subject);

  useEffect(() => {
    if (!open) return;
    firstItemRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeSoft();
        triggerRef.current?.focus();
      }
    };
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) closeSoft();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  async function handleSignOut() {
    setOpen(false);
    await signOut();
    router.push("/");
    router.refresh();
  }

  return (
    <div className="account-wrap">
      <button
        ref={triggerRef}
        type="button"
        className="account-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => { if (open) closeSoft(); else setOpen(true); }}
      >
        {devicePhoto ? <DevicePhoto url={devicePhoto} size={26} /> : <Avatar username={username} size={26} authenticated />}
        <span className="account-name">{username}</span>
        <span className="caret" data-open={open && !closing ? "true" : undefined} aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="account-menu" data-closing={closing ? "true" : undefined} role="menu" aria-label="Account" ref={menuRef}>
          {PROJECT_SYNC_ENABLED && <Link href="/account" role="menuitem" onClick={() => setOpen(false)}>Projects</Link>}
          <Link ref={firstItemRef} href="/account/profile" role="menuitem" onClick={() => setOpen(false)}>Profile</Link>
          <Link href="/account/connections" role="menuitem" onClick={() => setOpen(false)}>Connected apps</Link>
          <Link href="/chat" role="menuitem" onClick={() => setOpen(false)}>Chat</Link>
          {orgAdminEnabled() && <Link href="/org" role="menuitem" onClick={() => setOpen(false)}>Organization</Link>}
          <Link href="/account/models" role="menuitem" onClick={() => setOpen(false)}>Models</Link>
          <hr />
          <button type="button" role="menuitem" onClick={handleSignOut}>Sign out</button>
        </div>
      )}
    </div>
  );
}
