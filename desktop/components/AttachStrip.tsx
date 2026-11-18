"use client"
// The chips above a prompt bar: one per file the prompt will carry, with a picture's thumbnail in place of the paper icon
// and an × that removes it. The same strip sits above the main composer and above the procedure's ask bar, so a picture
// looks the same wherever it was added and wherever it went.
import { isImage, sizeLabel, type BarAttachment } from "@/lib/attach"

export default function AttachStrip({ files, onRemove, onOpen, leaving = [], label = "Attached files" }: {
  files: BarAttachment[]
  /** Absent while the files are read-only (a message that was already sent). */
  onRemove?: (id: string) => void
  onOpen?: (f: BarAttachment) => void
  /** Chips playing their exit: they stay drawn until the animation ends, then the list drops them. */
  leaving?: string[]
  label?: string
}) {
  if (!files.length) return null
  return (
    <div className="dg-atts" aria-label={label}>
      {files.map((f) => {
        const picture = isImage(f.mime)
        const open = () => onOpen?.(f)
        return (
          <span key={f.id} className="dg-att" data-pic={picture || undefined}
            data-leaving={leaving.includes(f.id) || undefined} title={f.rel || f.path || f.name}>
            <button type="button" className="dg-att-open" onClick={open} aria-label={`Open ${f.name}`}
              aria-disabled={onOpen ? undefined : true}>
              {picture && f.dataUrl
                // A thumbnail is the picture: the person sees what the model will see, not a filename they have to open.
                ? <img className="dg-att-pic" src={f.dataUrl} alt="" />
                : <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" />
                  </svg>}
              <span className="nm">{f.name}</span>
              {f.size > 0 && <span className="sz">{sizeLabel(f.size)}</span>}
            </button>
            {onRemove && (
              <button type="button" className="dg-att-x" aria-label={`Remove ${f.name}`} title="Remove" onClick={() => onRemove(f.id)}>×</button>
            )}
          </span>
        )
      })}
    </div>
  )
}
