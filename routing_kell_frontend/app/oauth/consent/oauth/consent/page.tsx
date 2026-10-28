// Supabase builds the consent URL as <Site URL> + <authorization path>. With the dashboard's two settings overlapping
// it sends people to /oauth/consent/oauth/consent?authorization_id=..., which would be a 404 and leave the MCP sign-in
// stuck. Serve the same page there so the flow works whichever way the dashboard is set; once the authorization path
// is /oauth/consent against a Site URL without it, this file is never reached and can be deleted.
export { default } from "../../page";
