import type { Metadata } from "next";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";

export const metadata: Metadata = {
  title: "How a pilot starts",
  description: "A spend audit first, then a replay of your own tasks, then a live trial against a number fixed in advance.",
};

const steps = [
  ["Spend audit", "Free, and before anything is installed. You share a usage export from the tools your team already uses. We work out how much of that spend is routine, checkable work, and say plainly whether there is enough to be worth testing. If there is not, we stop here."],
  ["Replay", "Your team supplies 40–60 of their own past tasks with the answers they accepted, with sensitive content removed. Both approaches run on them. The person who asked rates the outputs without knowing which approach produced which. This is where we first learn whether it holds on your kind of work."],
  ["Live trial", "A small group uses it for two weeks. Each person is switched on or off at random, so we compare the same team against itself. We record cost, rework turns, time and accepted answers."],
  ["Everyone on", "Two more weeks with everyone on, to see who keeps it switched on when nobody is making them."],
  ["Readout", "With whoever owns the AI budget. The rules for continue, stop or inconclusive are written down in the first week, not after the results."],
];

export default function Pilot() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>PILOT</b></div>
        <h1 className="display"><AnimatedHeading>How a pilot starts</AnimatedHeading></h1>
        <p className="lead">A spend audit first, then your own tasks, then a live trial against one number agreed in advance.</p>
      </section>

      <div className="frame grid" style={{ paddingBottom: 120 }}>
        <ol className="steps" style={{ gridColumn: "1 / span 12" }}>
          {steps.map(([t, d], i) => (
            <li key={t}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>
              <div><h3 className="h3">{t}</h3><p>{d}</p></div>
            </li>
          ))}
        </ol>

        <div className="doc-sec" style={{ gridColumn: "1 / span 12", marginTop: 56 }}>
          <h2>The number we agree first</h2>
          <p>
            Cost per accepted task falls by an agreed amount on the tasks that were routed, blind-rated quality stays within 10 points, and most of the
            group keep it on in the last two weeks. The amount is set from your spend audit, not from our benchmarks.
          </p>

          <h2>What we will not promise</h2>
          <ul>
            <li>We have no result yet on presentations or open-ended question answering. The replay is the first evidence for them.</li>
            <li>If your team is on flat per-seat plans inside their included usage, the dollar saving may be zero. We would then report time and quality, and say so.</li>
            <li>Routed tasks take longer. Expect tens of seconds, not instant.</li>
          </ul>
          <p className="small dim">
            What leaves your machine, and what we do not yet have, is on the <Link href="/trust" style={{ textDecoration: "underline" }}>data &amp; trust page</Link>.
            The measurements so far are on the <Link href="/evidence" style={{ textDecoration: "underline" }}>evidence page</Link>.
          </p>
        </div>
      </div>
    </>
  );
}
