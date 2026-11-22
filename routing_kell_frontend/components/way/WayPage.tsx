"use client";

/**
 * One Way (/way/[id]): everything a sceptical reader needs before they run it.
 *
 * The order is the page's whole argument. Identity first, then OUTPUT — before the description — because a
 * reader who does not believe it works should not be asked to read how. Then the price for one output, what it
 * does, the decomposition, history, provenance and reviews. A right-hand rail carries related Ways and honest
 * links out; reviews are opinion and never feed the capability numbers.
 * See docs/KELL.md §4 for why each section sits where it does.
 *
 * Two rules this file enforces in code rather than in a style guide:
 *
 *  1. Never a bare percentage. Every rate is rendered with its interval through <Rate/>, which REFUSES to
 *     draw a number without one. A Way with no evidence renders an honest "not measured", never "0%" and
 *     never "100%".
 *  2. The version is always visible next to the name. A Way's identity is (id, version) — a label alone can
 *     lie (see docs/KELL.md §2.4: 5.5% of skill edits move no version), so the version is never hidden
 *     behind a tooltip.
 *
 * Data comes from the hosted submissions API (backend/app/mcp_server/submission_routes.py), the same object
 * the desktop and the rooms show. Nothing here invents a field: a section with no backing data says so.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getAccessToken } from "@/lib/session";
import { MCP_ORIGIN } from "@/lib/connector";
import { rateFor, priceLabel } from "@/lib/way-rate";
import { InputsForm, QuestionCard, type WayInputDef, type WayQuestion } from "@/components/way/WayRunPanel";
import "@/app/way/way.css";
import "@/app/way/run-panel.css";

type Step = {
  order: number;
  kind: "human" | "automated";
  title: string;
  description: string;
  prompt: string;
  locator: { type: string; value: string } | null;
  status: "reviewing" | "ai_approved" | "approved" | "rejected";
  reason: string;
  revision: number;
  goal_match?: { decision?: string; goal?: string | null } | null;
};

export type Submission = {
  id: string;
  goal_name: string;
  description: string;
  mode: string;
  mode_label: string;
  status: "reviewing" | "needs_changes" | "ai_approved" | "approved" | "withdrawn";
  parent_procedure_id: string | null;
  steps: Step[];
  created_at?: string;
  updated_at?: string;
  message?: string;
  /** Per-procedure billing (docs/procedure_billing.md). Absent or null = free: the block does not render at all,
   *  because free is the default state of the world and a "₹0" badge would invent a price where none exists. */
  price_paise?: number | null;
  price_currency?: string | null;
  /** One-run purchase state for THIS person and THIS version: paid = an output is bought and not produced. */
  purchase_status?: "none" | "paid" | "used" | "refunded" | null;
  /** The old field, kept for clients that have not moved: paid reads as purchased, used reads as none. */
  licence_status?: "none" | "purchased" | "refunded" | null;
  /** The typical cost of ONE run, in paise - the models and tools the steps call, not the price. Absent when the
   *  Way has no measured runs yet, which the price block says rather than showing a made-up figure. */
  run_cost_paise?: number | null;
  /** What this Way takes before it can start. Absent = it needs nothing, which is the normal case. */
  inputs?: WayInputDef[];
  /** The artefact this version produces (docs/procedure_outputs.md). The bytes live in object storage; this is a
   *  locator plus metadata. Absent = nothing published yet, which the page says rather than hiding the section. */
  output?: WayOutput | null;
  /** Others like this, for the rail at the foot of the page — the "more like this" a video site would show. */
  similar?: WaySibling[];
};

export type WayOutput = {
  title?: string;
  /** "pdf" | "pptx" | "docx" | "xlsx" | "png" | … — drives the fallback tile and the button label. */
  kind?: string;
  /** A time-limited or public URL to the bytes. Never a Neon row: the database stores a key, not a file. */
  url: string;
  /** A small preview image (first page / first slide), served from the CDN, not from Postgres. */
  thumb_url?: string | null;
  page_count?: number | null;
  size_bytes?: number | null;
};

export type WaySibling = {
  id: string;
  goal_name: string;
  /** A line of the Way's own description, so a thumbnail-less tile still says what it does. */
  description?: string;
  thumb_url?: string | null;
  /** Why it is here: "same Goal", "same publisher", "same output kind". Shown, not guessed. */
  relation?: string;
  price_paise?: number | null;
  price_currency?: string | null;
};

