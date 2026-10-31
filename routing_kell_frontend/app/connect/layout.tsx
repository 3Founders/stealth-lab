import AnimatedHeading from "@/components/AnimatedHeading";
import ConnectNav from "@/components/connect/ConnectNav";

/** The header and the buttons shared by every Connect page; each guide is its own page beneath. */
export default function ConnectLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONNECT</b></div>
        <h1 className="display"><AnimatedHeading>Connect</AnimatedHeading></h1>
        <p className="lead">
          Set up keळ in Claude Code, Claude on the web or ChatGPT. Each guide is written for someone who has never used keळ:
          follow the steps in order and type exactly what is shown.
        </p>
      </section>
      <ConnectNav />
      <div className="frame" style={{ display: "grid", gap: "28px", paddingBottom: "80px" }}>{children}</div>
    </>
  );
}
