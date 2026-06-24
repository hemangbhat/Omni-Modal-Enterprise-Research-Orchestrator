# Requirements Document

## Introduction

This feature transforms OMERO (Omni-Modal Enterprise Research Orchestrator) from a
single-deployment research orchestrator into a production-grade, multi-tenant SaaS platform
suitable as a defensible portfolio and interview project. It layers SaaS capabilities —
organizations and workspaces, managed sessions, billing, object storage, background jobs,
caching, product analytics, transactional email, AI orchestration, document intelligence,
tenant-isolation security, deployment readiness, and SaaS UI surfaces — on top of the existing
Next.js 16 frontend (`apps/web/`) and Python `BaseHTTPRequestHandler` backend
(`services/api/src/omni_modal/`).

Two constraints govern every requirement in this document and are stated explicitly as
Requirement 1 and Requirement 2 so they are testable rather than aspirational:

1. **Zero paid services.** Every capability MUST be operable using a free-tier or local-first
   path (MinIO for S3, local Redis, Stripe test mode, Resend free tier, PostHog free or
   self-hosted, Neon free tier, Sentry free tier, local sentence-transformers/Whisper/NER).
   No requirement may depend on a paid plan to function.
2. **Preserve the working demo.** Every existing offline/in-memory/demo fallback path MUST keep
   working with no new mandatory configuration. Each new integration is selected by environment
   configuration; when that configuration is absent, the system MUST fall back to a local or
   in-memory equivalent and MUST surface which path is active rather than presenting a fallback
   as a finished integration.

Delivery is phased. Each requirement carries a **Delivery Phase** tag:

- **Phase 1 (Implement Now)** — highest value, lowest risk, preserves the working demo:
  foundational SaaS data model and the capabilities that make the existing UI genuinely
  functional. Requirements 1–9, 16.
- **Phase 2 (Defer)** — valuable but higher risk or dependent on external integrations:
  billing, email, analytics, orchestration migration, deeper document intelligence, full
  deployment hardening. Requirements 10–15.

Honesty rule: no scaffold may be reported as a finished feature. Where a capability is a
fallback or an optional path, the system and the documentation MUST label it as such.

---

## Glossary

- **OMERO_Platform**: The complete system comprising the Web_App and the API_Server.
- **API_Server**: The Python HTTP service at `services/api/src/omni_modal/main.py`.
- **Web_App**: The Next.js 16 frontend under `apps/web/`.
- **Operator**: The person deploying and configuring an OMERO_Platform instance.
- **Tenant**: An isolated organisational unit; every persisted data row carries a `tenant_id`.
- **Organization**: A billing-and-ownership boundary that owns one or more Workspaces and maps
  one-to-one to a Tenant.
- **Workspace**: A named collaboration container within an Organization that owns documents,
  projects, and archives. The active Workspace scopes all data a Member sees.
- **Member**: A user account associated with an Organization through a Membership.
- **Membership**: The record linking a Member to an Organization with exactly one Role.
- **Role**: One of `owner`, `admin`, `researcher`, or `auditor`, extending the existing RBAC
  roles (`researcher`, `admin`, `auditor`) with an `owner` role for Organization ownership.
- **Identity_Service**: The backend component that issues, verifies, and revokes Member sessions
  and resolves the active Organization and Workspace for a request.
- **Session**: An authenticated context for a Member, carrying `tenant_id`, `user_id`,
  `organization_id`, `workspace_id`, and `roles`.
- **Demo_Token**: The existing build-time JWT bearer token (`NEXT_PUBLIC_API_TOKEN`) that powers
  the one-click local demo sign-in.
- **Cache_Service**: The backend caching component, backed by Redis when configured and by the
  existing in-process cache (`QueryCache`) otherwise.
- **Object_Storage_Service**: The backend component that stores and retrieves uploaded file
  bytes, backed by an S3-compatible store (MinIO/S3) when configured and by the local
  filesystem otherwise.
- **Job_Queue**: The background job component, backed by Redis when configured and by the
  existing `AsyncIngestionQueue` in-process worker otherwise.
- **Audit_Service**: The component that records security- and billing-relevant events, backed by
  a Postgres `audit_logs` table when `DATABASE_URL` is set and by `InMemoryAuditSink` otherwise.
- **Observability_Service**: The Sentry integration already wired in `observability.py`.
- **Billing_Service**: The backend component integrating Stripe (test mode) for plans,
  subscriptions, and usage limits.
