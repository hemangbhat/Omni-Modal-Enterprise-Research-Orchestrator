import {
  boolean,
  customType,
  index,
  integer,
  jsonb,
  pgEnum,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
  uuid,
  varchar
} from "drizzle-orm/pg-core";

export const documentStatus = pgEnum("document_status", [
  "uploaded",
  "processing",
  "ready",
  "failed",
  "archived"
]);

export const documentSourceType = pgEnum("document_source_type", [
  "pdf",
  "audio",
  "transcript",
  "note",
  "web"
]);

export const entityType = pgEnum("entity_type", [
  "person",
  "organization",
  "location",
  "product",
  "metric",
  "topic",
  "date",
  "other"
]);

export const auditAction = pgEnum("audit_action", [
  "create",
  "read",
  "update",
  "delete",
  "upload",
  "process",
  "search",
  "export"
]);

export const vector = customType<{
  data: number[];
  driverData: string;
  config: { dimensions: number };
}>({
  dataType(config) {
    return `vector(${config?.dimensions ?? 1536})`;
  },
  toDriver(value) {
    return `[${value.join(",")}]`;
  }
});

export type AccessMetadata = {
  visibility: "private" | "tenant" | "restricted";
  allowedUserIds?: string[];
  allowedRoles?: string[];
  sensitivity?: "public" | "internal" | "confidential" | "restricted";
};

export type DocumentMetadata = {
  originalFileName?: string;
  mimeType?: string;
  byteSize?: number;
  pageCount?: number;
  durationSeconds?: number;
  checksum?: string;
};

export type ChunkMetadata = {
  pageNumber?: number;
  startMs?: number;
  endMs?: number;
  tokenCount?: number;
  headings?: string[];
};

export const users = pgTable(
  "users",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: varchar("tenant_id", { length: 128 }).notNull(),
    email: varchar("email", { length: 320 }).notNull(),
    displayName: text("display_name").notNull(),
    role: varchar("role", { length: 80 }).notNull().default("researcher"),
    isActive: boolean("is_active").notNull().default(true),
    accessMetadata: jsonb("access_metadata")
      .$type<AccessMetadata>()
      .notNull()
      .default({ visibility: "private" }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    tenantIdx: index("users_tenant_idx").on(table.tenantId),
    tenantEmailUnique: uniqueIndex("users_tenant_email_unique").on(
      table.tenantId,
      table.email
    )
  })
);

export const documents = pgTable(
  "documents",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: varchar("tenant_id", { length: 128 }).notNull(),
    ownerId: uuid("owner_id")
      .notNull()
      .references(() => users.id, { onDelete: "restrict" }),
    title: text("title").notNull(),
    sourceType: documentSourceType("source_type").notNull(),
    sourceUri: text("source_uri"),
    status: documentStatus("status").notNull().default("uploaded"),
    language: varchar("language", { length: 16 }).notNull().default("en"),
    accessMetadata: jsonb("access_metadata")
      .$type<AccessMetadata>()
      .notNull()
      .default({ visibility: "private" }),
    metadata: jsonb("metadata").$type<DocumentMetadata>().notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
    processedAt: timestamp("processed_at", { withTimezone: true })
  },
  (table) => ({
    tenantIdx: index("documents_tenant_idx").on(table.tenantId),
    ownerIdx: index("documents_owner_idx").on(table.ownerId),
    statusIdx: index("documents_status_idx").on(table.status),
    sourceTypeIdx: index("documents_source_type_idx").on(table.sourceType)
  })
);

export const documentChunks = pgTable(
  "document_chunks",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: varchar("tenant_id", { length: 128 }).notNull(),
    documentId: uuid("document_id")
      .notNull()
      .references(() => documents.id, { onDelete: "cascade" }),
    chunkIndex: integer("chunk_index").notNull(),
    content: text("content").notNull(),
    contentHash: varchar("content_hash", { length: 128 }).notNull(),
    metadata: jsonb("metadata").$type<ChunkMetadata>().notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    tenantIdx: index("document_chunks_tenant_idx").on(table.tenantId),
    documentIdx: index("document_chunks_document_idx").on(table.documentId),
    contentHashIdx: index("document_chunks_content_hash_idx").on(table.contentHash),
    documentChunkUnique: uniqueIndex("document_chunks_document_index_unique").on(
      table.documentId,
      table.chunkIndex
    )
  })
);

export const embeddings = pgTable(
  "embeddings",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: varchar("tenant_id", { length: 128 }).notNull(),
    documentId: uuid("document_id")
      .notNull()
      .references(() => documents.id, { onDelete: "cascade" }),
    chunkId: uuid("chunk_id")
      .notNull()
      .references(() => documentChunks.id, { onDelete: "cascade" }),
    embedding: vector("embedding", { dimensions: 1536 }).notNull(),
    embeddingModel: text("embedding_model").notNull(),
    dimensions: integer("dimensions").notNull().default(1536),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    tenantIdx: index("embeddings_tenant_idx").on(table.tenantId),
    documentIdx: index("embeddings_document_idx").on(table.documentId),
    chunkIdx: index("embeddings_chunk_idx").on(table.chunkId)
  })
);

export const extractedEntities = pgTable(
  "extracted_entities",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: varchar("tenant_id", { length: 128 }).notNull(),
    documentId: uuid("document_id")
      .notNull()
      .references(() => documents.id, { onDelete: "cascade" }),
    chunkId: uuid("chunk_id").references(() => documentChunks.id, {
      onDelete: "set null"
    }),
    type: entityType("type").notNull(),
    value: text("value").notNull(),
    normalizedValue: text("normalized_value"),
    confidence: integer("confidence"),
    metadata: jsonb("metadata").notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    tenantIdx: index("extracted_entities_tenant_idx").on(table.tenantId),
    documentIdx: index("extracted_entities_document_idx").on(table.documentId),
    chunkIdx: index("extracted_entities_chunk_idx").on(table.chunkId),
    typeValueIdx: index("extracted_entities_type_value_idx").on(table.type, table.value)
  })
);

export const auditLogs = pgTable(
  "audit_logs",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: varchar("tenant_id", { length: 128 }).notNull(),
    actorUserId: uuid("actor_user_id").references(() => users.id, {
      onDelete: "set null"
    }),
    action: auditAction("action").notNull(),
    resourceType: varchar("resource_type", { length: 80 }).notNull(),
    resourceId: uuid("resource_id"),
    ipAddress: varchar("ip_address", { length: 64 }),
    userAgent: text("user_agent"),
    metadata: jsonb("metadata").notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    tenantIdx: index("audit_logs_tenant_idx").on(table.tenantId),
    actorIdx: index("audit_logs_actor_idx").on(table.actorUserId),
    resourceIdx: index("audit_logs_resource_idx").on(
      table.resourceType,
      table.resourceId
    ),
    createdAtIdx: index("audit_logs_created_at_idx").on(table.createdAt)
  })
);

export type User = typeof users.$inferSelect;
export type NewUser = typeof users.$inferInsert;
export type Document = typeof documents.$inferSelect;
export type NewDocument = typeof documents.$inferInsert;
export type DocumentChunk = typeof documentChunks.$inferSelect;
export type NewDocumentChunk = typeof documentChunks.$inferInsert;
export type Embedding = typeof embeddings.$inferSelect;
export type NewEmbedding = typeof embeddings.$inferInsert;
export type ExtractedEntity = typeof extractedEntities.$inferSelect;
export type NewExtractedEntity = typeof extractedEntities.$inferInsert;
export type AuditLog = typeof auditLogs.$inferSelect;
export type NewAuditLog = typeof auditLogs.$inferInsert;
