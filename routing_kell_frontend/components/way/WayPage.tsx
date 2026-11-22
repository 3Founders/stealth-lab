"use client";

/**
 * One Way (/way/[id]): everything a sceptical reader needs before they run it.
 *
 * The order is the page's whole argument. Identity first, then OUTPUT — before the description — because the
 * because a reader who does not believe it works should not be asked to read how. Then what it does and when
 * it applies, then the decomposition, then history, then composition, then provenance and how to run it
 * yourself. See docs/KELL.md §4 for why each section sits where it does.
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
import { useEffect, useState } from "react";
import Link from "next/link";
import { getAccessToken } from "@/lib/session";
import { MCP_ORIGIN } from "@/lib/connector";
import { rateFor, priceLabel } from "@/lib/way-rate";
import "@/app/way/way.css";

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
  /** Whether THIS person already holds a licence for THIS version. */
  licence_status?: "none" | "purchased" | "refunded" | null;
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
function PriceBlock({ way, price, onBuy, busy, error }: {
  way: Submission;
  price: string;
  onBuy: () => void;
  busy: boolean;
  error: string | null;
}) {
  const licensed = way.licence_status === "purchased";
  const refunded = way.licence_status === "refunded";
  const version = way.parent_procedure_id ? short(way.parent_procedure_id) : null;

  return (
    <section className="kwm-card kwm-price" aria-labelledby="kwm-price-h">
      <h2 id="kwm-price-h">{licensed ? "Licensed" : "Price"}</h2>
      {licensed ? (
        <p>
          You have a licence for this version. It runs as often as you need, and a later revision never replaces
          what you bought.
        </p>
      ) : (
        <>
          <p className="kwm-price-figure">
            <b>{price}</b>
            <span className="kwm-muted"> once</span>
          </p>
          <p>
            Buys the right to run <b>this version</b>
            {version ? <span className="kwm-mono"> @{version}</span> : null} as often as you need. Not a
            subscription, and a later revision is a separate Way.
          </p>
          {refunded && (
            <p className="kwm-small kwm-muted">
              A previous purchase of this version was refunded, so it needs buying again to run.
            </p>
          )}
          <p>
            <button type="button" className="kwm-cta" onClick={onBuy} disabled={busy} aria-busy={busy || undefined}>
              {busy ? "Opening payment…" : `Buy for ${price}`}
            </button>
          </p>
          {error && (
            <p className="kwm-small kwm-reject">
              <span className="kwm-k">not charged</span> {error}
            </p>
          )}
          <p className="kwm-small kwm-muted">
            Nothing is charged until the payment is confirmed. Prices include applicable taxes; the receipt
            shows the split.
          </p>
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
      // upgrades the answer (adding `licence_status` for this person) rather than being the price of admission.
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

  // The purchase itself (docs/procedure_billing.md §3). The backend endpoint lands with the gate; until it does,
  // this reports what actually happened rather than flipping to "Licensed" on a 404 — a page that shows a
  // purchase for money nobody took is the worst possible lie on a page about evidence.
  async function buy() {
    setBuying(true);
    setBuyError(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        // A signed-out reader is a legitimate visitor to a sales page: buying needs an account, so send them
        // through sign-in and back to this exact Way rather than losing them to an error line.
        window.location.assign(`/sign-in?redirect=${encodeURIComponent(`/way/${way.id}`)}`);
        return;
      }
      const res = await fetch(`${MCP_ORIGIN}/v1/procedures/${encodeURIComponent(way.parent_procedure_id ?? way.id)}/purchase`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ procedure_row_id: way.id }),
      });
      if (res.status === 404 || res.status === 501) {
        setBuyError("Buying is not open yet on this server. You have not been charged.");
        return;
      }
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setBuyError(String(body?.error || body?.detail || `The server answered ${res.status}. You have not been charged.`));
        return;
      }
      // The gateway hands back a redirect or a hosted-checkout URL; send the person there rather than
      // pretending the licence exists before it is paid for.
      const url = body?.checkout_url || body?.redirect_url;
      if (typeof url === "string" && url) {
        window.location.assign(url);
        return;
      }
      setBuyError("The payment could not be opened. You have not been charged.");
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
  const needsLicence = !!price && way.licence_status !== "purchased";
  const version = way.parent_procedure_id ? `@${short(way.parent_procedure_id)}` : "";

  return (
    <section className="kwm frame grid">
      {/* 1. Identity — the name, the version, the Goal inline. Never a name without its version. */}
      <header className="kwm-id">
        <p className="marker caption">
          <b>WAY</b>
        </p>
        <h1 className="display">
          {way.goal_name}
          {version && <span className="kwm-version">{version}</span>}
        </h1>
        <p className="kwm-goal">
          <span className="kwm-k">Goal</span> {way.goal_name}
        </p>
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
          <span className="kwm-chip kwm-mono" title="Quote this id in a report">
            {short(way.id)}
          </span>
        </div>
      </header>

      {/* 2. Output — the artefact the Way produces, ABOVE the description. What a Way makes is the thing a
          reader wants to see first: a report or a deck says more about whether you want it than any step list.
          The bytes do NOT live in Postgres: Neon holds a locator, sha256 and size, and the file itself lives in
          object storage (docs/procedure_outputs.md). */}
      <section className="kwm-card kwm-output" aria-labelledby="kwm-output-h">
        <h2 id="kwm-output-h">Output</h2>
        {output ? (
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
        ) : (
          <p className="kwm-muted">
            No output recorded for this version yet. When this Way runs, the artefact it produces is published
            here, so the next person can judge the result before they pay for it.
          </p>
        )}
      </section>

      {/* 2b. Price and licence (docs/procedure_billing.md §7). Absent price = free = no block at all. It sits
          after Output because the price is only meaningful once the reader knows what it produces, and
          before the description because nobody parts with money before they know what they are buying. */}
      {price && <PriceBlock way={way} price={price} onBuy={() => setBuying(true)} busy={buying} error={buyError} />}

      {/* 3. What it does, and when it applies. */}
      <section className="kwm-card" aria-labelledby="kwm-what-h">
        <h2 id="kwm-what-h">What it does</h2>
        <p>{way.description || way.goal_name}</p>
        {steps.length > 0 && (
          <p className="kwm-small kwm-muted">
            Applies when the {steps.length}-step sequence below can start: every human step has someone to do
            it, and every automated step names something that exists.
          </p>
        )}
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

      {/* 6. Composition and dependents. */}
      <section className="kwm-card" aria-labelledby="kwm-comp-h">
        <h2 id="kwm-comp-h">Composition</h2>
        {way.parent_procedure_id ? (
          <p>
            Backed by procedure <span className="kwm-mono">{short(way.parent_procedure_id)}</span>. This Way
            can be used as a step inside another Way; the reference is pinned to this exact version, so a
            later revision never changes what an existing composition runs.
          </p>
        ) : (
          <p className="kwm-muted">
            Not yet bound to a procedure, so it cannot be used as a step inside another Way.
          </p>
        )}
      </section>

      {/* 7. Provenance, and how to run it yourself. */}
      <section className="kwm-card" aria-labelledby="kwm-prov-h">
        <h2 id="kwm-prov-h">Provenance — run it yourself</h2>
        <p>
          A badge nobody can check is worth nothing. The record above is the same object this page read;
          re-read it, or run the Way against your own input.
        </p>
        <pre className="kwm-code">
          <code>{`curl -s ${MCP_ORIGIN}/v1/goals/submissions/${short(way.id)} \\\n  -H "authorization: Bearer $KELL_TOKEN"`}</code>
        </pre>
        {runnable ? (
          needsLicence ? (
            // A published, priced Way that the reader has not bought. Saying "Run this Way" here would promise
            // a run the backend must refuse; the honest action is to point at the price.
            <p>
              <a className="kwm-cta" href="#kwm-price-h">
                {price ? `Buy for ${price} to run this` : "Buy this to run it"}
              </a>
            </p>
          ) : (
            <p>
              <Link className="kwm-cta" href={`/submit?id=${encodeURIComponent(way.id)}`}>
                Run this Way
              </Link>
            </p>
          )
        ) : (
          <p className="kwm-muted">
            This Way is {status.label.toLowerCase()}, so it does not run yet.
          </p>
        )}
      </section>

      {/* 8. Sideways navigation — the two doors that are not a comment box. */}
      <footer className="kwm-next">
        <Link href="/submit">Browse Ways</Link>
        <Link href={`/submit?parent=${encodeURIComponent(way.id)}`}>Propose an improvement</Link>
        {way.parent_procedure_id && (
          <Link href={`/way/${encodeURIComponent(way.parent_procedure_id)}`}>See the procedure</Link>
        )}
      </footer>

      {/* 9. More like this — the thumbnail rail. A reader who has just judged one Way is at their most receptive
          to another, which is exactly why a video site puts its grid here. Each tile is a picture (or a labelled
          fallback tile) plus the price, so the grid answers "what else is there, and what does it cost" without a
          click. Renders nothing at all when there is nothing to show, rather than an empty shelf. */}
      {(way.similar?.length ?? 0) > 0 && <SimilarRail items={way.similar!} />}

      {way.message && <p className="kwm-small kwm-muted">Note from kell: {way.message}</p>}
    </section>
  );
}

/** "More like this": the grid at the foot of a Way page. Thumbnails come from the CDN, never from Postgres. */
function SimilarRail({ items }: { items: WaySibling[] }) {
  return (
    <section className="kwm-card kwm-similar" aria-labelledby="kwm-similar-h">
      <h2 id="kwm-similar-h">More like this</h2>
      <ul className="kwm-similar-rail">
        {items.map((s) => {
          const p = priceLabel(s.price_paise, s.price_currency ?? "INR");
          return (
            <li key={s.id} className="kwm-similar-tile">
              <a href={`/way/${encodeURIComponent(s.id)}`} className="kwm-similar-link">
                <span className="kwm-similar-thumb">
                  {s.thumb_url ? (
                    <img src={s.thumb_url} alt="" loading="lazy" width={240} height={135} />
                  ) : (
                    <span className="kwm-similar-blabel" aria-hidden="true">
                      {s.goal_name.slice(0, 2).toUpperCase()}
                    </span>
                  )}
                </span>
                <b className="kwm-similar-title">{s.goal_name}</b>
              </a>
              <span className="kwm-small kwm-muted">
                {p ?? "Free"}
                {s.relation ? ` · ${s.relation}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
