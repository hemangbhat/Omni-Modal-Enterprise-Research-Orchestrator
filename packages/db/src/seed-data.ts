import type {
  NewDocument,
  NewDocumentChunk,
  NewEmbedding,
  NewExtractedEntity,
  NewUser
} from "./schema";

export const sampleUsers: NewUser[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    tenantId: "tenant-demo",
    email: "analyst@example.com",
    displayName: "Demo Analyst",
    role: "researcher",
    accessMetadata: {
      visibility: "tenant",
      sensitivity: "internal"
    }
  }
];

export const sampleDocuments: NewDocument[] = [
  {
    id: "22222222-2222-4222-8222-222222222222",
    tenantId: "tenant-demo",
    ownerId: "11111111-1111-4111-8111-111111111111",
    title: "Enterprise buyer interviews",
    sourceType: "pdf",
    sourceUri: "neon://sample/enterprise-buyer-interviews.pdf",
    status: "ready",
    accessMetadata: {
      visibility: "tenant",
      sensitivity: "confidential",
      allowedRoles: ["researcher", "admin"]
    },
    metadata: {
      originalFileName: "enterprise-buyer-interviews.pdf",
      mimeType: "application/pdf",
      pageCount: 12
    }
  }
];

export const sampleDocumentChunks: NewDocumentChunk[] = [
  {
    id: "33333333-3333-4333-8333-333333333333",
    tenantId: "tenant-demo",
    documentId: "22222222-2222-4222-8222-222222222222",
    chunkIndex: 0,
    content:
      "Procurement teams reported longer approval cycles for security reviews and vendor risk assessments.",
    contentHash: "sample-chunk-000",
    metadata: {
      pageNumber: 3,
      tokenCount: 14,
      headings: ["Procurement friction"]
    }
  },
  {
    id: "33333333-3333-4333-8333-333333333334",
    tenantId: "tenant-demo",
    documentId: "22222222-2222-4222-8222-222222222222",
    chunkIndex: 1,
    content:
      "Buyers still prioritized compliance automation, especially where manual evidence collection delayed enterprise deals.",
    contentHash: "sample-chunk-001",
    metadata: {
      pageNumber: 4,
      tokenCount: 15,
      headings: ["Compliance demand"]
    }
  }
];

const sampleVector = Array.from({ length: 1536 }, (_, index) =>
  index === 0 ? 0.12 : 0.001
);

export const sampleEmbeddings: NewEmbedding[] = [
  {
    tenantId: "tenant-demo",
    documentId: "22222222-2222-4222-8222-222222222222",
    chunkId: "33333333-3333-4333-8333-333333333333",
    embedding: sampleVector,
    embeddingModel: "text-embedding-3-small",
    dimensions: 1536
  },
  {
    tenantId: "tenant-demo",
    documentId: "22222222-2222-4222-8222-222222222222",
    chunkId: "33333333-3333-4333-8333-333333333334",
    embedding: sampleVector.map((value, index) => (index === 1 ? 0.12 : value)),
    embeddingModel: "text-embedding-3-small",
    dimensions: 1536
  }
];

export const sampleExtractedEntities: NewExtractedEntity[] = [
  {
    tenantId: "tenant-demo",
    documentId: "22222222-2222-4222-8222-222222222222",
    chunkId: "33333333-3333-4333-8333-333333333333",
    type: "topic",
    value: "vendor risk assessment",
    normalizedValue: "vendor_risk_assessment",
    confidence: 92
  }
];
