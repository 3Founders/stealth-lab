import type { Metadata, Viewport } from "next";
import "@fontsource/barlow/300.css";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "./globals.css";
import Header from "@/components/Header";
import BackButton from "@/components/BackButton";
import OnboardingGate from "@/components/OnboardingGate";
import PhotoPrompt from "@/components/PhotoPrompt";
import Footer from "@/components/Footer";
import SmoothScroll from "@/components/SmoothScroll";
import Telemetry from "@/components/Telemetry";

export const metadata: Metadata = {
  title: { default: "keळ: Cut LLM API bills by 50% without losing prompt cache retention.", template: "%s · keळ" },
  description:
    "keळ gives your coding agent a memory of your repo, reuses what's already been solved, and hands each task to the cheapest model that can do it. Nothing reaches your code until your own tests pass.",
  icons: { icon: "/kel-mark.png" },
};

// On Android the on-screen keyboard resizes the page (the Chat composer stays visible); iOS is handled by the Chat itself.
export const viewport: Viewport = { width: "device-width", initialScale: 1, interactiveWidget: "resizes-content" };

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
        <PhotoPrompt />
        <Header />
        <BackButton />
        <main id="main">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
