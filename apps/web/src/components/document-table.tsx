import { StatusBadge } from "@/components/status-badge";
import type { DocumentRecord } from "@/lib/mock-data";

export function DocumentTable({ documents }: { documents: DocumentRecord[] }) {
  return (
    <div className="overflow-hidden rounded border border-line bg-white">
      <div className="grid grid-cols-[1.4fr_0.7fr_0.8fr_0.8fr] border-b border-line bg-panel px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted">
        <span>Document</span>
        <span>Type</span>
        <span>Status</span>
        <span>Updated</span>
      </div>
      <div className="divide-y divide-line">
        {documents.map((document) => (
          <div
            className="grid grid-cols-[1.4fr_0.7fr_0.8fr_0.8fr] items-center gap-4 px-4 py-4 text-sm"
            key={document.id}
          >
            <div className="min-w-0">
              <p className="truncate font-medium">{document.title}</p>
              <p className="mt-1 truncate text-xs text-muted">{document.source}</p>
            </div>
            <span className="text-muted">{document.type}</span>
            <StatusBadge status={document.status} />
            <span className="text-muted">{document.updatedAt}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
