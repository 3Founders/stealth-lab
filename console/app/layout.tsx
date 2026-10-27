import type { Metadata } from "next";
import "@fontsource/barlow/300.css";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "keळ console",
  description: "Install kell into your tools, keep your model keys, and try a model, from a helper on your own machine.",
  icons: { icon: "/kel-mark.png" },
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <a className="skip" href="#main">Skip to content</a>
        <div className="gridlines" aria-hidden="true">
          <div className="frame"><i /><i /><i /><i /><i /></div>
        </div>
        <div className="nav-wrap">
          <div className="frame">
            <header className="nav">
              <span className="logo" aria-label="keळ console">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/kel-wordmark.png" alt="keळ" width={800} height={440} style={{ height: 54, width: "auto" }} />
              </span>
              <span className="caption dim">Console</span>
            </header>
          </div>
        </div>
        <main id="main">{children}</main>
        <footer className="console-foot">
          <div className="frame">
            <span>keळ console. Runs against a helper on this machine.</span>
            <span>Pre-release. A streamed reply here is not checked or verified.</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
