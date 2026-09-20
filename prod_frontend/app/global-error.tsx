"use client";
import { useEffect } from "react";
import { captureError } from "@/lib/monitoring";

// Last-resort boundary (errors in the root layout). Reports to Sentry only when configured; shows a plain message either way.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { void captureError(error, { boundary: "global", digest: error.digest ?? "" }); }, [error]);
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "12vh 24px", maxWidth: 640, margin: "0 auto" }}>
        <h1>Something went wrong.</h1>
        <p>The page failed to load. It has been noted; you can try again.</p>
        <button onClick={reset}>Try again</button>
      </body>
    </html>
  );
}
