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

/** Attach a picture: a frame with a hill and a sun. */
export function ImageIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <rect x="2.5" y="3.5" width="15" height="13" rx="2.5" />
      <circle cx="7.2" cy="8" r="1.3" />
      <path d="m3.5 14.5 4-3.8 3 2.6 2.3-2 3.7 3.2" />
    </svg>
  );
}

/** Take a picture off the message: a plain cross. */
export function CrossIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <path d="m5.5 5.5 9 9M14.5 5.5l-9 9" />
    </svg>
  );
}
