import type { Metadata } from "next";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import DesktopInstall from "@/components/DesktopInstall";

export const metadata: Metadata = {
  title: "kell for Windows",
  description: "Install the kell desktop app on Windows with one PowerShell line.",
};

const does = [
  ["One line", "PowerShell downloads the newest installer, runs it for your account and opens kell."],
  ["No administrator prompt", "It installs per user, under %LOCALAPPDATA%\\Programs\\kell-desktop, like any other app you own."],
  ["One sign-in", "Sign in once; the same account runs your coding agent and your rooms."],
  ["Nothing else touched", "It adds kell to the Start menu and writes nothing outside its own folder and your kell data."],
];

export default function DesktopPage() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>KELL FOR WINDOWS</b></div>
        <h1 className="display"><AnimatedHeading lines={["One line.", "Then just work."]} /></h1>
        <p className="lead">
          The kell desktop app installs from one PowerShell line. It downloads the app, installs it for your account and
          opens it. No administrator prompt, no key to paste.
        </p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
        <div className="desktop-install">
          <DesktopInstall />
        </div>

        <div className="desktop-notes">
          <h2 className="h3">What it does</h2>
          {does.map(([t, d]) => (
            <p className="small dim" key={t}><b style={{ color: "var(--ink)" }}>{t}.</b> {d}</p>
          ))}
          <h2 className="h3" style={{ marginTop: 10 }}>Requirements</h2>
          <p className="small dim">Windows 10 or 11, 64-bit, with PowerShell. About 700 MB of disk. An internet connection for the download.</p>
          <p className="small dim">
            Prefer to read first? <Link href="/docs" style={{ textDecoration: "underline" }}>The docs</Link> cover setup,
            teams and billing. The app is Windows-only for now.
          </p>
        </div>
      </section>
    </>
  );
}
