// @vitest-environment jsdom
// Pictures in a room are files everyone sees: the composer's picture becomes a File for the room's upload, and a picture file is drawn as
// the picture (other files keep the card).
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import FileCard, { FilesContext } from "@/components/chat/FileCard";
import { pictureFile } from "@/lib/chat/files";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; host?.remove(); });

const render = async (file: Parameters<typeof FileCard>[0]["file"], fetchBlob: (id: string) => Promise<Blob>) => {
  host = document.createElement("div"); document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root!.render(<FilesContext.Provider value={{ fetchBlob }}><FileCard file={file} /></FilesContext.Provider>); });
  await act(async () => { await Promise.resolve(); });
  return host;
};

describe("pictures in a room", () => {
  it("a prepared picture becomes a file with its type, bytes and an extension", async () => {
    const f = pictureFile({ data: btoa("\x89PNG-bytes"), mime: "image/png", name: "screenshot" });
    expect(f.name).toBe("screenshot.png");
    expect(f.type).toBe("image/png");
    expect(f.size).toBe(10);                       // the decoded bytes: 0x89 then "PNG-bytes"
    expect(pictureFile({ data: btoa("x"), mime: "image/jpeg", name: "cat.jpg" }).name).toBe("cat.jpg");
  });

  it("a picture file is shown as the picture; a PDF keeps its card", async () => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:pic", revokeObjectURL: () => undefined }));
    const fetchBlob = vi.fn(async () => new Blob(["x"], { type: "image/png" }));
    const h = await render({ id: "11111111-1111-1111-1111-111111111111", name: "photo.png", size: 2048, mime: "image/png" }, fetchBlob);
    expect(fetchBlob).toHaveBeenCalledWith("11111111-1111-1111-1111-111111111111");
    expect(h.querySelector("img")?.getAttribute("src")).toBe("blob:pic");
    act(() => root?.unmount()); root = null; h.remove();

    const h2 = await render({ id: "22222222-2222-2222-2222-222222222222", name: "report.pdf", size: 2048, mime: "application/pdf" }, fetchBlob);
    expect(h2.querySelector("img")).toBeNull();
    expect(h2.textContent).toContain("report.pdf");
    expect(h2.querySelector("button")?.textContent).toBe("Open");
  });

  it("a picture that can't be fetched falls back to the card", async () => {
    const h = await render({ id: "33333333-3333-3333-3333-333333333333", name: "gone.png", size: 10, mime: "image/png" },
      async () => { throw new Error("expired"); });
    expect(h.querySelector("img")).toBeNull();
    expect(h.textContent).toContain("gone.png");
  });
});