- **Email_Service**: The backend component sending transactional email via Resend when
  configured and logging the rendered message locally otherwise.
- **Analytics_Service**: The product-analytics integration sending events to PostHog (cloud free
  tier or self-hosted) when configured and dropping events silently otherwise.
- **Orchestration_Service**: The AI research orchestration component; today the
  `InternalResearchAdkWorkflow`/`DeterministicAgentGraph`, targeted for an optional LangGraph
  execution path.
- **Document_Intelligence_Service**: The components that derive structure from documents
  (transcription, entity extraction, and added classification/summarisation).
- **Usage_Counter**: The per-Organization tally of metered actions (for example documents
  ingested, queries run) within a billing period.
- **Plan**: A named tier (for example `free`, `pro`) defining quota limits for an Organization.
- **Quota**: A numeric limit on a metered action for a Plan within a billing period.
- **Fallback_Path**: A local or in-memory implementation used when an external integration is
  not configured.

---

## Requirements

### Requirement 1: Zero Paid Services

**Delivery Phase:** Phase 1 (cross-cutting; applies to all requirements)

**User Story:** As an Operator running a portfolio project, I want every capability to work on a
free-tier or local path, so that I can run and demonstrate the full platform without any paid
subscription.

#### Acceptance Criteria

1. THE OMERO_Platform SHALL start and serve all Phase 1 capabilities when configured with only
   free-tier or local services.
2. WHERE a capability integrates an external service, THE OMERO_Platform SHALL provide a
   documented free-tier or local configuration that exercises that capability.
3. IF an external integration requires credentials that are absent, THEN THE OMERO_Platform
   SHALL activate the corresponding Fallback_Path instead of failing to start.
4. THE OMERO_Platform SHALL document, for each external integration, the free-tier or local
   option and the steps to obtain any required credential.

### Requirement 2: Preserve Offline and Demo Fallbacks

**Delivery Phase:** Phase 1 (cross-cutting; applies to all requirements)

**User Story:** As an Operator, I want the existing offline demo to keep working unchanged, so
that the platform always runs out of the box without new mandatory setup.

#### Acceptance Criteria

1. WHERE no SaaS environment variables are configured, THE OMERO_Platform SHALL run the existing
   in-memory end-to-end path (upload, persist, retrieve, query) without error.
2. WHEN a Member signs in using the Demo_Token, THE Identity_Service SHALL grant an authenticated
   Session.
3. THE API_Server SHALL preserve every existing HTTP endpoint contract documented in
   `main.py` for callers that do not supply SaaS-specific fields.
4. WHEN a Fallback_Path is active for a capability, THE OMERO_Platform SHALL expose the active
   path name through the `/health` response.
5. IF an integration marked as a Fallback_Path is presented in the Web_App, THEN THE Web_App
   SHALL label that capability as a fallback or local path rather than as a configured
   integration.

### Requirement 3: Organizations and Workspaces

**Delivery Phase:** Phase 1

**User Story:** As a Member, I want my work grouped into an Organization and Workspaces, so that
multiple teams and projects can be isolated within one account.

#### Acceptance Criteria

1. WHEN a Member account is first created, THE Identity_Service SHALL create one Organization
   owned by that Member and one default Workspace within that Organization.
2. THE OMERO_Platform SHALL map each Organization to exactly one Tenant via the existing
   `tenant_id`.
3. WHEN a Member with the `owner` or `admin` Role requests Workspace creation within an
   Organization, THE Identity_Service SHALL create a Workspace scoped to that Organization.
4. WHILE a Workspace is the active Workspace for a Session, THE API_Server SHALL scope document,
   project, and archive reads and writes to that Workspace.
5. IF a request references a Workspace that is not owned by the Session's Organization, THEN THE
   API_Server SHALL respond with HTTP 403 and record an Audit_Service event.
6. WHERE no Workspace is specified on a request, THE API_Server SHALL use the Session's default
   Workspace.

### Requirement 4: Workspace Switcher UI

**Delivery Phase:** Phase 1

**User Story:** As a Member, I want a workspace switcher in the Web_App, so that I can change
which Workspace I am viewing.

#### Acceptance Criteria

1. THE Web_App SHALL display a workspace switcher showing the Workspaces the Session's Member can
   access.
2. WHEN a Member selects a Workspace from the switcher, THE Web_App SHALL set that Workspace as
   the active Workspace for subsequent API_Server requests.
3. WHEN the active Workspace changes, THE Web_App SHALL refresh the document, project, and
   archive views to reflect the selected Workspace.
