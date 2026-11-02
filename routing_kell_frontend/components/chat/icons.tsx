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

/** The settings gear: the chat menu's button. */
export function SettingsIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <circle cx="10" cy="10" r="2.6" />
      <path d="M10 2.2v1.9M10 15.9v1.9M2.2 10h1.9M15.9 10h1.9M4.5 4.5l1.35 1.35M14.15 14.15l1.35 1.35M15.5 4.5l-1.35 1.35M5.85 14.15 4.5 15.5" />
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

// The next two are Lucide's "copy" and "rotate-ccw" (lucide.dev, ISC license, downloaded 2026-11-02), drawn on this
// module's house stroke (1.6, round caps, currentColor) so they sit beside the icons above as one family.

/** Copy a message: two rounded rectangles, the front one offset. */
export function CopyIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...base}>
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </svg>
  );
}

/** Retry a message: an anticlockwise arrow re-entering the circle. */
export function RetryIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...base}>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
    </svg>
  );
}
