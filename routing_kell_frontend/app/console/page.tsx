import Console from "@/components/console/Console";
import "./console.css";

export const metadata = { title: "Console", robots: { index: false, follow: false } };

/**
 * The MCP console on the website: install kell into your tools, keep model keys, try a model, and watch the router. It talks to a
 * small helper on YOUR machine (`npx -y kell-mcp console`), never to a keळ server, so it works signed out. Opened from the helper's
 * own address (it prints one, and Claude Code opens it for you) the page is served same-origin and just works; opened from here the
 * helper must be started with `--allow-origin <this site>` and the address given as `?api=<helper address>&t=<token>`.
 */
export default function ConsolePage() {
  return <div className="kc"><Console /></div>;
}
