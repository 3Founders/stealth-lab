// A fresh project on open (owner, 2026-11-08: the app came back into old chats; it should open on nothing).
//
// What the page kept between starts was all localStorage: the folder you had open came back, its sessions with it, and a restore
// raced the settings so old chats flashed. The startup script below (main.ts, "kell:boot") runs before the page's own scripts and
// removes those transient entries, so every run begins on the empty "What's the task?" screen; in the app the origin's storage is
// also wiped when the window closes (main.ts), so nothing piles up. The keys are the page's own (desktop/app/page.tsx).
//
// The recent-project list is NOT one of those transient keys. It lives in ~/.stealthlab/chats.db (app/api/projects), and the old
// code's setItem("kell.projects", "[]") here was mirrored by prefs-init.js back into desktop-prefs.json, permanently erasing the
// list on every start (2026-11-11). Only the folder/session being opened is cleared.
export const BOOT = `(() => {
  try {
    for (const k of ["kell.directory", "kell.layout", "kell.open-session"]) localStorage.removeItem(k)
  } catch {}
})()`
