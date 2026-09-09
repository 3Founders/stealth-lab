import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Stealth Lab",
  description: "Find the best way to do something.",
};

function NavLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="text-sm text-neutral-500 transition-colors hover:text-neutral-900"
    >
      {label}
    </Link>
  );
}

import { AuthNav } from "@/components/auth-nav";
import { WebMcpProvider } from "@/components/webmcp-provider";

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen font-sans">
        <header className="sticky top-0 z-50 border-b border-black/5 bg-[--background]/75 backdrop-blur-md">
          <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
            <Link href="/" className="text-sm font-semibold tracking-tight">
              Stealth Lab
            </Link>
            <nav aria-label="Main" className="flex items-center gap-6">
              <NavLink href="/search" label="Search" />
              <NavLink href="/problems" label="Problems" />
              <NavLink href="/leaderboard" label="Leaderboard" />
              <NavLink href="/people" label="People" />
              <AuthNav />
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 pb-24">{children}</main>
        <footer className="border-t border-black/5 py-8">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-2 px-6 text-xs text-neutral-400">
            <Link href="/legal/terms" className="hover:text-neutral-600">
              Terms
            </Link>
            <Link href="/legal/privacy" className="hover:text-neutral-600">
              Privacy
            </Link>
            <Link href="/legal/acceptable-use" className="hover:text-neutral-600">
              Acceptable Use
            </Link>
            <Link href="/legal/global-commons" className="hover:text-neutral-600">
              Global Commons Terms
            </Link>
            <Link href="/legal/security" className="hover:text-neutral-600">
              Security
            </Link>
            <Link href="/legal" className="hover:text-neutral-600">
              All legal documents
            </Link>
          </div>
        </footer>
        <WebMcpProvider />
      </body>
    </html>
  );
}
