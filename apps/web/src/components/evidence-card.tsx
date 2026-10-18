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
  return (
    <figure
      id={anchor ? `evidence-${index}` : undefined}
      className={`rounded-xl border p-md transition-colors ${
        active
          ? "border-primary-fixed-dim bg-primary-fixed-dim/[0.07]"
          : citation.cited
            ? "border-outline-variant/40 bg-surface-container-low"
            : "border-outline-variant/20 bg-transparent opacity-70"
      }`}
    >
      <figcaption className="flex items-start gap-sm">
        <span
          className={`mt-[1px] inline-flex h-[22px] min-w-[22px] items-center justify-center rounded-md font-mono-sm text-[12px] ${
            active ? "bg-primary-fixed-dim text-on-primary-fixed" : "bg-surface-container-high text-primary-fixed-dim"
          }`}
        >
          {index}
        </span>
        <span className="flex min-w-0 flex-col gap-[2px]">
          <span className="truncate text-[14px] font-medium text-on-surface" title={citation.title}>
            {citation.title}
          </span>
          <span className="flex flex-wrap items-center gap-x-sm text-[12px] text-on-surface-variant/80">
            <span className="inline-flex items-center gap-[3px]">
              <MaterialIcon name={audio ? "graphic_eq" : "description"} size={13} />
              {location ?? (audio ? "Audio" : "Document")}
            </span>
            <span>{citation.scope === "external" ? "External source" : "Your workspace"}</span>
            {!citation.cited ? <span>Retrieved, not cited</span> : null}
          </span>
        </span>
      </figcaption>
      {citation.snippet ? (
        <blockquote className="mt-sm border-l-2 border-outline-variant/40 pl-sm text-[13.5px] leading-relaxed text-on-surface-variant">
          {citation.snippet}
        </blockquote>
      ) : null}
    </figure>
  );
}
