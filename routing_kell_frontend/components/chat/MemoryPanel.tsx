"use client";
import { useCallback, useEffect, useState } from "react";
import { knowledgePath, titleOf, type MemoryApi, type MemoryOverview } from "@/lib/chat/memory-api";

const INDEX = "MEMORY.md";
const INDEX_MAX = 2000;
const KNOWLEDGE_MAX_BYTES = 200 * 1024;
const KNOWLEDGE_MAX_FILES = 20;

/**
 * Settings > Memory and knowledge. Memory is off until you turn it on. When it is on, kell always knows a short index (MEMORY.md) and finds
 * the topic, note and knowledge files a question needs. Everything is stored on keळ's server (not end-to-end encrypted, like rooms), every
 * change is a version you can undo, and "Delete all and turn off" removes it. Connected agents (Claude, Cursor...) can use it only if the
 * box below is ticked; it is ticked by default and shown here at all times.
 */
export default function MemoryPanel({ api }: { api: MemoryApi }) {
  const [ov, setOv] = useState<MemoryOverview | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [share, setShare] = useState(true);                       // the choice shown before memory is turned on
  const [edit, setEdit] = useState<{ path: string; body: string; version: number } | null>(null);
  const [versions, setVersions] = useState<Array<{ version: number; by: string; reason: string; at: string }> | null>(null);

  const load = useCallback(async () => {
    try { const o = await api.overview(); setOv(o); setShare(o.settings.share_agents); setErr(""); }
    catch (e) { setErr(e instanceof Error ? e.message : "Couldn't load memory."); }
  }, [api]);
  useEffect(() => { void load(); }, [load]);

  const flash = (t: string) => { setNote(t); window.setTimeout(() => setNote(""), 3500); };
  const guard = async (fn: () => Promise<void>) => { setBusy(true); setErr(""); try { await fn(); } catch (e) { setErr(e instanceof Error ? e.message : "That didn't work."); } finally { setBusy(false); } };

  const open = (path: string) => guard(async () => {
    try { const f = await api.read(path); setEdit({ path: f.path, body: f.body, version: f.version }); }
    catch (e) { if ((e as { status?: number }).status === 404) setEdit({ path, body: "", version: 0 }); else throw e; }
    setVersions(null);
  });

  const save = () => guard(async () => {
    if (!edit) return;
    const r = await api.write(edit.path, edit.body, edit.version, "edited by you");
    setEdit({ ...edit, version: r.version });
    flash(r.unchanged ? "Nothing changed." : "Saved. You can undo it from History.");
    await load();
  });

  const showHistory = () => guard(async () => { if (edit) setVersions((await api.versions(edit.path)).versions); });
  const restore = (v: number) => guard(async () => { if (!edit) return; await api.revert(edit.path, v); await open(edit.path); flash(`Restored version ${v} as the newest.`); await load(); });

  const addKnowledge = (files: FileList | null) => guard(async () => {
    if (!files) return;
    let have = ov?.files.filter((f) => f.path.startsWith("knowledge/")).length ?? 0;
    for (const f of Array.from(files)) {
      if (have >= KNOWLEDGE_MAX_FILES) throw new Error(`Knowledge keeps at most ${KNOWLEDGE_MAX_FILES} files.`);
      if (f.size > KNOWLEDGE_MAX_BYTES) throw new Error(`${f.name} is over ${KNOWLEDGE_MAX_BYTES / 1024} KB. Split it into smaller files.`);
      if (!/\.(txt|md|markdown|csv|json)$/i.test(f.name)) throw new Error(`${f.name}: only text files (.txt, .md, .csv, .json) for now.`);
      await api.write(knowledgePath(f.name), await f.text(), undefined, "added as knowledge");
      have += 1;
    }
    flash("Added. kell looks in your knowledge files when a question seems to need them.");
    await load();
  });

  if (!ov) return <section><h3>Memory and knowledge</h3>{err ? <p className="dg-err" role="alert">{err}</p> : <p className="small dim">Loading…</p>}</section>;
  const on = ov.settings.enabled;
  const files = ov.files;
  const topics = files.filter((f) => f.path !== INDEX && !f.path.startsWith("knowledge/") && !f.path.startsWith("notes/"));
  const knowledge = files.filter((f) => f.path.startsWith("knowledge/"));
  const notes = files.filter((f) => f.path.startsWith("notes/"));

  return (
    <section>
      <h3>Memory and knowledge</h3>
      {err && <p className="dg-err" role="alert">{err}</p>}
      {note && <p className="small" role="status">{note}</p>}

      {!on ? (
        <>
          <p className="dg-set-lead">kell can remember what matters to you across your chats, so you don&rsquo;t have to repeat it. It is off until you turn it on.</p>
          <ul className="dg-about">
            <li><b>What is saved.</b> Short facts kell picks up (a preference, a person, a project), your notes and any knowledge files you add. You see all of it, edit it, undo any change and delete it.</li>
            <li><b>Where.</b> On keळ&rsquo;s server, like your rooms, so it follows you to every device and kell can tidy it in the background. It is not end-to-end encrypted, and secrets are cut out before saving.</li>
            <li><b>Temporary chats</b> never read or save memory.</li>
          </ul>
          <label className="dg-set-row">
            <input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} />
            <span><b>Let my connected agents use my memory</b><small>Claude, Cursor and other agents you connected can read it and add to it. Untick to keep it to kell in this chat. You can change this any time.</small></span>
          </label>
          <button type="button" className="btn-ink dg-mini-btn" disabled={busy} onClick={() => void guard(async () => { await api.setSettings({ enabled: true, share_agents: share }); await load(); flash("Memory is on."); })}><span>Turn on memory</span></button>
        </>
      ) : (
        <>
          <label className="dg-set-row">
            <input type="checkbox" checked={ov.settings.share_agents} disabled={busy} onChange={(e) => void guard(async () => { await api.setSettings({ share_agents: e.target.checked }); await load(); })} />
            <span><b>Let my connected agents use my memory</b><small>{ov.settings.share_agents ? "On: Claude, Cursor and your other connected agents can read and add to it." : "Off: only kell in this chat uses it."}</small></span>
          </label>

          <div className="dg-set-cards">
            <div><b>What kell knows</b><small>The short index kell reads in every conversation ({INDEX_MAX} characters).</small><button type="button" className="btn-line dg-mini-btn" onClick={() => void open(INDEX)}>Open</button></div>
            <div><b>Tidy up</b><small>{ov.settings.last_dream_at ? `Last tidied ${new Date(ov.settings.last_dream_at).toLocaleString()}. ` : "Not tidied yet. "}kell merges duplicates and brings old facts up to date, about every few hours when there is something new. Every change keeps the old version.</small><button type="button" className="btn-line dg-mini-btn" disabled={busy} onClick={() => void guard(async () => { const r = await api.dream(true); await load(); flash(r.changed?.length ? `Tidied: ${r.changed.join(", ")}` : "Nothing needed changing."); })}>Tidy now</button></div>
          </div>

          {ov.dreams.length > 0 && (
            <details className="small"><summary>Recent tidy-ups</summary>
              <ul className="dg-about">{ov.dreams.map((d) => <li key={d.id}>{new Date(d.at).toLocaleString()}: {d.note}</li>)}</ul>
            </details>
          )}

          {(topics.length > 0 || notes.length > 0) && (
            <>
              <h4>Topics and notes</h4>
              <ul className="dg-about">
                {[...topics, ...notes].map((f) => (
                  <li key={f.path}><button type="button" className="dg-linkbtn" onClick={() => void open(f.path)}>{f.path}</button> <span className="small dim">v{f.version}</span></li>
                ))}
              </ul>
            </>
          )}

          <h4>Knowledge files</h4>
          <p className="small">Text files kell may look in when a question seems to need them (guides, notes, reference). Up to {KNOWLEDGE_MAX_FILES} files of {KNOWLEDGE_MAX_BYTES / 1024} KB.</p>
          <ul className="dg-about">
            {knowledge.length === 0 && <li className="dim">None yet.</li>}
            {knowledge.map((f) => (
              <li key={f.path}><button type="button" className="dg-linkbtn" onClick={() => void open(f.path)}>{titleOf(f.path)}</button> <span className="small dim">{Math.ceil(f.size / 1024)} KB</span>{" "}
                <button type="button" className="dg-linkbtn" onClick={() => { if (window.confirm(`Remove ${titleOf(f.path)} from your knowledge?`)) void guard(async () => { await api.remove(f.path); await load(); }); }}>Remove</button></li>
            ))}
          </ul>
          <label className="btn-line dg-mini-btn" style={{ cursor: "pointer" }}>Add files
            <input type="file" multiple hidden accept=".txt,.md,.markdown,.csv,.json,text/plain,text/markdown" onChange={(e) => { const f = e.target.files; e.target.value = ""; void addKnowledge(f); }} />
          </label>

          {edit && (
            <div className="dg-memory-edit">
              <h4>{edit.path}</h4>
              {edit.path === INDEX && <p className="small dim">One fact per line, the current truth, each with where it came from and when. {edit.body.length} / {INDEX_MAX}</p>}
              <textarea value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} rows={10} aria-label={`Edit ${edit.path}`} spellCheck={false} />
              <div className="dg-set-cards">
                <button type="button" className="btn-ink dg-mini-btn" disabled={busy || (edit.path === INDEX && edit.body.length > INDEX_MAX)} onClick={() => void save()}><span>Save</span></button>
                <button type="button" className="btn-line dg-mini-btn" onClick={() => void showHistory()}>History</button>
                <button type="button" className="btn-line dg-mini-btn" onClick={() => { setEdit(null); setVersions(null); }}>Close</button>
              </div>
              {versions && (
                <ul className="dg-about">
                  {versions.map((v) => <li key={v.version}>v{v.version} · {v.by} · {new Date(v.at).toLocaleString()}{v.reason ? ` · ${v.reason}` : ""} <button type="button" className="dg-linkbtn" onClick={() => void restore(v.version)}>Restore</button></li>)}
                </ul>
              )}
            </div>
          )}

          <div className="dg-set-cards">
            <div><b>Turn off</b><small>Stops kell and your agents from using it. Nothing is deleted.</small><button type="button" className="btn-line dg-mini-btn" disabled={busy} onClick={() => void guard(async () => { await api.setSettings({ enabled: false }); await load(); })}>Turn off</button></div>
            <div><b>Delete all and turn off</b><small>Removes every memory, note, knowledge file and version from keळ&rsquo;s server. This can&rsquo;t be undone.</small><button type="button" className="btn-line dg-mini-btn" disabled={busy} onClick={() => { if (window.confirm("Delete all of your memory, notes and knowledge files from keळ's server? This can't be undone.")) void guard(async () => { await api.deleteAll(); setEdit(null); await load(); flash("Deleted. Memory is off."); }); }}>Delete all</button></div>
          </div>
        </>
      )}
    </section>
  );
}
