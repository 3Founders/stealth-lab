import { useId } from "react";

/**
 * kell's mark: a golden banana with a soft glow. It stands in for the agent wherever an agent speaks or is called (message avatars,
 * the agent list, the @ menu, the thinking indicator). Drawn for this app; the glow is a CSS drop-shadow so it follows the shape.
 */
export function KellMark({ size = 22, glow = true, label }: { size?: number; glow?: boolean; label?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className={glow ? "kell-mark kell-mark--glow" : "kell-mark"} width={size} height={size} viewBox="0 0 32 32" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true} focusable="false">
      <defs>
        <linearGradient id={`${id}g`} x1="6" y1="6" x2="27" y2="27" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#FFF08A" />
          <stop offset=".45" stopColor="#FFD21F" />
          <stop offset="1" stopColor="#E89A00" />
        </linearGradient>
      </defs>
      {/* the banana: a crescent from the stem at the top left to the tip at the lower right */}
      <path d="M8.2 6.4c-.9 8.4 3.4 17.1 16.3 18.1.9.1 1.4 1 .8 1.7-3.8 4.4-13.3 4.2-18.6-1.6C2.2 18.8 2.4 9.6 6.6 5.6c.8-.7 1.7.1 1.6.8Z" fill={`url(#${id}g)`} stroke="#B97700" strokeWidth=".9" strokeLinejoin="round" />
      {/* a ridge along the length, and the highlight */}
      <path d="M9.2 9.2c.4 6.6 4.6 12.6 13.4 14.2" fill="none" stroke="#C98A00" strokeWidth=".8" strokeLinecap="round" opacity=".55" />
      <path d="M7.4 11.2c.2 3.6 1.1 6.1 2.6 8.2" fill="none" stroke="#FFFBD0" strokeWidth="1.1" strokeLinecap="round" opacity=".8" />
      {/* the stem and the dark tip */}
      <path d="M6.7 5.7 6 3.2c-.1-.5.3-.9.8-.8l2.4.6c.5.1.7.7.4 1.1L8 6.6c-.4.5-1.2.3-1.3-.9Z" fill="#7A5200" />
      <path d="M24.6 24.6c.9.1 1.6.6 1.7 1.3.1.6-.3 1-.9 1.1l-1.4.1Z" fill="#5E3F00" />
    </svg>
  );
}

/** Claude's sunburst, in its coral. A simplified drawing for the picker; swap in the official asset if you have the right to. */
export function ClaudeLogo({ size = 20 }: { size?: number }) {
  const rays = Array.from({ length: 12 }, (_, i) => i * 30);
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <g stroke="#D97757" strokeWidth="2.6" strokeLinecap="round">
        {rays.map((a, i) => <line key={a} x1="12" y1={i % 2 ? 5.4 : 3.6} x2="12" y2="9" transform={`rotate(${a} 12 12)`} />)}
      </g>
    </svg>
  );
}

/** Cursor's cube, drawn in its grey and white. A simplified drawing for the picker. */
export function CursorLogo({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 2.5 21 7.7v8.6L12 21.5 3 16.3V7.7Z" fill="#26251E" />
      <path d="M12 2.5 21 7.7 12 12.9 3 7.7Z" fill="#6B6A60" />
      <path d="M12 12.9v8.6L3 16.3V7.7Z" fill="#3A3932" />
      <path d="M12 12.9 21 7.7v8.6l-9 5.2Z" fill="#EDECE4" />
    </svg>
  );
}

/** ChatGPT's knot, in OpenAI green: a simplified six-petal drawing for the picker. */
export function ChatGPTLogo({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <g fill="none" stroke="#10A37F" strokeWidth="1.9" strokeLinejoin="round">
        {[0, 60, 120].map((a) => <rect key={a} x="7.2" y="3" width="9.6" height="18" rx="4.8" transform={`rotate(${a} 12 12)`} />)}
      </g>
    </svg>
  );
}
