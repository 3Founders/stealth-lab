import "./globals.css"
import "./chat.css"
import "./desktop.css"
export const metadata = {
  title: "kell",
  description: "kell's own coding app — the cheapest model that can do it, the right outside tool when one helps.",
}
export const viewport = { width: "device-width", initialScale: 1 }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* a file, not inline text (the website learned that the hard way): sets data-theme before the page paints */}
        {/* first: the saved settings back into localStorage, which starts empty on every run (the app's port changes) */}
        <script src="/prefs-init.js" />
        <script src="/theme-init.js" />
      </head>
      <body>{children}</body>
    </html>
  )
}
