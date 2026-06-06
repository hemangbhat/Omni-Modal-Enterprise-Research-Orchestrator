import {
  customType,
  index,
  jsonb,
  pgTable,
  text,
  timestamp,
  uuid
} from "drizzle-orm/pg-core";

const vector = customType<{ data: number[]; driverData: string; config: { dimensions: number } }>({
  dataType(config) {
    return `vector(${config?.dimensions ?? 1536})`;
  },
  toDriver(value) {
    return `[${value.join(",")}]`;
  }
});

export const researchDocuments = pgTable(
  "research_documents",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: text("tenant_id").notNull(),
    sourceType: text("source_type").notNull(),
    sourceUri: text("source_uri"),
    title: text("title").notNull(),
    bodyText: text("body_text"),
    metadata: jsonb("metadata").notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    tenantIdx: index("research_documents_tenant_idx").on(table.tenantId),
    sourceIdx: index("research_documents_source_idx").on(table.sourceType)
  })
);

export const extractedEntities = pgTable(
  "extracted_entities",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    documentId: uuid("document_id")
      .notNull()
      .references(() => researchDocuments.id),
    tenantId: text("tenant_id").notNull(),
    label: text("label").notNull(),
    value: text("value").notNull(),
    confidence: text("confidence"),
    metadata: jsonb("metadata").notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    documentIdx: index("extracted_entities_document_idx").on(table.documentId),
    tenantLabelIdx: index("extracted_entities_tenant_label_idx").on(
      table.tenantId,
      table.label
    )
  })
);

export const documentEmbeddings = pgTable(
  "document_embeddings",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    documentId: uuid("document_id")
      .notNull()
      .references(() => researchDocuments.id),
    tenantId: text("tenant_id").notNull(),
    embedding: vector("embedding", { dimensions: 1536 }).notNull(),
    modelName: text("model_name").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow()
  },
  (table) => ({
    documentIdx: index("document_embeddings_document_idx").on(table.documentId),
    tenantIdx: index("document_embeddings_tenant_idx").on(table.tenantId)
  })
);
