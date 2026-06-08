import { neon } from "@neondatabase/serverless";
import { requireDatabaseUrl } from "./env.mjs";

const sql = neon(requireDatabaseUrl());

function vectorLiteral(primaryIndex) {
  return `[${Array.from({ length: 1536 }, (_, index) =>
    index === primaryIndex ? "0.12" : "0.001"
  ).join(",")}]`;
}

await sql`
  INSERT INTO users (
    id,
    tenant_id,
    email,
    display_name,
    role,
    access_metadata
  )
  VALUES (
    '11111111-1111-4111-8111-111111111111',
    'tenant-demo',
    'analyst@example.com',
    'Demo Analyst',
    'researcher',
    '{"visibility":"tenant","sensitivity":"internal"}'::jsonb
  )
  ON CONFLICT (tenant_id, email) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    role = EXCLUDED.role,
    access_metadata = EXCLUDED.access_metadata,
    updated_at = now()
`;

await sql`
  INSERT INTO documents (
    id,
    tenant_id,
    owner_id,
    title,
    source_type,
    source_uri,
    status,
    access_metadata,
    metadata,
    processed_at
  )
  VALUES (
    '22222222-2222-4222-8222-222222222222',
    'tenant-demo',
    '11111111-1111-4111-8111-111111111111',
    'Enterprise buyer interviews',
    'pdf',
    'neon://sample/enterprise-buyer-interviews.pdf',
    'ready',
    '{"visibility":"tenant","sensitivity":"confidential","allowedRoles":["researcher","admin"]}'::jsonb,
    '{"originalFileName":"enterprise-buyer-interviews.pdf","mimeType":"application/pdf","pageCount":12}'::jsonb,
    now()
  )
  ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    status = EXCLUDED.status,
    access_metadata = EXCLUDED.access_metadata,
    metadata = EXCLUDED.metadata,
    updated_at = now(),
    processed_at = EXCLUDED.processed_at
`;

const chunks = [
  {
    id: "33333333-3333-4333-8333-333333333333",
    chunkIndex: 0,
    content:
      "Procurement teams reported longer approval cycles for security reviews and vendor risk assessments.",
    contentHash: "sample-chunk-000",
    metadata:
      '{"pageNumber":3,"tokenCount":14,"headings":["Procurement friction"]}'
  },
  {
    id: "33333333-3333-4333-8333-333333333334",
    chunkIndex: 1,
    content:
      "Buyers still prioritized compliance automation, especially where manual evidence collection delayed enterprise deals.",
    contentHash: "sample-chunk-001",
    metadata: '{"pageNumber":4,"tokenCount":15,"headings":["Compliance demand"]}'
  }
];

for (const chunk of chunks) {
  await sql`
    INSERT INTO document_chunks (
      id,
      tenant_id,
      document_id,
      chunk_index,
      content,
      content_hash,
      metadata
    )
    VALUES (
      ${chunk.id},
      'tenant-demo',
      '22222222-2222-4222-8222-222222222222',
      ${chunk.chunkIndex},
      ${chunk.content},
      ${chunk.contentHash},
      ${chunk.metadata}::jsonb
    )
    ON CONFLICT (document_id, chunk_index) DO UPDATE SET
      content = EXCLUDED.content,
      content_hash = EXCLUDED.content_hash,
      metadata = EXCLUDED.metadata
  `;
}

await sql`
  DELETE FROM embeddings
  WHERE document_id = '22222222-2222-4222-8222-222222222222'
`;

const embeddings = [
  {
    chunkId: "33333333-3333-4333-8333-333333333333",
    vector: vectorLiteral(0)
  },
  {
    chunkId: "33333333-3333-4333-8333-333333333334",
    vector: vectorLiteral(1)
  }
];

for (const embedding of embeddings) {
  await sql`
    INSERT INTO embeddings (
      tenant_id,
      document_id,
      chunk_id,
      embedding,
      embedding_model,
      dimensions
    )
    VALUES (
      'tenant-demo',
      '22222222-2222-4222-8222-222222222222',
      ${embedding.chunkId},
      ${embedding.vector}::vector,
      'text-embedding-3-small',
      1536
    )
  `;
}

await sql`
  DELETE FROM extracted_entities
  WHERE document_id = '22222222-2222-4222-8222-222222222222'
`;

await sql`
  INSERT INTO extracted_entities (
    tenant_id,
    document_id,
    chunk_id,
    type,
    value,
    normalized_value,
    confidence
  )
  VALUES (
    'tenant-demo',
    '22222222-2222-4222-8222-222222222222',
    '33333333-3333-4333-8333-333333333333',
    'topic',
    'vendor risk assessment',
    'vendor_risk_assessment',
    92
  )
`;

const results = await sql`
  SELECT
    c.id,
    c.content,
    1 - (e.embedding <=> ${vectorLiteral(0)}::vector) AS similarity
  FROM embeddings e
  INNER JOIN document_chunks c ON c.id = e.chunk_id
  WHERE e.tenant_id = 'tenant-demo'
  ORDER BY e.embedding <=> ${vectorLiteral(0)}::vector
  LIMIT 2
`;

console.log(`Seed complete. Vector search returned ${results.length} chunks.`);
