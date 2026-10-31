/** Small line icons for the chat (currentColor, 1.6 stroke, round caps), used where a plain "x" said too little. */
const base = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, focusable: false } as const;

/** Closes the room list: a panel with its left column marked and an arrow pointing it away. */
export function PanelCloseIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <rect x="2.5" y="3.5" width="15" height="13" rx="2.5" />
      <path d="M8 3.5v13" />
      <path d="M5.9 8.4 4.3 10l1.6 1.6" />
    </svg>
  );
}

/** Takes an agent out of the room: a ring with a bar through it. */
export function RemoveIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <circle cx="10" cy="10" r="7" />
      <path d="M7 10h6" />
    </svg>
  );
}
