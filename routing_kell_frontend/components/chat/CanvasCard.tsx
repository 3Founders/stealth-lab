"use client";
import { createContext, useContext } from "react";
import type { CanvasBlock } from "@/lib/chat/canvas";

/** What a canvas card needs from the room around it: how to open the canvas panel at a title, or at a page that is not a document yet. Provided by RoomView. */
export const CanvasContext = createContext<{ open: (title: string) => void; openPage?: (title: string, html: string) => void | Promise<void> } | null>(null);

/** Where an agent's canvas block was: a card with the title and size. Pressing anywhere on it opens the canvas beside the chat. */
export default function CanvasCard({ block }: { block: CanvasBlock }) {
  const ctx = useContext(CanvasContext);
  const lines = block.text.split("\n").length;
  const open = () => ctx?.open(block.title);
  return (
    <div
      className="dg-file dg-canvas-card" data-clickable={ctx ? "true" : undefined} role="button" tabIndex={ctx ? 0 : -1} aria-label={`Open the canvas ${block.title}`}
      onClick={open} onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); open(); } }}
    >
      <span className="dg-file-kind" aria-hidden="true">{block.kind === "html" ? "PAGE" : block.kind === "code" ? "CODE" : block.kind === "latex" ? "TEX" : "DOC"}</span>
      <span className="dg-file-meta"><b title={block.title}>{block.title}</b><span className="small dim">Canvas · {lines} line{lines === 1 ? "" : "s"}</span></span>
      <span className="dg-artifact-btn dg-card-open" aria-hidden="true">Open</span>
    </div>
  );
}
