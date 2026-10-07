import type { Metadata } from "next";
import "@fontsource/barlow/300.css";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "./globals.css";
import AdminHeader from "@/components/AdminHeader";
import { OrgProvider } from "@/components/OrgContext";

export const metadata: Metadata = {
  title: { default: "keळ admin", template: "%s · keळ admin" },
  description: "Operations console for keळ.",
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
        <OrgProvider>
          <AdminHeader />
          <main id="main">{children}</main>
        </OrgProvider>
      </body>
    </html>
  );
}
