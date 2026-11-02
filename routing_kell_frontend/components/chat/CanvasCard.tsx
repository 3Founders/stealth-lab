"use client";
import { createContext, useContext } from "react";
import type { CanvasBlock } from "@/lib/chat/canvas";

/** What a canvas card needs from the room around it: how to open the canvas panel at a title. Provided by RoomView. */
export const CanvasContext = createContext<{ open: (title: string) => void } | null>(null);

/** Where an agent's canvas block was: a card with the title and size, and a button to open it beside the chat. */
export default function CanvasCard({ block }: { block: CanvasBlock }) {
  const ctx = useContext(CanvasContext);
  const lines = block.text.split("\n").length;
  return (
    <div className="dg-file dg-canvas-card">
      <span className="dg-file-kind" aria-hidden="true">{block.kind === "html" ? "PAGE" : block.kind === "code" ? "CODE" : "DOC"}</span>
      <span className="dg-file-meta"><b title={block.title}>{block.title}</b><span className="small dim">Canvas · {lines} line{lines === 1 ? "" : "s"}</span></span>
      <button type="button" className="dg-artifact-btn" onClick={() => ctx?.open(block.title)} disabled={!ctx} aria-label={`Open the canvas ${block.title}`}>Open</button>
    </div>
  );
}
