import { sql, type SQL } from "drizzle-orm";

export type VectorSearchInput = {
  tenantId: string;
  queryEmbedding: number[];
  topK?: number;
  minSimilarity?: number;
  ownerId?: string;
  documentIds?: string[];
};

export type ChunkSearchResult = {
  chunkId: string;
  documentId: string;
  title: string;
  sourceType: string;
  chunkIndex: number;
  content: string;
  similarity: number;
  metadata: unknown;
};

export type SearchExecutor = {
  execute<T = unknown>(query: SQL): Promise<T[] | { rows: T[] }>;
};

function vectorLiteral(values: number[]) {
  if (values.length === 0) {
    throw new Error("queryEmbedding must contain at least one dimension.");
  }

  return `[${values.map((value) => Number(value).toString()).join(",")}]`;
}

function normalizeRows<T>(result: T[] | { rows: T[] }) {
  return Array.isArray(result) ? result : result.rows;
}

export async function searchRelevantChunks(
  db: SearchExecutor,
  input: VectorSearchInput
): Promise<ChunkSearchResult[]> {
  const topK = input.topK ?? 5;
  const vector = vectorLiteral(input.queryEmbedding);
  const ownerFilter = input.ownerId
    ? sql`and d.owner_id = ${input.ownerId}`
    : sql``;
  const documentFilter =
    input.documentIds && input.documentIds.length > 0
      ? sql`and d.id in (${sql.join(
          input.documentIds.map((documentId) => sql`${documentId}::uuid`),
          sql`, `
        )})`
      : sql``;

  const rows = await db.execute<ChunkSearchResult>(sql`
    select
      c.id as "chunkId",
      d.id as "documentId",
      d.title,
      d.source_type as "sourceType",
      c.chunk_index as "chunkIndex",
      c.content,
      1 - (e.embedding <=> ${vector}::vector) as similarity,
      c.metadata
    from embeddings e
    inner join document_chunks c on c.id = e.chunk_id
    inner join documents d on d.id = e.document_id
    where e.tenant_id = ${input.tenantId}
      and d.status = 'ready'
      ${ownerFilter}
      ${documentFilter}
      and (${input.minSimilarity ?? 0} = 0
        or 1 - (e.embedding <=> ${vector}::vector) >= ${input.minSimilarity ?? 0})
    order by e.embedding <=> ${vector}::vector
    limit ${topK}
  `);

  return normalizeRows(rows);
}
