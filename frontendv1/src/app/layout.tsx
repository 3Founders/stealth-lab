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
              <NavLink href="/procedures" label="Procedures" />
              <NavLink href="/tasks" label="Tasks" />
              <NavLink href="/problems" label="Problems" />
              <Link
                href="/auth"
                className="rounded-md border border-neutral-200 px-3 py-1.5 text-sm text-neutral-700 transition-colors hover:bg-neutral-50"
              >
                Sign in
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 pb-24">{children}</main>
        <WebMcpProvider />
      </body>
    </html>
  );
}