4. IF the Session's Member belongs to exactly one Workspace, THEN THE Web_App SHALL display that
   Workspace name without offering a switch action.

### Requirement 5: Managed Sessions and Membership

**Delivery Phase:** Phase 1

**User Story:** As a Member, I want a managed sign-in that remembers my Organization and Role, so
that I can access my Workspaces securely across sessions.

#### Acceptance Criteria

1. WHEN a Member presents valid credentials, THE Identity_Service SHALL issue a Session carrying
   `tenant_id`, `user_id`, `organization_id`, `workspace_id`, and `roles`.
2. WHEN the API_Server receives a request with a Session token, THE Identity_Service SHALL verify
   the token signature and expiry before any handler runs.
3. IF a Session token is missing, malformed, or expired, THEN THE API_Server SHALL respond with
   HTTP 401 and record an Audit_Service event.
4. WHEN a Member with the `owner` or `admin` Role invites a user to the Organization, THE
   Identity_Service SHALL create a Membership with the specified Role.
5. THE Identity_Service SHALL resolve a request's permitted actions from the Membership Role
   using the existing RBAC role checks.
6. WHERE the Demo_Token is presented, THE Identity_Service SHALL grant a Session bound to the
   demo Organization and default Workspace.

### Requirement 6: Team Management UI

**Delivery Phase:** Phase 1

**User Story:** As an Organization owner, I want a team management page, so that I can view
Members and manage their Roles.

#### Acceptance Criteria

1. WHILE a Session holds the `owner` or `admin` Role, THE Web_App SHALL display a team management
   page listing each Member and that Member's Role.
2. WHEN an owner or admin changes a Member's Role, THE API_Server SHALL update the Membership and
   record an Audit_Service event.
3. WHEN an owner or admin invites a user by email address, THE API_Server SHALL create a pending
   Membership and request a Phase 2 invitation email through the Email_Service.
4. IF a Member without the `owner` or `admin` Role requests the team management page, THEN THE
   Web_App SHALL deny access and the API_Server SHALL respond with HTTP 403.
5. IF an owner attempts to remove the last remaining `owner` of an Organization, THEN THE
   API_Server SHALL reject the change with HTTP 409 and record an Audit_Service event.

### Requirement 7: Object Storage for Uploads

**Delivery Phase:** Phase 1

**User Story:** As a Member, I want uploaded files stored in object storage, so that ingestion
works reliably across restarts and multiple workers.

#### Acceptance Criteria

1. WHERE S3-compatible storage is configured, THE Object_Storage_Service SHALL store uploaded
   file bytes in the configured bucket scoped by `tenant_id` and `document_id`.
2. WHERE S3-compatible storage is not configured, THE Object_Storage_Service SHALL store uploaded
   file bytes on the local filesystem, preserving the existing upload behaviour.
3. WHEN ingestion processes an uploaded document, THE Job_Queue SHALL retrieve the file bytes
   from the Object_Storage_Service using the stored object reference.
4. IF an object reference cannot be retrieved, THEN THE Job_Queue SHALL mark the ingestion job
   failed with a descriptive error code and record an Audit_Service event.
5. THE Object_Storage_Service SHALL accept file sizes up to the existing `MAX_BODY_BYTES` limit
   and reject larger uploads with HTTP 413.

### Requirement 8: Redis Caching with Fallback

**Delivery Phase:** Phase 1

**User Story:** As an Operator, I want query results and hot data cached in Redis when available,
so that the platform scales across workers while still running locally without Redis.

#### Acceptance Criteria

1. WHERE Redis is configured, THE Cache_Service SHALL store and retrieve query results from Redis
   keyed by `tenant_id`, `workspace_id`, and the query parameters.
2. WHERE Redis is not configured, THE Cache_Service SHALL use the existing in-process query
   cache.
3. WHEN a document is ingested into a Workspace, THE Cache_Service SHALL evict cached query
   results for that Workspace.
4. IF the configured Redis backend is unreachable, THEN THE Cache_Service SHALL fall back to the
   in-process cache and record the degradation through the Observability_Service.
5. THE Cache_Service SHALL return results that are equivalent whether served from Redis or the
   in-process cache for the same inputs.

### Requirement 9: Background Jobs and Queue

**Delivery Phase:** Phase 1

**User Story:** As an Operator, I want ingestion and other long-running work processed as
background jobs, so that the platform can scale workers and survive restarts.

#### Acceptance Criteria

