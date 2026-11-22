import Link from "next/link";
import { MCP_ORIGIN } from "@/lib/connector";
import "@/app/way/way.css";

/**
 * The stored record behind a Way, on kell's own domain.
 *
 * Provenance should not send a reader to the connector: the thing they are checking is the page they
 * just read, so the record is served here, verbatim and read-only. A fingerprint of the page's copy of
 * the record is shown on the Way page itself; this page is where a reader can see what that copy held.
 *
 * Nothing is edited on the way through: the JSON is the server's answer, pretty-printed. If the server
 * cannot be read, the page says so rather than rendering an empty object that would look like a record.
 */
export const dynamic = "force-dynamic";

export const metadata = {
  title: "A Way — stored record",
  robots: { index: false, follow: false },
};

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let record: unknown = null;
  let status = 0;
  try {
    const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(id)}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    status = res.status;
    if (res.ok) record = await res.json();
  } catch {
    status = 0;
  }

  return (
    <section className="kwm frame grid">
      <header className="kwm-id">
        <h1 className="display">Stored record</h1>
        <p className="kwm-muted">
          The object the Way page was rendered from, read-only. It is the same record, not a summary
          written afterwards.
        </p>
      </header>

      <section className="kwm-card" aria-labelledby="kwm-record-h">
        <h2 id="kwm-record-h">Record</h2>
        {record ? (
          <pre className="kwm-code">
            <code>{JSON.stringify(record, null, 2)}</code>
          </pre>
        ) : (
          <div className="kwm-notice" data-tone="bad">
            <p>
              {status === 404
                ? "No published Way with that id."
                : "The record could not be read just now. Try again in a moment."}
            </p>
          </div>
        )}
        <p className="kwm-small">
          <Link href={`/way/${encodeURIComponent(id)}`}>Back to the Way</Link>
        </p>
      </section>
    </section>
  );
}
