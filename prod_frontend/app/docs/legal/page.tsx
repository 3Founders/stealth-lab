import type { Metadata } from "next";
import Link from "next/link";
import { LEGAL_DOCS } from "@/lib/legal";

export const metadata: Metadata = { title: "Legal" };

export default function LegalIndex() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>DOCS</b><span>/ LEGAL</span></div>
        <h1 className="display">Legal.</h1>
        <p className="lead">The policies and terms that govern the service.</p>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <div className="empty">
          <b>Drafts — not yet in effect.</b>
          <p>These documents are drafts awaiting legal review. They describe what the software does today; bracketed placeholders are still to be filled in. They are shown as written, without edits.</p>
        </div>
        <ul className="list">
          {LEGAL_DOCS.map((d, i) => (
            <li key={d.slug}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>
              <div><h3><Link href={`/docs/legal/${d.slug}`}>{d.title}</Link></h3><p className="small dim">{d.blurb}</p></div>
              <span className="caption dim">Draft</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
