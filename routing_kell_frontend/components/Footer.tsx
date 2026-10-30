import Link from "next/link";
import Image from "next/image";
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
  { h: "Account", items: [["Sign in", "/sign-in"], ["Connected apps", "/account/connections"]] },
  { h: "About", items: [["About keळ", "/#about"]] },
];

/** Outside addresses open in a new tab and never hand the new page a reference to this one. */
const isOutside = (href: string) => href.startsWith("http");

export default function Footer() {
  const admin = adminSignInUrl();
  const columns = [
    ...cols.map((c) => (c.h === "Account" && admin ? { ...c, items: [...c.items, ["Administrator sign in", admin]] } : c)),
    { h: "Company", items: companyLinks().map((l) => [l.label, l.href]) },
  ];
  return (
    <footer className="footer">
      <div className="frame grid">
        <div className="footer-mark">
          <span className="footer-logo"><Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} /></span>
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
          <span>Pre-release · v0.1</span>
        </div>
      </div>
    </footer>
  );
}
