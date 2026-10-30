import Link from "next/link";
import { notFound } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";

const DOCS = {
  refunds: { title: "Refund and cancellation", what: "when a top-up can be refunded and how to cancel" },
} as const;
type Doc = keyof typeof DOCS;

export function generateStaticParams() {
  return (Object.keys(DOCS) as Doc[]).map((doc) => ({ doc }));
}

export async function generateMetadata({ params }: { params: Promise<{ doc: string }> }) {
  const { doc } = await params;
  return { title: doc in DOCS ? DOCS[doc as Doc].title : "Not found" };
}

/**
 * Placeholder for the one document a payment provider asks for that the legal section (/docs/legal) does not have yet. Terms and privacy are there already. They say plainly that the text is not final:
 * drafts are written separately and reviewed by a lawyer before anything here is in force (docs/billing.md, legal checklist).
 */
export default async function LegalPage({ params }: { params: Promise<{ doc: string }> }) {
  const { doc } = await params;
  if (!(doc in DOCS)) notFound();
  const d = DOCS[doc as Doc];
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>LEGAL</b></div>
        <h1 className="display"><AnimatedHeading>{d.title}</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="profile-card" style={{ gridColumn: "1 / span 12" }}>
          <p className="small"><b>This document is not published yet.</b> It will set out {d.what}. Until it is reviewed and published here, nothing on this page is in force.</p>
          <p className="small" style={{ marginTop: 10 }}>Questions before then: <Link href="/contact" style={{ textDecoration: "underline" }}>contact us</Link>.</p>
        </div>
      </section>
    </>
  );
}