type Load =
  | { kind: "loading" }
  | { kind: "sign-in-required" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ok"; way: Submission };

const STATUS_COPY: Record<string, { label: string; tone: string }> = {
  approved: { label: "Approved", tone: "good" },
  ai_approved: { label: "AI approved — not yet human-reviewed", tone: "warn" },
  reviewing: { label: "In review", tone: "quiet" },
  needs_changes: { label: "Needs changes", tone: "bad" },
  withdrawn: { label: "Withdrawn", tone: "quiet" },
};

/** The short id a reader can quote in a bug report, without the whole uuid. */
const short = (id: string) => id.slice(0, 8);

/**
 * A rate with its interval. Renders nothing numeric without `n` and a bound: this is the component that
 * makes the "never a bare percentage" rule impossible to break by accident.
 */
function Rate({ successes, total }: { successes: number | null; total: number | null }) {
  // The maths lives in lib/way-rate.ts so it can be tested: a rate without its n and interval is the exact
  // failure this page exists to correct, so the rule is enforced where a test can reach it.
  const iv = rateFor(successes, total);
  if (!iv) {
    return <span className="kwm-muted">Not measured — no run has been recorded against this version yet.</span>;
  }
  return (
    <span className="kwm-rate">
      <b>{(iv.p * 100).toFixed(1)}%</b>
      <span className="kwm-muted">
        {" "}
        ({iv.successes}/{iv.total}, 95% CI {Math.round(iv.lower * 100)}–{Math.round(iv.upper * 100)}%)
      </span>
    </span>
  );
}

/**
 * The price, and what buying it does. Present only when a price exists (docs/procedure_billing.md §7).
 *
 * Two things this deliberately does NOT do:
 *  - It does not show a price for a free Way. `priceLabel` returns null, so the block never renders.
 *  - It does not claim the purchase is complete until the gateway confirms. While the request is in flight the
 *    button says so; a failure states that nothing was charged, because that is true.
 */
function PriceBlock({ way, price, onBuy, onWarm, busy, error }: {
  way: Submission;
  price: string;
  onBuy: () => void;
  /** Start creating the hosted checkout before the click; a Razorpay link takes seconds to make. */
  onWarm: () => void;
  busy: boolean;
  error: string | null;
}) {
  const purchased = way.purchase_status === "paid" || way.licence_status === "purchased";
  const used = way.purchase_status === "used";
  const refunded = way.purchase_status === "refunded" || way.licence_status === "refunded";

  return (
    <section className="kwm-card kwm-price" aria-labelledby="kwm-price-h">
      <h2 id="kwm-price-h">{purchased ? "Purchased" : used ? "Output delivered" : "Buy one output"}</h2>
      {purchased ? (
        <p>
          You paid for <b>one run</b> of this version. Start it in kell desktop; the output is yours. This is
          not a subscription, and a later revision is a separate purchase.
        </p>
      ) : (
        <>
          <p className="kwm-price-figure">
            <b>{price}</b>
            <span className="kwm-muted"> for one output</span>
          </p>
          {used && (
            <p className="kwm-small kwm-muted">
              You already received this Way&apos;s output. Buy another output to produce it again.
            </p>
          )}
          {refunded && (
            <p className="kwm-small kwm-muted">
              A previous purchase of this version was refunded, so it needs buying again.
            </p>
          )}
          <p>
            <button
              type="button"
              className="kwm-cta"
              onClick={onBuy}
              onMouseEnter={onWarm}
              onFocus={onWarm}
              onTouchStart={onWarm}
              disabled={busy}
              aria-busy={busy || undefined}
            >
              {busy ? "Opening payment…" : used || refunded ? `Buy another output for ${price}` : `Buy one output for ${price}`}
            </button>
          </p>
          {error && (
            <p className="kwm-small kwm-reject">
              <span className="kwm-k">not charged</span> {error}
            </p>
          )}
        </>
      )}
    </section>
  );
}

/** A human byte size for an artefact ("412 KB"), or "" when unknown — never "0 B" for something that exists. */
function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function WayPage({ id }: { id: string }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    (async () => {
      // PUBLIC BY DEFAULT. A Way page is a sales page: a reader must be able to see the artefact and the price
      // before deciding to sign in. So this always fetches, and sends the token only when there is one — the token
      // upgrades the answer (adding `purchase_status` for this person) rather than being the price of admission.
      const token = await getAccessToken();
      try {
        const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(id)}`, {
          headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        });
        if (!live) return;
        if (res.status === 404) return setLoad({ kind: "missing" });
        // 401 is not a wall any more. It means the server has no public view of this Way, which is the one case
        // where signing in is genuinely required — and it is now a nudge inside a page that still explains itself,
        // not a blank screen.
        if (res.status === 401) return setLoad({ kind: "sign-in-required" });
        if (!res.ok) return setLoad({ kind: "error", message: `The server answered ${res.status}.` });
        const body = await res.json();
        // The route answers {submission: {...}} on some paths and the object itself on others; accept both
        // rather than guessing, so a shape change cannot silently render an empty page.
        const way: Submission | undefined = body?.submission ?? (body?.id ? body : undefined);
        if (!way?.id) return setLoad({ kind: "missing" });
        setLoad({ kind: "ok", way });
        // "More like this" for anyone, signed in or not: the public catalogue narrowed by this Way's
        // own goal, then — when that goal is unique — other published Ways. Every tile names its
        // relation, and a failure here must never take the Way page down.
        if (!way.similar?.length) {
          type CatalogueRow = { id?: string; goal_name?: string; description?: string; price_paise?: number | null; price_currency?: string | null };
          const pick = (rows: CatalogueRow[], relation: string): WaySibling[] =>
            rows
              .filter((r) => r?.id && r.id !== way.id)
              .slice(0, 6)
              .map((r) => ({
                id: String(r.id),
                goal_name: r.goal_name || "Untitled Way",
                description: r.description || "",
                relation,
                thumb_url: null,
                price_paise: r.price_paise ?? null,
                price_currency: r.price_currency ?? "INR",
              }));
          try {
            const q = encodeURIComponent(String(way.goal_name || "").slice(0, 80));
            let res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/public?q=${q}&limit=8`, {
              headers: { Accept: "application/json" },
            });
            let rows = res.ok ? (await res.json())?.ways : null;
            let siblings = Array.isArray(rows) ? pick(rows, "same Goal") : [];
            if (!siblings.length) {
              res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/public?limit=8`, {
                headers: { Accept: "application/json" },
              });
              rows = res.ok ? (await res.json())?.ways : null;
              siblings = Array.isArray(rows) ? pick(rows, "another published Way") : [];
            }
            if (live && siblings.length) setLoad({ kind: "ok", way: { ...way, similar: siblings } });
          } catch { /* the rail simply does not appear */ }
        }
      } catch {
        if (live) setLoad({ kind: "error", message: "Could not reach the server." });
      }
    })();
    return () => {
      live = false;
    };
  }, [id]);

  if (load.kind === "loading") {
    return (
      <section className="kwm page-hero frame grid">
        <p className="kwm-muted" role="status">
          Loading the Way…
        </p>
      </section>
    );
  }

  if (load.kind === "sign-in-required") {
    // The one case that still needs an account: the server has no public view of this Way. Say why, and keep the
    // door where the person already is rather than dumping them at a blank sign-in screen.
    return (
      <section className="kwm page-hero frame grid">
        <div className="kwm-notice">
          <p>
            This Way is not public yet, so its record is only readable to the person who owns it.{" "}
            <Link href={`/sign-in?redirect=${encodeURIComponent(`/way/${id}`)}`}>Sign in</Link> to read it.
          </p>
          <p className="kwm-small kwm-muted">
            Ways that are published are readable by anyone, without an account — the output and the price come
            first, because that is what a buyer needs to decide.
          </p>
        </div>
      </section>
    );
  }

  if (load.kind === "missing" || load.kind === "error") {
    return (
      <section className="kwm page-hero frame grid">
        <div className="kwm-notice" data-tone="bad">
          <p>
            {load.kind === "missing"
              ? "No Way with that id — it may have been withdrawn, or the link may be from another account."
              : load.message}
          </p>
          <p className="kwm-muted">
            Ways are listed on <Link href="/submit">Submit a Way</Link>.
          </p>
        </div>
      </section>
    );
  }

  const { way } = load;
  return <WayCard way={way} />;
}

/**
 * The page body itself, given the Way. Split from the loader so a preview (or a test) can render the real
 * layout from a payload without a signed-in fetch — there is still exactly one implementation of the layout.
 */
export function WayCard({ way }: { way: Submission }) {
  // Buying state lives in the card, not the loader: it is about this Way's price, and it must not survive a
  // reload as if the purchase had happened.
  const [buying, setBuying] = useState(false);
  const [buyError, setBuyError] = useState<string | null>(null);
  const price = priceLabel(way.price_paise, way.price_currency ?? "INR");
  const output = way.output ?? null;

  // The hosted checkout is created ahead of the click: a Razorpay link takes about five seconds to
  // make, so hover/focus starts the request and the click only redirects. One in-flight promise is
  // shared, so a fast hover-then-click never opens two purchases.
  const checkout = useRef<Promise<{ url: string | null; error: string | null }> | null>(null);
  const [readyUrl, setReadyUrl] = useState<string | null>(null);

  async function openCheckout(): Promise<{ url: string | null; error: string | null }> {
    const token = await getAccessToken();
    if (!token) return { url: null, error: null };          // buy() sends them to sign-in
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), 25000);
    try {
      const res = await fetch(`${MCP_ORIGIN}/v1/procedures/${encodeURIComponent(way.parent_procedure_id ?? way.id)}/purchase`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ procedure_row_id: way.id }),
        signal: stop.signal,
      });
      if (res.status === 404 || res.status === 501) {
        return { url: null, error: "Buying is not open yet on this server. You have not been charged." };
      }
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message = body?.error?.message || body?.error || body?.detail;
        return { url: null, error: String(message || `The server answered ${res.status}. You have not been charged.`) };
      }
      const url = body?.checkout_url || body?.redirect_url;
      return typeof url === "string" && url
        ? { url, error: null }
        : { url: null, error: "The payment could not be opened. You have not been charged." };
    } catch (err) {
      return {
        url: null,
        error: err instanceof DOMException && err.name === "AbortError"
          ? "The payment page took too long to open. You have not been charged; try again."
          : "Could not reach the server. You have not been charged.",
      };
    } finally {
      clearTimeout(timer);
    }
  }

  function prewarm() {
    if (readyUrl || checkout.current) return;
    checkout.current = openCheckout();
    checkout.current.then((r) => { if (r.url) setReadyUrl(r.url); }).catch(() => {});
  }

  async function buy() {
    setBuying(true);
    setBuyError(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        // A signed-out reader is a legitimate visitor to a sales page: buying needs an account, so send
        // them through sign-in and back to this exact Way rather than losing them to an error line.
        window.location.assign(`/sign-in?redirect=${encodeURIComponent(`/way/${way.id}`)}`);
        return;
      }
      if (readyUrl) {
        window.location.assign(readyUrl);
        return;
      }
      if (!checkout.current) checkout.current = openCheckout();
      let result = await checkout.current;
      if (!result.url && !result.error) result = await openCheckout();
      if (result.url) {
        window.location.assign(result.url);
        return;
      }
      setBuyError(result.error || "The payment could not be opened. You have not been charged.");
    } catch {
      setBuyError("Could not reach the server. You have not been charged.");
    } finally {
      setBuying(false);
    }
  }
  const status = STATUS_COPY[way.status] ?? { label: way.status, tone: "quiet" };
  const steps = [...(way.steps || [])].sort((a, b) => a.order - b.order);
  const automated = steps.filter((s) => s.kind === "automated").length;
  const rejected = steps.filter((s) => s.status === "rejected").length;
  const human = steps.length - automated;
  const runnable = way.status === "approved" || way.status === "ai_approved";
  // A priced Way the reader has not bought: the page must not offer a run it cannot deliver.
  const needsPurchase = !!price && !(way.purchase_status === "paid" || way.licence_status === "purchased");
  const version = way.parent_procedure_id ? `@${short(way.parent_procedure_id)}` : "";

  return (
    <section className="kwm frame grid">
      {/* 1. Identity — the name and the version. The WAY marker, its rule and the short id are gone:
          they were chrome above the thing a reader came for, and the id is reachable from the record. */}
      <header className="kwm-id">
        <h1 className="display">
          {way.goal_name}
          {version && <span className="kwm-version">{version}</span>}
        </h1>
        <div className="kwm-meta">
          <span className="kwm-badge" data-tone={status.tone}>
            {status.label}
          </span>
          <span className="kwm-chip">{way.mode_label || way.mode}</span>
          <span className="kwm-chip">
            {steps.length} step{steps.length === 1 ? "" : "s"} · {automated} automated · {human} human
          </span>
          {way.created_at && (
            <span className="kwm-chip" title={way.created_at}>
              created {new Date(way.created_at).toLocaleDateString()}
            </span>
          )}
        </div>
      </header>

      <div className="kwm-cols">
        <div className="kwm-main">
          {/* 2. Output — shown only when there is one. The bytes never live in Postgres
              (docs/procedure_outputs.md). */}
          {output && (
            <section className="kwm-card kwm-output" aria-labelledby="kwm-output-h">
              <h2 id="kwm-output-h">Output</h2>
              <div className="kwm-output-body">
                <a className="kwm-output-preview" href={output.url} target="_blank" rel="noopener noreferrer">
                  {output.thumb_url ? (
                    <img src={output.thumb_url} alt={`${output.title || "Output"} — first page`} loading="lazy" width={320} height={226} />
                  ) : (
                    <span className="kwm-output-fallback" aria-hidden="true">
                      {output.kind === "pdf" ? "PDF" : (output.kind || "FILE").toUpperCase()}
                    </span>
                  )}
                </a>
                <div className="kwm-output-meta">
                  <b>{output.title || way.goal_name}</b>
                  <span className="kwm-small kwm-muted">
                    {(output.kind || "file").toUpperCase()}
                    {output.page_count ? ` · ${output.page_count} page${output.page_count === 1 ? "" : "s"}` : ""}
                    {typeof output.size_bytes === "number" ? ` · ${formatBytes(output.size_bytes)}` : ""}
                  </span>
                  <p>
                    <a className="kwm-cta" href={output.url} target="_blank" rel="noopener noreferrer">
                      {output.kind === "pdf" ? "Open the PDF" : "Open the output"}
                    </a>
                  </p>
                  <p className="kwm-small kwm-muted">
                    A sample produced by this version — not a description of what it would produce.
                  </p>
                </div>
              </div>
            </section>
          )}

          {/* 2b. Price and one-run purchase (docs/procedure_billing.md §7). Absent price = free = no
              block at all. It sits after Output because the price is only meaningful once the reader
              knows what it produces. */}
          {price && (
            <PriceBlock way={way} price={price} onBuy={buy} onWarm={prewarm} busy={buying} error={buyError} />
          )}

          {/* 3. Description - what this Way does, in the author's words. */}
          <section className="kwm-card" aria-labelledby="kwm-what-h">
            <h2 id="kwm-what-h">Description</h2>
            <p>{way.description || way.goal_name}</p>
          </section>

          {/* 4. The decomposition — versions on the nodes, one atom per step. */}
          <section className="kwm-card" aria-labelledby="kwm-steps-h">
            <h2 id="kwm-steps-h">The decomposition</h2>
            {steps.length === 0 ? (
              <p className="kwm-muted">This Way records no steps.</p>
            ) : (
              <ol className="kwm-steps">
                {steps.map((s) => (
                  <li key={s.order} className="kwm-step" data-kind={s.kind} data-status={s.status}>
                    <span className="kwm-dot" aria-hidden="true" />
                    <div className="kwm-step-body">
                      <div className="kwm-step-head">
                        <span className="kwm-order">{s.order}</span>
                        <b>{s.title || `Step ${s.order}`}</b>
                        <i className="kwm-kind">{s.kind === "human" ? "person" : "automated"}</i>
                      </div>
                      {s.description && <p>{s.description}</p>}
                      {s.prompt && (
                        <p className="kwm-prompt">
                          <span className="kwm-k">does</span> {s.prompt}
                        </p>
                      )}
                      {s.locator?.value && (
                        <p className="kwm-loc kwm-mono" title={s.locator.value}>
                          <span className="kwm-k">{s.locator.type}</span> {s.locator.value}
                        </p>
                      )}
                      {s.status === "rejected" && s.reason && (
                        <p className="kwm-reject">
                          <span className="kwm-k">rejected</span> {s.reason}
                        </p>
                      )}
                      {s.revision > 0 && (
                        <p className="kwm-small kwm-muted">
                          revised {s.revision} time{s.revision === 1 ? "" : "s"}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {/* 5. Version history — an honest single entry when nothing has changed yet. */}
          <section className="kwm-card" aria-labelledby="kwm-hist-h">
            <h2 id="kwm-hist-h">Version history</h2>
            <ul className="kwm-hist">
              <li>
                <span className="kwm-chip">{version || "current"}</span>
                <span>
                  {status.label}
                  {way.updated_at ? ` · last changed ${new Date(way.updated_at).toLocaleDateString()}` : ""}
                </span>
              </li>
            </ul>
            <p className="kwm-small kwm-muted">
              A change that breaks a consumer of this Way is marked <b>Breaking</b> and says what breaks.
            </p>
          </section>

          {/* 6. Run this — the inputs the Way declares, then the questions a step asks mid-run
              (docs/procedure_billing.md §7.1, app/submissions/way_inputs.py). Above provenance,
              because running is what the buyer came for. Optional inputs are optional: a Way with
              none starts with one press. */}
          {runnable && (
            <section className="kwm-card" aria-labelledby="kwm-run-h">
              <h2 id="kwm-run-h">Run this</h2>
              {needsPurchase ? (
                // A published, priced Way that the reader has not bought. Saying "Run this Way" here
                // would promise a run the backend must refuse; the honest action is the price.
                <p>
                  <a className="kwm-cta" href="#kwm-price-h">
                    {price ? `Buy for ${price} to run this` : "Buy this to run it"}
                  </a>
                </p>
              ) : (
                <WayRun way={way} />
              )}
            </section>
          )}

          {/* 7. Provenance — kept as a heading only. */}
          <section className="kwm-card" aria-labelledby="kwm-prov-h">
            <h2 id="kwm-prov-h">Provenance</h2>
          </section>

          {/* 8. Reviews. Opinion, kept apart from the evidence: a rating never changes a success rate. */}
          <Reviews submissionId={way.id} />

          {/* 9. Sideways navigation — the two doors that are not a comment box. */}
          <footer className="kwm-next">
            <Link href="/submit">Browse Ways</Link>
            <Link href={`/submit?parent=${encodeURIComponent(way.id)}`}>Propose an improvement</Link>
            {way.parent_procedure_id && (
              <Link href={`/way/${encodeURIComponent(way.parent_procedure_id)}`}>See the procedure</Link>
            )}
          </footer>
        </div>

        {/* The right rail: related Ways first, then honest links out. On a narrow screen it drops below
            the page rather than squeezing the reading column. */}
        <WayRail items={way.similar ?? []} goal={way.goal_name} />
      </div>

      {way.message && <p className="kwm-small kwm-muted">Note from kell: {way.message}</p>}
    </section>
  );
}

/** The right rail: related Ways (the column a video site puts beside the thing you are watching), then
 *  honest links out of kell. The outbound links are search links, not endorsements, and the copy says so. */
function WayRail({ items, goal }: { items: WaySibling[]; goal: string }) {
  const q = encodeURIComponent((goal || "").slice(0, 80));
  return (
    <aside className="kwm-rail" aria-label="Related">
      {items.length > 0 && (
        <section className="kwm-card kwm-rail-card" aria-labelledby="kwm-similar-h">
          <h2 id="kwm-similar-h">More like this</h2>
          <ul className="kwm-rail-list">
            {items.map((s) => {
              const p = priceLabel(s.price_paise, s.price_currency ?? "INR");
              return (
                <li key={s.id}>
                  <a
                    href={`/way/${encodeURIComponent(s.id)}`}
                    className="kwm-rail-link"
                    data-text={s.thumb_url ? undefined : "true"}
                  >
                    {s.thumb_url && (
                      <span className="kwm-rail-thumb">
                        <img src={s.thumb_url} alt="" loading="lazy" width={120} height={68} />
                      </span>
                    )}
                    <span className="kwm-rail-body">
                      <b>{s.goal_name}</b>
                      {s.description && <span className="kwm-rail-desc">{s.description}</span>}
                      <span className="kwm-small kwm-muted">
                        {p ?? "Free"}
                        {s.relation ? ` · ${s.relation}` : ""}
                      </span>
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="kwm-card kwm-rail-card" aria-labelledby="kwm-elsewhere-h">
        <h2 id="kwm-elsewhere-h">Elsewhere</h2>
        <ul className="kwm-rail-list kwm-rail-out">
          <li>
            <a href={`https://www.youtube.com/results?search_query=${q}`} target="_blank" rel="noopener noreferrer">
              Videos on YouTube
            </a>
          </li>
          <li>
            <a href={`https://www.google.com/search?q=${q}`} target="_blank" rel="noopener noreferrer">
              Guides on the web
            </a>
          </li>
        </ul>
        <p className="kwm-small kwm-muted">
          Search links, not endorsements: kell has not watched or checked these.
        </p>
      </section>
    </aside>
  );
}

type Review = {
  id: string;
  author_name?: string | null;
  rating: number;
  body: string;
  created_at?: string;
};

function Stars({ value }: { value: number }) {
  const n = Math.max(0, Math.min(5, Math.round(value)));
  return (
    <span className="kwm-stars" aria-label={`${n} out of 5`}>
      <span aria-hidden="true">{"★".repeat(n)}{"☆".repeat(5 - n)}</span>
    </span>
  );
}

/**
 * Reviews on a Way. Opinion, kept in its own table and its own section: a rating never moves a success
 * rate. The list is public; writing one needs a kell account, because an anonymous review is not a
 * review. Posting again edits your earlier review rather than adding a second.
 */
function Reviews({ submissionId }: { submissionId: string }) {
  const [data, setData] = useState<{ reviews: Review[]; count: number; average: number | null } | null>(null);
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [rating, setRating] = useState(0);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function load() {
    try {
      const res = await fetch(
        `${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(submissionId)}/reviews`,
        { headers: { Accept: "application/json" } },
      );
      if (!res.ok) return;
      const body = await res.json();
      setData({
        reviews: Array.isArray(body?.reviews) ? body.reviews : [],
        count: Number(body?.count || 0),
        average: typeof body?.average === "number" ? body.average : null,
      });
    } catch { /* the section stays on "loading", which is honest: it could not read them */ }
  }

  useEffect(() => {
    let live = true;
    (async () => {
      const token = await getAccessToken();
      if (!live) return;
      setSignedIn(!!token);
      await load();
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submissionId]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    if (rating < 1) { setError("choose a rating from 1 to 5"); return; }
    if (text.trim().length < 3) { setError("write a sentence or two about the output"); return; }
    const token = await getAccessToken();
    if (!token) {
      window.location.assign(`/sign-in?redirect=${encodeURIComponent(`/way/${submissionId}`)}`);
      return;
    }
    setSending(true);
    try {
      const res = await fetch(
        `${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(submissionId)}/reviews`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ rating, body: text }),
        },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message = body?.error?.message || body?.error || body?.detail;
        setError(String(message || `The server answered ${res.status}.`));
        return;
      }
      setRating(0);
      setText("");
      setSaved(true);
      await load();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setSending(false);
    }
  }

  const average = data?.average ?? null;
  const count = data?.count ?? 0;
  const bodyId = `kwm-review-body-${submissionId}`;

  return (
    <section className="kwm-card kwm-reviews" aria-labelledby="kwm-reviews-h">
      <h2 id="kwm-reviews-h">Reviews</h2>
      {average !== null ? (
        <p className="kwm-review-summary">
          <Stars value={average} /> <b>{average.toFixed(1)}</b>{" "}
          <span className="kwm-muted">from {count} review{count === 1 ? "" : "s"}</span>
        </p>
      ) : (
        <p className="kwm-muted">No reviews yet. If you have run this Way, say what the output was like.</p>
      )}

      {data === null ? (
        <p className="kwm-small kwm-muted" role="status">Loading reviews…</p>
      ) : data.reviews.length > 0 ? (
        <ul className="kwm-review-list">
          {data.reviews.map((r) => (
            <li key={r.id} className="kwm-review">
              <div className="kwm-review-head">
                <b>{r.author_name || "kell user"}</b>
                <Stars value={r.rating} />
                {r.created_at && (
                  <span className="kwm-small kwm-muted">{new Date(r.created_at).toLocaleDateString()}</span>
                )}
              </div>
              <p>{r.body}</p>
            </li>
          ))}
        </ul>
      ) : null}

      {signedIn === false ? (
        <p className="kwm-small">
          <Link href={`/sign-in?redirect=${encodeURIComponent(`/way/${submissionId}`)}`}>Sign in</Link> to review this Way.
        </p>
      ) : signedIn ? (
        <form className="kwm-review-form" onSubmit={submit}>
          <fieldset className="kwm-rating">
            <legend className="kwm-k">Your rating</legend>
            <div className="kwm-rating-row">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  className="kwm-star"
                  aria-label={`${n} star${n === 1 ? "" : "s"}`}
                  aria-pressed={rating === n}
                  onClick={() => setRating(n)}
                >
                  ★
                </button>
              ))}
            </div>
          </fieldset>
          <label className="kwm-k" htmlFor={bodyId}>What was the output like?</label>
          <textarea
            id={bodyId}
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={4000}
            rows={4}
            placeholder="One or two sentences: what you asked for, and whether the output was usable."
          />
          {error && <p className="kwm-small kwm-reject">{error}</p>}
          {saved && !error && <p className="kwm-small kwm-muted">Saved — your review is shown above.</p>}
          <p>
            <button type="submit" className="kwm-cta" disabled={sending} aria-busy={sending || undefined}>
              {sending ? "Saving…" : "Post review"}
            </button>
          </p>
        </form>
      ) : null}
    </section>
  );
}

