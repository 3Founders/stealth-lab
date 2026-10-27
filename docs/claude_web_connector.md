# Connecting claude.ai (Claude web) to kell

claude.ai adds MCP servers as **custom connectors**. Anthropic's cloud makes the
calls, not your browser, so the server must be reachable from the public
internet over HTTPS. `http://127.0.0.1:8765/mcp` (the local default) cannot work.

What already exists server-side (no code change needed): OAuth 2.1 with Supabase
Auth as the authorization server, RFC 9728 protected-resource metadata, dynamic
client registration, PKCE. See `backend/app/mcp_server/oauth_resource.py`.

## One-time server setup

1. Deploy `backend/Dockerfile.mcp-server` (`railway.mcp.json` / `render.yaml`),
   one worker (`--workers 1` is load-bearing).
2. Set on the deployment:
   - `STEALTHLAB_MCP_PUBLIC_URL=https://<your-mcp-host>` (origin only, no path)
   - `SUPABASE_PROJECT_URL` and `SUPABASE_JWT_AUDIENCE` (turns on the OAuth preset)
   - `DATABASE_URL` and the model keys the deployment uses
3. In the Supabase dashboard, enable the OAuth 2.1 server, set the
   authorization path to prod_frontend's `/oauth/consent`, and allow dynamic
   client registration. claude.ai's callback is `https://claude.ai/api/mcp/auth_callback`.
4. Check it from outside your network:
   ```
   curl -i https://<your-mcp-host>/mcp          # 401 + WWW-Authenticate with resource_metadata
   curl https://<your-mcp-host>/.well-known/oauth-protected-resource/mcp
   ```

## Add the connector (per user or per org)

1. claude.ai -> Settings -> Connectors -> **Add custom connector**
   (paid plans; on Team/Enterprise an owner adds it for the org first).
2. Name: `kell`. URL: `https://<your-mcp-host>/mcp`. Leave client ID/secret empty.
3. Click Connect, sign in on the kell consent page, approve.
4. In a chat, open the tools menu and enable `kell`.
5. Ask: "Use kell find_ways for: <your task>".

## Limits

- No hooks on claude.ai: nothing runs automatically. The model has to be told to
  call `find_ways` and `report_result`.
- No repo or shell, so `.kell/claims.md` and test-result capture don't happen.
  Only the knowledge lookup works. Use Claude Code for the full loop.
- Not verified end to end: no public deployment was reachable when this was written,
  so the claude.ai handshake itself is untested.