1. WHEN a Member uploads a document, THE API_Server SHALL enqueue an ingestion job and respond
   with HTTP 202 and a job identifier, preserving the existing contract.
2. WHERE a Redis-backed queue is configured, THE Job_Queue SHALL persist enqueued jobs in Redis
   so that a worker process can claim and process them.
3. WHERE a Redis-backed queue is not configured, THE Job_Queue SHALL use the existing in-process
   `AsyncIngestionQueue` worker.
4. WHEN a job's status is requested by identifier, THE API_Server SHALL return the job status as
   `queued`, `processing`, `ready`, or `failed`.
5. IF a job fails, THEN THE Job_Queue SHALL record the failure with an error code retrievable
   through the job status endpoint.
6. WHILE a job is retried after a transient failure, THE Job_Queue SHALL apply the existing
   exponential-backoff retry policy.

### Requirement 10: Stripe Billing in Test Mode

**Delivery Phase:** Phase 2 (Defer)

**User Story:** As an Organization owner, I want subscription billing through Stripe test mode,
so that the platform demonstrates plan management without processing real payments.

#### Acceptance Criteria

1. WHERE Stripe test-mode keys are configured, THE Billing_Service SHALL create a Stripe customer
   for an Organization on first subscription.
2. WHEN an owner selects a Plan, THE Billing_Service SHALL create a Stripe test-mode subscription
   and associate it with the Organization.
3. WHEN the Billing_Service receives a Stripe webhook, THE Billing_Service SHALL verify the
   webhook signature before updating subscription state.
4. IF a webhook signature is invalid, THEN THE Billing_Service SHALL reject the webhook with
   HTTP 400 and record an Audit_Service event.
5. WHERE Stripe is not configured, THE Billing_Service SHALL assign every Organization the `free`
   Plan and label billing as a local fallback in the Web_App.
6. WHEN an Organization's Usage_Counter reaches a Plan Quota, THE Billing_Service SHALL signal the
   quota state to the API_Server for enforcement under Requirement 16.

### Requirement 11: Billing and Usage UI

**Delivery Phase:** Phase 2 (Defer)

**User Story:** As an Organization owner, I want a billing page and usage dashboard, so that I can
see my Plan, usage, and limits.

#### Acceptance Criteria

1. WHILE a Session holds the `owner` Role, THE Web_App SHALL display a billing page showing the
   Organization's current Plan and subscription status.
2. THE Web_App SHALL display a usage dashboard showing each metered action's Usage_Counter value
   and the corresponding Plan Quota for the current billing period.
3. WHEN an owner initiates a Plan change, THE Web_App SHALL direct the owner to a Stripe
   test-mode checkout flow.
4. WHERE Stripe is not configured, THE Web_App SHALL display the `free` Plan and label billing as
   a local fallback.
5. WHEN usage data is requested, THE Web_App SHALL display values sourced from the API_Server
   rather than placeholder figures.

### Requirement 12: Transactional Email

**Delivery Phase:** Phase 2 (Defer)

**User Story:** As a Member, I want transactional emails for invitations and key events, so that
the platform communicates important actions.

#### Acceptance Criteria

1. WHERE Resend is configured, THE Email_Service SHALL send transactional email through Resend.
2. WHERE Resend is not configured, THE Email_Service SHALL render the message and write it to the
   application log instead of sending it.
3. WHEN an owner or admin invites a user to an Organization, THE Email_Service SHALL send or log
   an invitation message containing the Organization name and an acceptance link.
4. IF an email send attempt fails, THEN THE Email_Service SHALL record the failure through the
   Observability_Service without failing the originating request.

### Requirement 13: Product Analytics

**Delivery Phase:** Phase 2 (Defer)

**User Story:** As an Operator, I want product-usage events captured in PostHog, so that I can
analyse how the platform is used.

#### Acceptance Criteria

1. WHERE PostHog is configured, THE Analytics_Service SHALL send product events identified by
   Organization and Member to PostHog.
2. WHERE PostHog is not configured, THE Analytics_Service SHALL drop events without error.
3. WHEN a Member completes a tracked action, THE Analytics_Service SHALL capture an event naming
   the action and the active Workspace.
4. THE Analytics_Service SHALL exclude document contents and personally identifying document data
   from analytics event properties.

### Requirement 14: LangGraph Orchestration Path

**Delivery Phase:** Phase 2 (Defer)

**User Story:** As an Operator, I want an optional LangGraph orchestration path, so that research
queries can run through a graph-based agent workflow while the deterministic path remains
available.

