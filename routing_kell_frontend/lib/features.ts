// Product features that are switched off by default. Each is read at build time.
//
// Project sync (encrypted upload of a local .stealth/ folder to the account, /account and /account/projects):
// off since 2026-10-08. The local half never registers projects, so the list was always empty. Turn it back on
// with NEXT_PUBLIC_PROJECT_SYNC=on here AND PROJECT_SYNC_ENABLED=true on the API.
export const PROJECT_SYNC_ENABLED = (process.env.NEXT_PUBLIC_PROJECT_SYNC || "").trim().toLowerCase() === "on";
