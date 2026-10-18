/**
 * Minimal server-sent-events reader for POST streams (EventSource only does GET).
 * Calls `onEvent(name, data)` for each `event:` / `data:` block whose data is JSON.
 */
export type SseHandler = (event: string, data: Record<string, unknown>) => void;

export function parseSseBlock(block: string): { event: string; data: Record<string, unknown> } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) as Record<string, unknown> };
  } catch {
    return null;
  }
}

export async function readSse(body: ReadableStream<Uint8Array>, onEvent: SseHandler): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    // Normalise the whole buffer, so a CR/LF pair split across two reads still joins.
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const parsed = parseSseBlock(buffer.slice(0, split));
      buffer = buffer.slice(split + 2);
      if (parsed) onEvent(parsed.event, parsed.data);
      split = buffer.indexOf("\n\n");
    }
  }
  const tail = parseSseBlock(buffer.trim());
  if (tail) onEvent(tail.event, tail.data);
}
