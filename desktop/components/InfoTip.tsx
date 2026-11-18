"use client"
// A small round "i" that explains something on hover or focus, in place of a line of explanatory text (owner,
// 2026-11-18: "remove text like send a task … instead add a small i button which explains this").
/** `align`: which way the explanation opens from the "i" -- "start" grows to the right, "end" to the left, "center" both. */
export default function InfoTip({ text, side = "below", align = "center" }: { text: string; side?: "below" | "above"; align?: "start" | "center" | "end" }) {
  return (
    <button type="button" className="dk-info" aria-label={text} data-side={side} data-align={align}>
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.2" />
        <circle cx="8" cy="4.9" r=".95" fill="currentColor" />
        <path d="M8 7.2v4.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
      <span className="dk-info-tip" role="tooltip">{text}</span>
    </button>
  )
}
