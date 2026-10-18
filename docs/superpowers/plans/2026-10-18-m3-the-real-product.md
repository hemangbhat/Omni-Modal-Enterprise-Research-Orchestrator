# M3 The Real Product Implementation Plan

**Goal:** Every page does what its name says, from real data. Projects group documents, research, findings and reports; research can be limited to a project; reports are generated with their citations; the documents, archives, dashboard, search, pricing, billing and admin pages show only real records.

**Spec:** `docs/superpowers/specs/2026-10-11-omero-completion-design.md`, section 7, "M3".

Built on one branch in two parts, each committed when green:

## Part A: projects

New package `omni_modal/projects/` with an in-memory and a Postgres store (selected by `DATABASE_URL`, as before) and a `ProjectService`.

- **Projects** are shared by everyone in the tenant. Researchers and admins create and edit; the creator or an admin archives and restores; auditors read.
- **Links:** documents are added to and removed from a project. A research session can belong to a project (`research_sessions.project_id`); sessions stay private to their owner.
- **Findings and notes:** a finding is saved from a research answer and keeps that answer's citations; a note is free text. Both can be edited and deleted by their author or an admin.
- **Reports** are compiled, not invented: from a project they list its findings (with citations), its decisions, risks, action items, owners and deadlines, and a numbered source list; from a session they list each question and answer with its citations. Markdown, viewed in the app and downloadable. No model writes the report, so every sentence in it comes from saved material.
- **Activity log** per project: created, renamed, archived, restored, document added or removed, finding or note added, report created, session linked.
- **Search within a project:** document titles, findings, notes and report titles.
- **Scoped research:** a session that belongs to a project retrieves only from that project's documents. `QueryRequest.document_ids` carries the scope to both retrievers, and the query cache key includes it.
- **Insights:** extracted entities are now stored in Postgres (`extracted_entities` gains a `label` column, because its old `type` enum does not match the extractor's labels), and a project shows its documents' decisions, risks, action items, owners and deadlines, each linked to its document.

## Part B: the rest of the product

- **Documents:** server-side search on title and file name, filters (status, type, project, uploader), sort (newest, oldest, title, size) and pagination.
- **Archives:** `documents.archived_at`. Archive and restore (uploader or admin). Archived documents are left out of research and the default document list, and are listed on the Archives page. No retention or classification claims.
- **Dashboard:** counts and recent activity from the database only (documents by status, projects, research sessions, this month's usage, recent uploads and project activity).
- **Global search** in the top bar, with its scope stated: document titles, project names and the caller's own research session titles.
- **Pricing page** from the real plan table, reachable signed out.
- **Billing history** from Stripe invoices when Stripe is configured; otherwise the page says history is available once billing is connected.
- **Admin: failed ingestion jobs** for the tenant, with the error and a retry button (admin only).
- **Readiness** also checks object storage and the model provider (a key is configured and the provider answered a model list call recently; cached so the probe stays cheap).

## Schema

`0013_projects.sql`: `projects`, `project_documents`, `project_items`, `project_reports`, `project_activity`; `research_sessions.project_id`; `extracted_entities.label`, nullable `chunk_id` unchanged; `documents.archived_at`. RLS policies in the 0010 shape on each new table.

## Review focus

1. Tenant isolation on every new store method and route, including project links to another tenant's documents or sessions.
2. Scoped research never returns chunks outside the project, including through the cache.
3. Archived documents never appear in research answers.
4. Report content comes only from saved findings, answers and entities.

## Review outcome (2026-10-18)

An independent review of parts A and B found these, all fixed with tests (`test_m3_review_fixes.py`, `test_projects_api.py`):

- Citation runs like `[1][2]` were only partly renumbered; markers are now read as one group in answers and reports.
- Report text from users or the model could add headings (a fake "Sources" list), HTML or remote images; it is now flattened or escaped.
- Entity values could swallow whole unpunctuated paragraphs; spans stop at line breaks and are capped at 300 characters. Owner values keep decimals.
- An empty item edit crashed in Postgres; the Redis worker saved entities to memory only.
- Report downloads failed for non-ASCII titles; the header now carries an ASCII fallback and the exact UTF-8 name.
- The library, dashboard and search ran one query per project; project counts and document status counts are now single queries.
- The readiness probe called the model provider; it now reports the last known state and refreshes it in the background at most every 5 minutes.
- Auditors could attach sessions to projects and write session reports into them; both need project-manage rights, and archived projects refuse new reports.
- Search and dashboard links did nothing when already on the target page, and a slow Documents response could replace newer results; pages now follow the address and only the newest request updates the list.
