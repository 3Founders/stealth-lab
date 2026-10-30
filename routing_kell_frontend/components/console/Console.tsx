"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import ConnectPanel from "@/components/console/ConnectPanel";
import CopyCommand from "@/components/console/CopyCommand";
import ModelsPanel from "@/components/console/ModelsPanel";
import StatusBar from "@/components/console/StatusBar";
import PlaygroundPanel from "@/components/console/PlaygroundPanel";
import ActivityPanel from "@/components/console/ActivityPanel";
import { HelperClient, connectFromLocation } from "@/lib/console/helper";
import type { ClientInfo } from "@/lib/console/types";
import { HelperError } from "@/lib/console/words";

type Gate =
  | { kind: "checking" }
  | { kind: "no-helper" }
  | { kind: "no-token" | "forbidden"; message: string }
  | { kind: "ready"; client: HelperClient; clients: ClientInfo[] };

const TABS = [
  { id: "connect", label: "Connect" },
  { id: "models", label: "Keys & models" },
  { id: "playground", label: "Playground" },
  { id: "activity", label: "Router activity" },
] as const;
type TabId = (typeof TABS)[number]["id"];
const isTab = (v: string): v is TabId => TABS.some((t) => t.id === v);

/** The two commands that start the helper. Fixed text: the page never knows or guesses an address. */
export const HELPER_COMMANDS = {
  installed: "npx -y kell-mcp console",
  checkout: "node packaging/npm/bin/kell.mjs console --ui-dir console/out",
};

export default function Console() {
  const [gate, setGate] = useState<Gate>({ kind: "checking" });
  const [tab, setTab] = useState<TabId>("connect");
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const connect = useCallback(async () => {
    setGate({ kind: "checking" });
    const client = new HelperClient(connectFromLocation());
    if (!(await client.health())) { setGate({ kind: "no-helper" }); return; }
    try {
      setGate({ kind: "ready", client, clients: await client.clients() });
    } catch (e) {
      if (e instanceof HelperError && (e.kind === "unauthorized" || e.kind === "forbidden")) setGate({ kind: e.kind === "unauthorized" ? "no-token" : "forbidden", message: e.message });
      else setGate({ kind: "no-helper" });
    }
  }, []);

  useEffect(() => { void connect(); }, [connect]);

  useEffect(() => {
    const fromHash = () => { const h = window.location.hash.replace("#", ""); if (isTab(h)) setTab(h); };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  const choose = (id: TabId) => { setTab(id); window.history.replaceState(null, "", `#${id}`); };
  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const n = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    choose(TABS[n].id);
    tabRefs.current[TABS[n].id]?.focus();
  };

  return (
    <div className="frame grid console-main">
      <header className="console-head">
        <div className="marker caption"><b>CONSOLE</b><span>/ on this machine</span></div>
        <h1>Set up kell, keep your keys, try a model.</h1>
        <p className="lead dim">This page talks to a small helper running on your computer. It installs kell into your tools, keeps your model keys in a file only you can read, and streams replies from the models you add.</p>
      </header>

      {gate.kind === "checking" && <p className="panel small dim" role="status">Looking for the helper…</p>}

      {gate.kind === "no-helper" && (
        <section className="panel" aria-labelledby="no-helper">
          <div className="card">
            <h2 id="no-helper">The helper is not running</h2>
            <p>This page needs the local helper. Start it in a terminal, then open the address it prints (it carries a one-time token).</p>
            <div className="stack" style={{ marginTop: 14 }}>
              <p className="small dim">Installed:</p>
              <CopyCommand command={HELPER_COMMANDS.installed} label="Start the helper, installed" />
              <p className="small dim">From a checkout of the repository:</p>
              <CopyCommand command={HELPER_COMMANDS.checkout} label="Start the helper, from a checkout" />
            </div>
            <div className="cluster" style={{ marginTop: 16 }}>
              <button type="button" className="btn-ink" onClick={() => void connect()}>Check again</button>
            </div>
          </div>
        </section>
      )}

      {(gate.kind === "no-token" || gate.kind === "forbidden") && (
        <section className="panel" aria-labelledby="no-token">
          <div className="card">
            <h2 id="no-token">{gate.kind === "no-token" ? "This page has no valid token" : "The helper refused this page"}</h2>
            <p>{gate.message}</p>
            <div className="cluster" style={{ marginTop: 16 }}>
              <button type="button" className="btn-ink" onClick={() => void connect()}>Check again</button>
            </div>
          </div>
        </section>
      )}

      {gate.kind === "ready" && (
        <>
          <StatusBar client={gate.client} />
          <div role="tablist" aria-label="Console" className="tabs">
            {TABS.map((t, i) => (
              <button key={t.id} id={`tab-${t.id}`} role="tab" type="button" aria-selected={tab === t.id} aria-controls={`panel-${t.id}`}
                      tabIndex={tab === t.id ? 0 : -1} ref={(el) => { tabRefs.current[t.id] = el; }}
                      onClick={() => choose(t.id)} onKeyDown={(e) => onTabKey(e, i)}>
                {t.label}
              </button>
            ))}
          </div>
          {tab === "connect" && <ConnectPanel client={gate.client} initial={gate.clients} />}
          {tab === "models" && <ModelsPanel client={gate.client} />}
          {tab === "playground" && <PlaygroundPanel client={gate.client} />}
          {tab === "activity" && <ActivityPanel client={gate.client} />}
        </>
      )}
    </div>
  );
}
