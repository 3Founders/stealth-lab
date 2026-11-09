"use client";
import { useId, useState } from "react";
import { HelperError, scrub } from "@/lib/words";

/**
 * Where a key is typed, and what is shown afterwards. The value lives only in this component's state while it is being
 * typed; it is sent once, then cleared. It is never put in storage, in the address, or in a message (errors are scrubbed
 * of it). After saving, only the last four characters are shown, and the key cannot be read back.
 *
 * There is no "Remove" (2026-11-08): a key that is shown here must never be deletable from the console. Replacing it
 * (typing a new one) is the way to change it; a plan user never sees this component at all (ModelsPanel).
 */
export default function KeyField({
  envName, isSet, last4, onSave, label = "Key",
}: {
  envName: string;
  isSet: boolean;
  last4: string | null;
  onSave: (value: string) => Promise<{ last4: string | null }>;
  label?: string;
}) {
  const id = useId();
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(!isSet);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(isSet ? last4 : null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const typed = value.trim();
    if (typed.length < 8) { setError("That looks too short to be a key."); return; }
    setBusy(true); setError(null);
    try {
      const r = await onSave(typed);
      setSaved(r.last4); setEditing(false);
    } catch (err) {
      setError(scrub(err instanceof HelperError ? err.message : "The key could not be saved.", typed));
    } finally {
      setValue(""); setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="stack" data-key-state="saved">
        <p className="small">
          <span className="status" data-s="successful">saved</span>{" "}
          {label} <code>{envName}</code>{saved ? <> ends in <code>{saved}</code></> : null}. It cannot be shown again.
        </p>
        <div className="cluster">
          <button type="button" className="btn-line" onClick={() => { setEditing(true); setError(null); }} disabled={busy}>Replace</button>
        </div>
        {error && <p className="notice" role="alert" data-tone="bad">{error}</p>}
      </div>
    );
  }

  return (
    <form onSubmit={save} className="stack" data-key-state="empty" autoComplete="off">
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
