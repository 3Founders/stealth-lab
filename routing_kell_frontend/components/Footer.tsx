"use client";
import Wordmark from "@/components/Wordmark";
import { useEffect, useState } from "react";
import { getSession, onSessionChange } from "@/lib/session";
import Link from "next/link";
import { adminSignInUrl } from "@/lib/admin-link";
import { companyLinks } from "@/lib/social-links";

// Only routes and anchors that exist in this build.
const cols: Array<{ h: string; items: string[][] }> = [
  { h: "Product", items: [["See it work", "/#action"], ["Docs", "/docs"], ["Pricing", "/pricing"]] },
  {
    h: "How to use",
    items: [
      ["For teams", "/docs#teams"],
    ],
  },
  { h: "About", items: [["About keळ", "/#about"]] },
];

/** Outside addresses open in a new tab and never hand the new page a reference to this one. */
const isOutside = (href: string) => href.startsWith("http");

export default function Footer() {
  const admin = adminSignInUrl();
  // The Account column offers what fits: someone signed in sees their profile, not "Sign in".
  const [signedIn, setSignedIn] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    let live = true;
    void getSession().then((x) => { if (live) setSignedIn(Boolean(x)); });
    const off = onSessionChange((x) => { if (live) setSignedIn(Boolean(x)); });
    return () => { live = false; off(); };
  }, []);
  const account: string[][] = signedIn
    ? [["Profile", "/account/profile"], ["Plan and billing", "/account/billing"], ["Connected apps", "/account/connections"]]
    : signedIn === false ? [["Sign in", "/sign-in"], ["Connected apps", "/account/connections"]] : [["Connected apps", "/account/connections"]];
  const columns = [
    ...cols.slice(0, 2),
    { h: "Account", items: admin && signedIn === false ? [...account, ["Administrator sign in", admin]] : account },
    ...cols.slice(2),
    { h: "Company", items: companyLinks().map((l) => [l.label, l.href]) },
  ];
  return (
    <footer className="footer">
      <div className="frame grid">
        <div className="footer-mark">
          <span className="footer-logo"><Wordmark /></span>
          <p>Recall, reuse, route, reduce.</p>
        </div>
        {columns.map((c) => (
          <nav key={c.h} className="footer-col" aria-label={c.h}>
            <h4>{c.h}</h4>
            <ul>
              {c.items.map(([label, href]) => (
                <li key={label}>
                  {isOutside(href) ? <a href={href} target="_blank" rel="noopener noreferrer">{label}</a> : <Link href={href}>{label}</Link>}
                </li>
              ))}
            </ul>
          </nav>
        ))}
        <nav className="footer-legal" aria-label="Legal">
          <span>Legal</span>
          <Link href="/docs/legal/terms">Terms</Link>
          <Link href="/docs/legal/privacy">Privacy</Link>
          <Link href="/legal/refunds">Refunds and cancellation</Link>
          <Link href="/contact">Contact</Link>
        </nav>
        <div className="footer-base">
          <span>© {new Date().getFullYear()} keळ</span>
        </div>
      </div>
    </footer>
  );
}
