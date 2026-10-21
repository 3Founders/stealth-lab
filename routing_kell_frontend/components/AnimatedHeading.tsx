/**
 * Same per-line "rise" entrance the home hero's <h1> uses (globals.css's
 * `.motion h1.display .ln` rule, originally scoped to `.hero h1` only,
 * generalized so this component can reuse it anywhere). No JS trigger is
 * needed -- unlike Reveal.tsx's IntersectionObserver-driven fade, this
 * always plays on mount, which is correct here since every caller is a
 * page-hero heading already above the fold at load.
 *
 * Single line by default (`children`); pass `lines` (array of nodes) for
 * a staggered multi-line reveal like the home hero's 3-line headline.
 */
export default function AnimatedHeading({
  children, lines, delayStep = 90,
}: { children?: React.ReactNode; lines?: React.ReactNode[]; delayStep?: number }) {
  const items = lines ?? [children];
  return (
    <>
      {items.map((line, i) => (
        <span key={i} className="ln" style={{ "--d": `${i * delayStep}ms` } as React.CSSProperties}>
          <span>{line}</span>
        </span>
      ))}
    </>
  );
}
