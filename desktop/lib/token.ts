// Is the saved kell token usable now? A token file can exist long after its access token expired (the refresh token
// may also be dead), and every route that only checked `!!cfg.token` then said "signed in" while every call answered
// 401. That is what made the app skip the coding sign-in after a rooms sign-in (2026-11-16).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const SKEW_MS = 60_000; // refresh/sign in a minute early, never at the boundary

export const kellHome = () => process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab");

export function readKellConfig(): any {
  try {
    return JSON.parse(fs.readFileSync(path.join(kellHome(), "config.json"), "utf8")) || {};
  } catch {
    return {};
  }
}

/** True when a token exists and is not known to be expired. A token with no expiry claim is treated as live. */
export function tokenLive(cfg: any = readKellConfig()): boolean {
  if (!cfg?.token) return false;
  const expires = Number(cfg.expires_at || 0);
  if (expires) return expires > Date.now() + SKEW_MS;
  try {
    const payload = JSON.parse(Buffer.from(String(cfg.token).split(".")[1], "base64url").toString("utf8")) || {};
    const exp = Number(payload.exp || 0);
    return !exp || exp * 1000 > Date.now() + SKEW_MS;
  } catch {
    return true; // not a JWT: the server decides, and the call that follows says what is wrong
  }
}
