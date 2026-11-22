"use client"
// /way?id=… — one Way as its own screen in the desktop. Client-only: the record is per-account, and the app is
// a local Next server, so there is nothing to pre-render. The id comes from the query string because the page
// is opened from a Way card, a chat link, or a copied link — all of which carry the id, none of which own it.
import { useEffect, useState } from "react"
import WayPage from "@/components/WayPage"
import "./desktop-way.css"

export default function Page() {
  const [id, setId] = useState<string | null>(null)
  useEffect(() => {
    setId(new URLSearchParams(window.location.search).get("id") || "")
  }, [])

  if (id === null) return <p className="dkw-muted" role="status">Loading the Way…</p>
  if (!id) {
    return (
      <section className="dkw">
        <p>No Way named in the link. Open one from your submissions or a Way card in the chat.</p>
        <p className="dkw-small dkw-muted">
          <a href="/">Back to the chat</a>
        </p>
      </section>
    )
  }
  return <WayPage id={id} />
}
