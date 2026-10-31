/* The Connect guides, in nav order. Kept out of ConnectNav.tsx on purpose: that file is a client component, and a server page that imports a
   value from a "use client" module gets a client reference instead of the array (the production build fails on `.filter`; dev and vitest do not). */
export const CONNECT_PAGES: Array<{ href: string; label: string; blurb: string }> = [
  { href: "/connect", label: "Overview", blurb: "Pick where you work." },
  { href: "/connect/claude-code", label: "Claude Code", blurb: "Install keळ in your terminal." },
  { href: "/connect/claude", label: "Claude on the web", blurb: "Add keळ as a connector on claude.ai. About 5 minutes." },
  { href: "/connect/chatgpt", label: "ChatGPT", blurb: "Add keळ as a custom connector." },
  { href: "/connect/help", label: "If something goes wrong", blurb: "Common problems and what to do." },
];