#### Acceptance Criteria

1. WHERE the LangGraph orchestration path is enabled, THE Orchestration_Service SHALL execute
   research queries through the LangGraph workflow.
2. WHERE the LangGraph orchestration path is not enabled, THE Orchestration_Service SHALL use the
   existing deterministic agent graph.
3. THE Orchestration_Service SHALL return query responses in the existing response contract
   regardless of which orchestration path executed.
4. IF the LangGraph path raises an error during execution, THEN THE Orchestration_Service SHALL
   fall back to the deterministic path and record the failure through the Observability_Service.

### Requirement 15: Document Intelligence

**Delivery Phase:** Phase 2 (Defer)

**User Story:** As a Member, I want documents automatically classified and summarised, so that I
can understand a document without reading all of it.

#### Acceptance Criteria

1. WHEN ingestion completes for a document, THE Document_Intelligence_Service SHALL produce a
   document classification and a short summary using local models.
2. WHERE a local intelligence model is unavailable, THE Document_Intelligence_Service SHALL use
   the existing rule-based extraction and label the output as a fallback.
3. WHEN a Member requests a document's details, THE API_Server SHALL return the classification,
   summary, and extracted entities for that document.
4. THE Document_Intelligence_Service SHALL scope all derived data to the document's Workspace and
   Tenant.

### Requirement 16: Tenant Isolation, Quotas, and Audit

**Delivery Phase:** Phase 1

**User Story:** As an Operator, I want strict tenant isolation, quota enforcement, and a durable
audit trail, so that the platform is defensibly secure and accountable.

#### Acceptance Criteria

1. WHEN the API_Server resolves any data request, THE API_Server SHALL filter results to the
   Session's `tenant_id` and active Workspace.
2. IF a Cross_Tenant resource is requested, THEN THE API_Server SHALL respond with HTTP 403 and
   record an Audit_Service event.
3. WHERE `DATABASE_URL` is configured, THE Audit_Service SHALL persist audit events to the
   `audit_logs` table.
4. WHERE `DATABASE_URL` is not configured, THE Audit_Service SHALL record audit events through
   the in-memory audit sink.
5. WHEN a metered action is performed, THE API_Server SHALL increment the Organization's
   Usage_Counter for that action.
6. IF a metered action would exceed the Organization's Plan Quota, THEN THE API_Server SHALL
   reject the action with HTTP 429 and record an Audit_Service event.
7. THE Audit_Service SHALL exclude secret values and raw document contents from recorded audit
   events.

### Requirement 17: SaaS UI Surfaces

**Delivery Phase:** Phase 1 (notification center, onboarding, admin controls); billing surfaces deferred to Phase 2 per Requirement 11

**User Story:** As a Member, I want onboarding, notifications, and admin controls in the Web_App,
so that the platform is genuinely usable rather than a set of disconnected pages.

#### Acceptance Criteria

1. WHEN a Member signs in for the first time, THE Web_App SHALL present an onboarding flow that
   guides creation of a first Workspace and a first document upload.
2. THE Web_App SHALL display a notification center showing events sourced from the API_Server,
   such as completed ingestions and Membership changes.
3. WHILE a Session holds the `owner` or `admin` Role, THE Web_App SHALL display admin controls for
   Members, Workspaces, and Organization settings.
4. IF a Member without the `owner` or `admin` Role requests an admin control, THEN THE Web_App
   SHALL hide the control and the API_Server SHALL respond with HTTP 403 to any direct request.
5. THE Web_App SHALL source every displayed value from the API_Server and SHALL NOT present
   placeholder data as live data.

### Requirement 18: Deployment Readiness

**Delivery Phase:** Phase 2 (Defer)

**User Story:** As an Operator, I want the platform deployable with documented free-tier
infrastructure, so that I can host a live instance for demonstration.

#### Acceptance Criteria

1. THE OMERO_Platform SHALL provide a `docker-compose` configuration that starts the API_Server,
   a local Redis, and a local MinIO instance for end-to-end local operation.
2. THE OMERO_Platform SHALL document deployment of the Web_App to a free-tier host and the
   API_Server to a free-tier or local host.
3. WHEN the deployment configuration starts, THE OMERO_Platform SHALL pass a `/health` check
   reporting the active path for each capability.
4. THE OMERO_Platform SHALL document every environment variable, marking each as required,
   required-if, optional, or fallback.
5. IF a required environment variable is absent at startup, THEN THE API_Server SHALL log a
   descriptive error naming the missing variable.
