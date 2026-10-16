"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";

const LINKS = [
  { href: "/about", label: "About" },
  { href: "/search", label: "Search" },
  { href: "/problems", label: "Problems" },
  { href: "/docs", label: "Docs" },
  { href: "/setup", label: "Setup" },
];

/** Disclosure-based mobile nav — the desktop nav links hide below `md`, this takes over. */
export function MobileNav({ authSlot }: { authSlot: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    setOpen(false);
  }, []);

  return (
    <div className="md:hidden">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={open ? "Close menu" : "Open menu"}
        onClick={() => setOpen((v) => !v)}
        className="flex h-9 w-9 items-center justify-center rounded-md border border-ink/15 text-ink"
      >
        <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4" aria-hidden="true">
          {open ? (
            <path
              d="M5 5l10 10M15 5L5 15"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          ) : (
            <path
              d="M3 6h14M3 10h14M3 14h14"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          )}
        </svg>
      </button>

      {open ? (
        <div
          id={panelId}
          className="fixed inset-x-0 top-16 z-40 border-b border-ink/10 bg-paper px-6 py-6"
        >
          <nav aria-label="Main" className="flex flex-col gap-1">
            {LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="rounded-md px-2 py-3 text-base text-ink hover:bg-paper-dim"
              >
                {l.label}
              </Link>
            ))}
          </nav>
          <div className="mt-4 border-t border-ink/10 pt-4">{authSlot}</div>
        </div>
      ) : null}
    </div>
  );
}
