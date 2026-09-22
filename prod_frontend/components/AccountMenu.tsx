"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Avatar from "@/components/Avatar";
import { signOut } from "@/lib/session";

/**
 * Header account menu (V1 identity spec §20). The avatar never navigates
 * by itself — it only opens this menu; "View profile" is one of the menu
 * items, not the trigger's own click target.
 */
export default function AccountMenu({ username }: { username: string }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const firstItemRef = useRef<HTMLAnchorElement>(null);
  const router = useRouter();

  useEffect(() => {
    if (!open) return;
    firstItemRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) setOpen(false);
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
        onClick={() => setOpen((o) => !o)}
      >
        <Avatar username={username} size={26} />
        <span className="account-name">{username}</span>
        <span className="caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="account-menu" role="menu" aria-label="Account" ref={menuRef}>
          <Link ref={firstItemRef} href={`/u/${encodeURIComponent(username)}`} role="menuitem" onClick={() => setOpen(false)}>
            View profile
          </Link>
          <Link href="/account/credits" role="menuitem" onClick={() => setOpen(false)}>Credits &amp; Standing</Link>
          <Link href="/account/settings" role="menuitem" onClick={() => setOpen(false)}>Settings</Link>
          <hr />
          <button type="button" role="menuitem" onClick={handleSignOut}>Sign out</button>
        </div>
      )}
    </div>
  );
}
