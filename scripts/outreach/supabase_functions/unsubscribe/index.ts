// Supabase Edge Function: one-click unsubscribe for the outreach list.
//
//   deploy:  supabase functions deploy unsubscribe --no-verify-jwt        (the link is opened by mail programs, which have no Supabase login)
//   secret:  supabase secrets set UNSUBSCRIBE_SECRET=<the same value as UNSUBSCRIBE_SECRET in backend/.env>
//   url:     https://<project>.supabase.co/functions/v1/unsubscribe       -> UNSUBSCRIBE_URL in backend/.env
//
// POST (RFC 8058 one-click, or the page's button)  -> marks the address unsubscribed.
// GET (a person's browser, or a link scanner)       -> shows a page with one button; it changes nothing.
// A token that does not verify gets the same page text with no action, so nothing leaks about who is on the list.
import { verify } from "./token.mjs";

const URL_ = Deno.env.get("SUPABASE_URL")!;
const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SECRET = Deno.env.get("UNSUBSCRIBE_SECRET")!;
const FINAL = ["unsubscribed", "bounced", "do_not_contact"];

const rest = (path: string, init: RequestInit = {}) =>
  fetch(`${URL_}/rest/v1/${path}`, { ...init, headers: { apikey: KEY, Authorization: `Bearer ${KEY}`, "Content-Type": "application/json", ...(init.headers || {}) } });

const page = (body: string, status = 200) =>
  new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Unsubscribe</title>` +
    `<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem">${body}</body>`,
    { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });

async function unsubscribe(email: string) {
  const got = await (await rest(`outreach_contacts?select=status&email=eq.${encodeURIComponent(email)}`)).json();
  if (got.length && FINAL.includes(got[0].status)) return;             // already final: nothing to change (the table's trigger forbids it anyway)
  if (!got.length) {
    await rest("outreach_contacts?on_conflict=email", { method: "POST", headers: { Prefer: "resolution=ignore-duplicates" },
      body: JSON.stringify({ email, source: "unsubscribe link", status: "new", created_at: new Date().toISOString() }) });
  }
  await rest(`outreach_contacts?email=eq.${encodeURIComponent(email)}`, { method: "PATCH",
    body: JSON.stringify({ status: "unsubscribed", final_at: new Date().toISOString(), final_reason: "unsubscribe link" }) });
  await rest("outreach_events", { method: "POST", body: JSON.stringify({ at: new Date().toISOString(), email, kind: "unsubscribed", detail: "unsubscribe link" }) });
}

Deno.serve(async (req) => {
  const token = new URL(req.url).searchParams.get("t") || "";
  const email = await verify(SECRET, token);
  if (req.method === "POST") {
    if (!email) return page("<p>This link is not valid.</p>", 400);
    await unsubscribe(email);
    return page("<h1>You are unsubscribed</h1><p>You will not hear from us again.</p>");
  }
  if (req.method === "GET") {
    if (!email) return page("<p>This link is not valid. You can also reply to the email with the word unsubscribe.</p>", 400);
    return page(`<h1>Unsubscribe</h1><p>Stop all email from us to <b>${email.replace(/[<>&"]/g, "")}</b>?</p>` +
      `<form method="POST" action=""><button type="submit" style="font-size:1rem;padding:.6rem 1.2rem">Unsubscribe</button></form>`);
  }
  return new Response("Method not allowed", { status: 405 });
});
