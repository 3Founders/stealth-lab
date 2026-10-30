/**
 * The maths of the profile-photo cropper. The picture is shown "cover-fit" inside a square view (so the view is always full), then
 * zoomed and moved by the person. `x`/`y` are how far the picture's centre sits from the view's centre, in view pixels. Nothing here
 * touches the page, so it can be tested directly; components/PhotoCropper.tsx draws it and handles the pointer.
 */
export const VIEW = 260;        // the on-screen crop square, in px
export const OUT = 256;         // the saved picture's side, in px (sharp at the 96px profile size on a 2x screen)
export const MIN_ZOOM = 1;
export const MAX_ZOOM = 4;

export interface Crop { zoom: number; x: number; y: number }
export const START: Crop = { zoom: 1, x: 0, y: 0 };

/** Pixels on screen per pixel of picture at zoom 1: the shorter side just fills the view. */
export const baseScale = (w: number, h: number, view: number = VIEW): number => view / Math.min(w, h);

export const clampZoom = (z: number): number => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number.isFinite(z) ? z : 1));

/** Keeps the view full: the picture can be moved only until its edge meets the view's edge. */
export function clamp(c: Crop, w: number, h: number, view: number = VIEW): Crop {
  const zoom = clampZoom(c.zoom);
  const s = baseScale(w, h, view) * zoom;
  const mx = Math.max(0, (w * s - view) / 2), my = Math.max(0, (h * s - view) / 2);
  return { zoom, x: Math.min(mx, Math.max(-mx, c.x)), y: Math.min(my, Math.max(-my, c.y)) };
}

/** Zooms about the middle of the view, keeping the picture inside it. */
export function zoomTo(c: Crop, zoom: number, w: number, h: number, view: number = VIEW): Crop {
  const z = clampZoom(zoom), k = z / c.zoom;
  return clamp({ zoom: z, x: c.x * k, y: c.y * k }, w, h, view);
}

/** The square of the original picture the view is showing, in the picture's own pixels. */
export function sourceRect(c: Crop, w: number, h: number, view: number = VIEW): { sx: number; sy: number; side: number } {
  const k = clamp(c, w, h, view);
  const s = baseScale(w, h, view) * k.zoom;
  const side = view / s;
  return { sx: w / 2 - k.x / s - side / 2, sy: h / 2 - k.y / s - side / 2, side };
}
