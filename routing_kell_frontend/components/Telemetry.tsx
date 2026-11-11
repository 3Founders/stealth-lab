"use client";
// Loads website analytics (Plausible) and error monitoring (Sentry) when, and only when, they are configured and the
// visitor has not sent Do Not Track / Global Privacy Control. Renders nothing.
import { useEffect } from "react";
import { useReportWebVitals } from "next/web-vitals";
import { analyticsEnabled, PLAUSIBLE_DOMAIN, PLAUSIBLE_SRC, track, type PlausibleFn } from "@/lib/analytics";

const VITALS = new Set(["LCP", "CLS", "INP", "TTFB"]);

export default function Telemetry() {
  useEffect(() => {
    // Monitoring is an error path, not part of the first paint: load its chunk when the browser is idle.
    const start = () => { void import("@/lib/monitoring").then((m) => m.initMonitoring()); };
    if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(start, { timeout: 3000 });
    else window.setTimeout(start, 1);
    if (!analyticsEnabled() || document.querySelector("script[data-kel-analytics]")) return;
    // the standard Plausible queue stub, so track() calls made before the script finishes loading are not lost
    if (!window.plausible) {
      const q: unknown[] = [];
      const stub = ((...a: unknown[]) => { q.push(a); }) as unknown as PlausibleFn;
      stub.q = q;
      window.plausible = stub;
    }
    const s = document.createElement("script");
    s.defer = true;
    s.src = PLAUSIBLE_SRC;
    s.dataset.domain = PLAUSIBLE_DOMAIN;
    s.dataset.kelAnalytics = "1";
    document.head.appendChild(s);
  }, []);

  useReportWebVitals((m) => {
    if (VITALS.has(m.name)) track("web_vital", { name: m.name, rating: m.rating ?? "n/a", value: Math.round(m.name === "CLS" ? m.value * 1000 : m.value) });
  });
  return null;
}
