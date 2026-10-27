"use client";
import { useId, useState } from "react";
import { HelperError, scrub } from "@/lib/words";

/**
 * Where a key is typed, and what is shown afterwards. The value lives only in this component's state while it is being
 * typed; it is sent once, then cleared. It is never put in storage, in the address, or in a message (errors are scrubbed
 * of it). After saving, only the last four characters are shown, and the key cannot be read back.
 */
export default function KeyField({
  envName, isSet, last4, onSave, onRemove, label = "Key",
}: {
  envName: string;
  isSet: boolean;
  last4: string | null;
  onSave: (value: string) => Promise<{ last4: string | null }>;
  onRemove: () => Promise<void>;
  label?: string;
}) {
  const id = useId();
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(!isSet);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(isSet ? last4 : null);
  const [state, setState] = useState<"saved" | "removed" | "idle">(isSet ? "saved" : "idle");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const typed = value.trim();
    if (typed.length < 8) { setError("That looks too short to be a key."); return; }
    setBusy(true); setError(null);
    try {
      const r = await onSave(typed);
      setSaved(r.last4); setState("saved"); setEditing(false);
    } catch (err) {
      setError(scrub(err instanceof HelperError ? err.message : "The key could not be saved.", typed));
    } finally {
      setValue(""); setBusy(false);
    }
  }

  async function remove() {
    setBusy(true); setError(null);
    try { await onRemove(); setSaved(null); setState("removed"); setEditing(true); }
    catch (err) { setError(err instanceof HelperError ? err.message : "The key could not be removed."); }
    finally { setBusy(false); }
  }

  if (!editing && state === "saved") {
    return (
      <div className="stack" data-key-state="saved">
        <p className="small">
          <span className="status" data-s="successful">saved</span>{" "}
          {label} <code>{envName}</code>{saved ? <> ends in <code>{saved}</code></> : null}. It cannot be shown again.
        </p>
        <div className="cluster">
          <button type="button" className="btn-line" onClick={() => { setEditing(true); setError(null); }} disabled={busy}>Replace</button>
          <button type="button" className="btn-line" onClick={remove} disabled={busy}>Remove</button>
        </div>
        {error && <p className="notice" role="alert" data-tone="bad">{error}</p>}
      </div>
    );
  }

  return (
    <form onSubmit={save} className="stack" data-key-state={state === "removed" ? "removed" : "empty"} autoComplete="off">
      {state === "removed" && <p className="small" role="status">Removed. No key is saved for <code>{envName}</code>.</p>}
      <div className="field">
        <label htmlFor={id}>{label} <span className="dim">(kept in <code>{envName}</code> on this machine)</span></label>
        <div className="keyline">
          <input id={id} type="password" autoComplete="new-password" spellCheck={false} autoCapitalize="off"
                 value={value} onChange={(e) => setValue(e.target.value)} aria-describedby={`${id}-hint`} />
          <button type="submit" className="btn-ink" disabled={busy || value.trim().length === 0}>{busy ? "Saving…" : "Save key"}</button>
          {isSet && <button type="button" className="btn-line" onClick={() => { setEditing(false); setValue(""); setError(null); }}>Cancel</button>}
        </div>
        <span id={`${id}-hint`} className="hint">Typed once. After saving, only the last four characters are shown.</span>
      </div>
      {error && <p className="notice" role="alert" data-tone="bad">{error}</p>}
    </form>
  );
}
