// Parses a `text/event-stream` body that arrives in arbitrary chunks. The helper writes `data: <json>\n\n` per event.
// EventSource cannot be used: /chat is a POST with a token header.

export interface SseParser {
  /** Feed the next decoded chunk. Calls onEvent once per complete JSON event. */
  push(chunk: string): void;
  /** Flush what is left when the stream ends. */
  end(): void;
}

export function createSseParser(onEvent: (event: unknown) => void): SseParser {
  let buf = "";
  const dispatch = (block: string) => {
    const data = block
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) return;
    try {
      onEvent(JSON.parse(data));
    } catch {
      // A line that is not JSON is skipped: one bad event must not end the stream.
    }
  };
  return {
    push(chunk) {
      buf += chunk;
      let i: number;
      while ((i = buf.search(/\r?\n\r?\n/)) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i).replace(/^\r?\n\r?\n/, "");
        dispatch(block);
      }
    },
    end() {
      if (buf.trim()) dispatch(buf);
      buf = "";
    },
  };
}

/** Reads a fetch body to the end, calling onEvent as each event completes (not when the stream finishes). */
export async function readSse(body: ReadableStream<Uint8Array>, onEvent: (event: unknown) => void): Promise<void> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  const parser = createSseParser(onEvent);
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(dec.decode(value, { stream: true }));
    }
    parser.push(dec.decode());
    parser.end();
  } finally {
    reader.releaseLock();
  }
}
