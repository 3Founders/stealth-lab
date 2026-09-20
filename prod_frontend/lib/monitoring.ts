// Frontend error monitoring. OFF unless NEXT_PUBLIC_SENTRY_DSN is set at build time; the SDK is only downloaded when it is.
//
// What is sent: the error, its stack, browser/OS, and the page path WITHOUT query string or hash. No IP-derived identity is
// requested (sendDefaultPii=false), no user object, no request bodies, no cookies, no session replay, no performance tracing.
// Do Not Track / Global Privacy Control switch it off entirely. Update docs/legal/COOKIES_AND_TRACKING.md and SUBPROCESSORS.md
// if any of this changes.

import { privacySignalOn } from "./analytics";

export const SENTRY_DSN = process.env.NEXT_PUBLIC_SENTRY_DSN?.trim() || "";
const ENVIRONMENT = process.env.NEXT_PUBLIC_KEL_ENV?.trim() || process.env.NODE_ENV || "production";
const RELEASE = process.env.NEXT_PUBLIC_KEL_RELEASE?.trim() || undefined;

type SentryModule = typeof import("@sentry/browser");
let loading: Promise<SentryModule | null> | null = null;

function scrubUrl(u?: string): string | undefined {
  if (!u) return u;
  try {
    const url = new URL(u, "http://x");
    return u.startsWith("http") ? `${url.origin}${url.pathname}` : url.pathname;
  } catch {
    return u.split(/[?#]/)[0];
  }
}

export function monitoringEnabled(): boolean {
  return Boolean(SENTRY_DSN) && !privacySignalOn();
}

export function initMonitoring(): Promise<SentryModule | null> {
  if (!monitoringEnabled() || typeof window === "undefined") return Promise.resolve(null);
  if (!loading) {
    loading = import("@sentry/browser")
      .then((Sentry) => {
        Sentry.init({
          dsn: SENTRY_DSN,
          environment: ENVIRONMENT,
          release: RELEASE,
          sendDefaultPii: false,
          tracesSampleRate: 0,
          replaysSessionSampleRate: 0,
          replaysOnErrorSampleRate: 0,
          maxBreadcrumbs: 20,
          beforeBreadcrumb: (b) => (b.category === "console" ? null : b),        // console output can contain anything
          beforeSend(event) {
            if (event.request) {
              event.request = { url: scrubUrl(event.request.url) };              // drops query, headers, cookies, body
            }
            delete event.user;
            return event;
          },
        });
        return Sentry;
      })
      .catch(() => null);
  }
  return loading;
}

export async function captureError(error: unknown, context?: Record<string, string>): Promise<void> {
  const Sentry = await initMonitoring();
  if (!Sentry) return;
  try {
    Sentry.withScope((scope) => {
      for (const [k, v] of Object.entries(context ?? {})) scope.setTag(k, v.slice(0, 60));
      Sentry.captureException(error);
    });
  } catch {
    /* monitoring must never break the page */
  }
}
