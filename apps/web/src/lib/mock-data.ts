export type DocumentStatus = "uploaded" | "processing" | "ready" | "failed";

export type DocumentRecord = {
  id: string;
  title: string;
  source: string;
  type: "PDF" | "Audio";
  status: DocumentStatus;
  updatedAt: string;
};

export const documents: DocumentRecord[] = [
  {
    id: "doc-001",
    title: "Q2 enterprise buyer interviews",
    source: "buyer-interviews-q2.pdf",
    type: "PDF",
    status: "ready",
    updatedAt: "Today"
  },
  {
    id: "doc-002",
    title: "CFO call transcript",
    source: "cfo-call.wav",
    type: "Audio",
    status: "processing",
    updatedAt: "Today"
  },
  {
    id: "doc-003",
    title: "Competitive landscape packet",
    source: "market-landscape.pdf",
    type: "PDF",
    status: "uploaded",
    updatedAt: "Yesterday"
  },
  {
    id: "doc-004",
    title: "Regional sales sync",
    source: "sales-sync.mp3",
    type: "Audio",
    status: "failed",
    updatedAt: "Yesterday"
  }
];
