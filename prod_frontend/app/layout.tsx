import type { Metadata } from "next";
import "@fontsource/barlow/300.css";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "./globals.css";
import Header from "@/components/Header";
import Footer from "@/components/Footer";
import SmoothScroll from "@/components/SmoothScroll";

export const metadata: Metadata = {
  title: { default: "keळ — Remember how things actually get done", template: "%s — keळ" },
  description:
    "keळ finds known ways to accomplish real-world goals, executes them, verifies what happened, and turns experience into reusable knowledge.",
  icons: { icon: "/kel-mark.png" },
};

// Motion is opt-in: only pages whose visitor has not asked for reduced motion get the reveal classes.
const motionGate = `try{if(!matchMedia('(prefers-reduced-motion: reduce)').matches)document.documentElement.classList.add('motion')}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: motionGate }} />
      </head>
      <body>
        <a className="skip" href="#main">Skip to content</a>
        <div className="gridlines" aria-hidden="true">
          <div className="frame"><i /><i /><i /><i /><i /></div>
        </div>
        <SmoothScroll />
        <Header />
        <main id="main">{children}</main>
        <Footer />
      </body>
    </html>
  );
}
