"use client";

type StreamingMarkdownProps = {
  content: string;
  isStreaming: boolean;
};

function renderLine(line: string, index: number) {
  if (line.startsWith("### ")) {
    return (
      <h4 className="mt-4 text-base font-semibold" key={index}>
        {line.replace("### ", "")}
      </h4>
    );
  }

  if (line.startsWith("- ")) {
    return (
      <li className="ml-5 list-disc text-sm leading-6 text-ink" key={index}>
        {line.replace("- ", "")}
      </li>
    );
  }

  if (!line.trim()) {
    return <div className="h-2" key={index} />;
  }

  return (
    <p className="text-sm leading-6 text-ink" key={index}>
      {line}
    </p>
  );
}

export function StreamingMarkdown({ content, isStreaming }: StreamingMarkdownProps) {
  // Content is streamed incrementally by the caller (real SSE deltas), so we
  // render it directly rather than simulating a typewriter effect.
  const hasContent = content.trim().length > 0;

  return (
    <div className="min-h-80 rounded border border-line bg-white p-5">
      <div className="mb-4 flex items-center justify-between border-b border-line pb-3">
        <h3 className="text-sm font-semibold">Streaming response</h3>
        <span className="text-xs font-medium text-muted">
          {isStreaming ? "Streaming" : "Ready"}
        </span>
      </div>
      <div className="space-y-1">
        {hasContent ? (
          content.split("\n").map(renderLine)
        ) : (
          <p className="text-sm leading-6 text-muted">
            {isStreaming
              ? "Waiting for the first tokens..."
              : "Submit a prompt to see a streamed answer."}
          </p>
        )}
        {isStreaming ? (
          <span className="inline-block h-4 w-2 animate-pulse bg-accent align-middle" />
        ) : null}
      </div>
    </div>
  );
}
