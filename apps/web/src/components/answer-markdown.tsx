"use client";

import { Fragment, type ReactNode } from "react";

/**
 * Renders the small Markdown subset model answers use (headings, lists,
 * bold, inline code, paragraphs) without HTML injection: everything is
 * rendered as React text. Citation markers like [2] or [1, 3] become buttons
 * that point at the matching evidence card.
 */
type Props = {
  markdown: string;
  citationCount: number;
  activeCitation?: number | null;
  onCite?: (n: number) => void;
};

const INLINE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[\d+(?:\s*,\s*\d+)*\])/g;

function Inline({ text, citationCount, activeCitation, onCite }: Omit<Props, "markdown"> & { text: string }) {
  const parts = text.split(INLINE).filter((p) => p !== "");
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <strong key={i} className="font-semibold text-on-surface">
              {part.slice(2, -2)}
            </strong>
          );
        }
        if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) {
          return <em key={i}>{part.slice(1, -1)}</em>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return (
            <code key={i} className="rounded bg-surface-container-high px-1 py-0.5 font-mono-sm text-[0.9em]">
              {part.slice(1, -1)}
            </code>
          );
        }
        if (/^\[\d/.test(part)) {
          const numbers = part
            .slice(1, -1)
            .split(",")
            .map((n) => Number(n.trim()))
            .filter((n) => n >= 1 && n <= citationCount);
          if (numbers.length === 0) return <Fragment key={i}>{part}</Fragment>;
          return (
            <span key={i} className="whitespace-nowrap">
              {numbers.map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={(e) => {
                    // The answer block itself is clickable; keep this click for the chip.
                    e.stopPropagation();
                    onCite?.(n);
                  }}
                  aria-label={`Show source ${n}`}
                  className={`mx-[2px] inline-flex h-[1.35em] min-w-[1.35em] -translate-y-[1px] items-center justify-center rounded-md border px-1 align-middle font-mono-sm text-[11px] leading-none transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-fixed-dim ${
                    activeCitation === n
                      ? "border-primary-fixed-dim bg-primary-fixed-dim text-on-primary-fixed"
                      : "border-primary-fixed-dim/40 bg-primary-fixed-dim/10 text-primary-fixed-dim hover:bg-primary-fixed-dim/20"
                  }`}
                >
                  {n}
                </button>
              ))}
            </span>
          );
        }
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "p"; text: string };

export function parseBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "p", text: paragraph.join(" ") });
    paragraph = [];
  };
  // Some models cite with full-width brackets (【1】) while streaming.
  const normalised = markdown
    .replace(/\s*【\s*(\d+(?:\s*,\s*\d+)*)\s*】/g, " [$1]")
    .replace(/\s*【[^】]*】/g, "");
  for (const raw of normalised.split("\n")) {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      flush();
    } else if (heading) {
      flush();
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
    } else if (bullet || numbered) {
      flush();
      const kind = bullet ? "ul" : "ol";
      const text = (bullet ?? numbered)![1];
      const last = blocks[blocks.length - 1];
      if (last && last.kind === kind) last.items.push(text);
      else blocks.push({ kind, items: [text] });
    } else {
      paragraph.push(line.trim());
    }
  }
  flush();
  return blocks;
}

export function AnswerMarkdown({ markdown, ...inline }: Props) {
  return (
    <div className="flex flex-col gap-md text-[16px] leading-[1.75] text-on-surface/90">
      {parseBlocks(markdown).map((block, i) => {
        if (block.kind === "heading") {
          return (
            <h3 key={i} className="mt-sm font-headline-md text-[17px] font-semibold text-on-surface">
              <Inline text={block.text} {...inline} />
            </h3>
          );
        }
        if (block.kind === "p") {
          return (
            <p key={i}>
              <Inline text={block.text} {...inline} />
            </p>
          );
        }
        const List = block.kind === "ul" ? "ul" : "ol";
        return (
          <List
            key={i}
            className={`flex flex-col gap-xs pl-lg ${block.kind === "ul" ? "list-disc" : "list-decimal"} marker:text-on-surface-variant/60`}
          >
            {block.items.map((item, j) => (
              <li key={j} className="pl-xs">
                <Inline text={item} {...inline} />
              </li>
            ))}
          </List>
        );
      })}
    </div>
  );
}
