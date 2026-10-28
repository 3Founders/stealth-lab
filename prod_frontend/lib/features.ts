// Product features that are switched off by default. Each is read at build time.
//
// Project sync (encrypted upload of a local .kell/ folder to the account, /account and /account/projects):
// off since 2026-10-08. The local half never registers projects, so the list was always empty. Turn it back on
// with NEXT_PUBLIC_PROJECT_SYNC=on here AND PROJECT_SYNC_ENABLED=true on the API.
export const PROJECT_SYNC_ENABLED = (process.env.NEXT_PUBLIC_PROJECT_SYNC || "").trim().toLowerCase() === "on";

// The public commons (goals, search, contributing, Credits and Standing) is off by default: the site is aimed at teams
// evaluating keळ for their own work, and none of those pages is part of that. The routes answer 404 while it is off
// (see app/goals/layout.tsx and friends), so a hidden link is never a reachable page. Turn it back on with
// NEXT_PUBLIC_COMMONS=on.
export const COMMONS_ENABLED = (process.env.NEXT_PUBLIC_COMMONS || "").trim().toLowerCase() === "on";