/**
 * The run: declared inputs -> start -> answer questions the steps raise -> done.
 *
 * Two states, because they are two different moments. Before the run it asks for what the Way declares (and most
 * Ways declare nothing, so the button is the whole interaction). During the run it shows whichever question the
 * paused step is waiting on — one at a time, per app/submissions/way_inputs.py.
 *
 * Every failure is stated plainly and never as a spinner that never resolves.
 */
function WayRun({ way }: { way: Submission }) {
  const inputs = way.inputs ?? [];
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<RunState | null>(null);

  async function start(values: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) return setError("Sign in to run this Way.");
      const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(way.id)}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ input: Object.values(values).filter(Boolean).join("\n") || "" }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) return setError(String(body?.error || `The server answered ${res.status}.`));
      setRun(body);
      setRunning(true);
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function answer(value: string | string[]) {
    if (!run?.id) return;
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${MCP_ORIGIN}/v1/goals/runs/${encodeURIComponent(run.id)}/done`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ note: Array.isArray(value) ? value.join(", ") : value }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) return setError(String(body?.error || `The server answered ${res.status}.`));
      setRun(body);
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  if (!running) {
    return (
      <>
        <InputsForm inputs={inputs} onStart={start} busy={busy} error={error} />
        <p className="kwm-small kwm-muted">
          Runs on kell&apos;s own models. Every step sees the run&apos;s input and the output of the steps before it.
        </p>
      </>
    );
  }

  if (run?.status === "done") {
    return (
      <p>
        Done.{" "}
        {run.outputs?.length
          ? `It produced ${run.outputs.length} output${run.outputs.length === 1 ? "" : "s"}.`
          : ""}
      </p>
    );
  }

  if (run?.question) {
    return <QuestionCard question={run.question} busy={busy} error={error} onAnswer={answer} />;
  }

  if (run?.waiting_on) {
    return (
      <p className="kwm-small kwm-muted">
        Waiting on step {run.waiting_on.order}: {run.waiting_on.title}
        {run.waiting_on.link ? (
          <>
            {" "}
            · <a href={run.waiting_on.link}>open it</a>
          </>
        ) : null}
      </p>
    );
  }

  return <p className="kwm-small kwm-muted">Running…</p>;
}

/** The slice of a run reply this panel reads. Unknown fields are ignored, never guessed at. */
type RunState = {
  id?: string;
  status?: "running" | "waiting_human" | "waiting_session" | "done" | "failed" | "cancelled";
  waiting_on?: { order: number; title: string; link?: string } | null;
  outputs?: { order: number; title: string; output: string }[];
  question?: WayQuestion | null;
};
