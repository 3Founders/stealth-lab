import "./globals.css"
import "./chat-core.css"
export const metadata = {
  title: "kell",
  description: "kell's own coding app — the cheapest model that can do it, the right outside tool when one helps.",
}
export const viewport = { width: "device-width", initialScale: 1 }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
