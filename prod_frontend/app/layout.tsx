import type { Metadata } from "next";
import "@fontsource/barlow/300.css";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "./globals.css";
import Header from "@/components/Header";
import BackButton from "@/components/BackButton";
import OnboardingGate from "@/components/OnboardingGate";
import Footer from "@/components/Footer";
import SmoothScroll from "@/components/SmoothScroll";
import Telemetry from "@/components/Telemetry";

export const metadata: Metadata = {
  title: { default: "keळ: Routine work on cheaper models, checked first.", template: "%s · keळ" },
  description:
    "keळ sends everyday tasks to lower-cost models, checks each answer, and falls back to the frontier model when a check fails. Every attempt leaves a record you can audit.",
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
        <Telemetry />
        <OnboardingGate />
        <Header />
        <main id="main">
          <BackButton />
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
