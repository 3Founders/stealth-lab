// The public sign-in settings the kell desktop app needs to sign its rooms in with its own session (PKCE, see
// desktop/electron/rooms.ts::signInRooms): the Supabase project URL and the anon key. Both are public by Supabase's own
// design (they already ship in this site's browser bundle; RLS and the backend's token checks are what gate access). No
// secret and no service-role key belongs here.
export const dynamic = "force-static";

export function GET() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
  if (!url || !anonKey) return Response.json({ error: "sign-in is not configured" }, { status: 503 });
  return Response.json({ url, anonKey }, { headers: { "Cache-Control": "public, max-age=300" } });
}
