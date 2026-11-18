// Attachments for every prompt bar: what the composer chip shows, and how a picked, dropped or pasted file becomes bytes
// the model can read. Two ways, and the difference is what the engine receives (app/api/prompt :: filesOf):
//   - a text file inside the project stays a path reference (the agent reads it itself, cheapest),
//   - an image is always sent as bytes: a data: URL from the browser, or the file read back through /api/attach/read when
//     the drop came from the OS with a path and the project is somewhere else. An image's path is not its picture.
//
// Images are downscaled before they leave the browser. Vision models see a picture best around 1568 px on the long edge,
// and every byte of a data: URL is paid for on every later turn of the chat that carries it, so a 4 MB phone photo is
// made ~200 KB and keeps everything the model reads. GIF, SVG and PNG under the line are left exactly as they are: a GIF
// loses its animation when it is drawn to a canvas, and an SVG is drawn by the provider from its own source.
export type BarAttachment = { id: string; name: string; size: number; mime: string; path?: string; dataUrl?: string; rel?: string }

export const MAX_ATTACH_BYTES = 25 * 1024 * 1024
export const MAX_ATTACHMENTS = 10
/** The long edge a picture is reduced to; the size the vision models are trained on, and above which they see no more. */
export const IMAGE_MAX_EDGE = 1568
/** The byte size a data: URL may reach before the picture is re-encoded (12 MB per file is /api/prompt's own limit). */
const IMAGE_MAX_BYTES = 1_500_000

export const isImage = (mime: string) => /^image\//i.test(mime)

export const sizeLabel = (bytes: number) =>
  bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`

export const fileToDataUrl = (f: Blob) => new Promise<string>((resolve, reject) => {
  const r = new FileReader()
  r.onload = () => resolve(String(r.result || ""))
  r.onerror = () => reject(r.error)
  r.readAsDataURL(f)
})

/** An image's data: URL, downscaled when it is big enough to be worth it. Anything that cannot be drawn is passed through. */
export async function imageDataUrl(file: Blob, mime: string): Promise<string> {
  const original = await fileToDataUrl(file)
  const keep = /gif|svg/i.test(mime) || !/^image\/(png|jpe?g|webp)$/i.test(mime)
  if (keep || file.size <= IMAGE_MAX_BYTES) return original
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, IMAGE_MAX_EDGE / Math.max(bitmap.width, bitmap.height))
    const w = Math.max(1, Math.round(bitmap.width * scale))
    const h = Math.max(1, Math.round(bitmap.height * scale))
    const canvas = document.createElement("canvas")
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext("2d")
    if (!ctx) return original
    ctx.drawImage(bitmap, 0, 0, w, h)
    bitmap.close?.()
    const out = canvas.toDataURL("image/jpeg", 0.86)
    // A re-encode that somehow grew the picture is not worth the quality it lost.
    return out.length < original.length * 0.95 ? out : original
  } catch {
    return original
  }
}
