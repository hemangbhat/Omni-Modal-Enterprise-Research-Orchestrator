"use client";

import { MaterialIcon } from "@/components/material-icon";
import { citationLocation, type Citation } from "@/lib/research-api";

/** One retrieved passage: where it came from and the text that supports the answer. */
export function EvidenceCard({
  citation,
  index,
  active,
  anchor = false
}: {
  citation: Citation;
  index: number;
  active: boolean;
  // The side panel's cards are scroll targets for citation chips.
  anchor?: boolean;
}) {
  const location = citationLocation(citation);
  const audio = citation.source_type === "audio";
  const web = citation.scope === "external";
  const link = web && citation.url && /^https?:\/\//.test(citation.url) ? citation.url : null;
  return (
    <figure
      id={anchor ? `evidence-${index}` : undefined}
      className={`rounded-lg border p-md transition-colors ${
        active
          ? "border-primary-fixed-dim bg-surface-container"
          : citation.cited
            ? "border-outline-variant/60 bg-surface-container-low"
            : "border-outline-variant/30 bg-transparent"
      }`}
    >
      {citation.snippet ? (
        <blockquote
          className={`border-l-[3px] pl-md font-serif text-[15px] leading-[1.65] ${
            citation.cited ? "border-primary-fixed-dim/80 text-on-surface" : "border-outline-variant text-on-surface-variant"
          }`}
        >
          {citation.snippet}
        </blockquote>
      ) : null}
      <figcaption className={`flex items-start gap-sm ${citation.snippet ? "mt-md" : ""}`}>
        <span
          className={`inline-flex h-[22px] min-w-[22px] shrink-0 items-center justify-center rounded font-serif text-[13px] font-semibold ${
            active ? "bg-primary-fixed-dim text-on-primary-fixed" : "border border-outline-variant text-on-surface"
          }`}
        >
          {index}
        </span>
        <span className="flex min-w-0 flex-col gap-[2px]">
          {link ? (
            <a
              href={link}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="truncate text-[14px] font-medium text-primary underline-offset-2 hover:underline"
              title={link}
            >
              {citation.title}
            </a>
          ) : (
            <span className="truncate text-[14px] font-medium text-on-surface" title={citation.title}>
              {citation.title}
            </span>
          )}
          <span className="flex flex-wrap items-center gap-x-md text-[12.5px] text-on-surface-variant">
            <span className="inline-flex items-center gap-[4px]">
              <MaterialIcon name={web ? "public" : audio ? "graphic_eq" : "description"} size={14} />
              {web ? (link ? new URL(link).hostname : "Web") : location ?? (audio ? "Audio" : "Document")}
            </span>
            <span className={web ? "rounded bg-tertiary-container/40 px-1 text-on-surface" : undefined}>
              {web ? "From the web" : "Your workspace"}
            </span>
            {!citation.cited ? <span>Retrieved, not cited</span> : null}
          </span>
        </span>
      </figcaption>
    </figure>
  );
}
