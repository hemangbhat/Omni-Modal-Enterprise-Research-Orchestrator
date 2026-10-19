/**
 * The OMERO wordmark: the name in the serif used for documents, with a
 * highlighter stroke under it — the mark a reader leaves on evidence.
 */
export function Wordmark({ size = "md", tagline = true }: { size?: "md" | "lg"; tagline?: boolean }) {
  const text = size === "lg" ? "text-[30px]" : "text-[22px]";
  return (
    <span className="inline-flex flex-col">
      <span className={`relative isolate w-fit self-start font-serif font-semibold leading-none tracking-[-0.02em] text-on-surface ${text}`}>
        omero
        <span
          aria-hidden="true"
          className="absolute -bottom-[0.06em] -left-[0.08em] -z-10 h-[0.42em] w-[calc(100%+0.16em)] -skew-x-6 rounded-[2px] bg-primary-fixed-dim/45"
        />
      </span>
      {tagline ? (
        <span className="mt-[6px] text-[12px] leading-tight text-on-surface-variant">Research from your own documents</span>
      ) : null}
    </span>
  );
}
