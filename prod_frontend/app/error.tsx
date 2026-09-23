"use client";
import { useEffect } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import { captureError } from "@/lib/monitoring";

// Route-level error boundary: keeps the header/footer, reports to Sentry only when configured.
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { void captureError(error, { boundary: "route", digest: error.digest ?? "" }); }, [error]);
  return (
    <section className="page-hero frame grid">
      <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ERROR</b></div>
      <h1 className="display"><AnimatedHeading>Something went wrong</AnimatedHeading></h1>
      <p className="small dim" style={{ gridColumn: "1 / -1" }}>This page failed to render. You can try again or go back to the <a href="/" style={{ textDecoration: "underline" }}>homepage</a>.</p>
      <button className="btn-ink" style={{ gridColumn: "1 / -1", justifySelf: "start" }} onClick={reset}>Try again</button>
    </section>
  );
}
