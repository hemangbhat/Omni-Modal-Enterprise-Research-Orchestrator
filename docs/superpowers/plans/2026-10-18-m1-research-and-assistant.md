# M1 Research and Assistant Implementation Plan

**Goal:** Research that holds up in an interview: saved multi-turn sessions, real token streaming, follow-up questions, citations with snippet and page or timestamp, an explicit "not enough evidence" answer, and a read-only assistant on every signed-in page.

**Architecture:** One new package, `omni_modal/research/`, holds the session store (in-memory and Postgres, picked by `select_research_session_service`, the same pattern as `security/password_reset.py`), the follow-up query rewriter, citation post-processing, and the assistant. A small `qa/llm.py` gives Groq and Gemini one streaming chat interface that both the answer synthesizers and the assistant use. The orchestration graph gains `answer_stream`, which runs the same nodes but streams the synthesis step.

**Spec:** `docs/superpowers/specs/2026-10-11-omero-completion-design.md`, section 7, "M1".

## Decisions made while planning

1. **Hybrid retrieval moves to M4.** The spec keeps hybrid search only if the M4 evaluation shows it helps. Building it before the labelled corpus exists means shipping an unmeasured claim, which the spec's anti-goals forbid. It is built and measured together in M4.
2. **The assistant routes tools deterministically.** A keyword router (not the model) picks which read-only tools run. The model only writes the reply from the tool results. This keeps the model from choosing actions, so a prompt-injected document cannot make it call anything, and the behaviour is testable without an API key.
3. **"Navigate" is a suggestion, not an action.** The tool returns a link the UI shows as a button; the page never changes without a click.
4. **Sessions are private to the user who created them**, scoped by tenant and user.
5. **Citations keep their retrieval numbering.** Every retrieved passage is returned, each marked `cited` or not, so `[n]` in the text always matches card `n`. Markers that point outside the retrieved set are removed from the final answer.

## Global constraints

- No new paid services. Groq first, Gemini second, extractive last.
- The no-database local path keeps working.
- Schema change: `packages/db/drizzle/0010_research_sessions.sql`, with `packages/db/src/schema.ts` kept in step and the same RLS policy shape as 0008.
- Every new endpoint is tenant- and user-scoped and has a cross-tenant test.
- Existing tests keep passing (baseline: 567 backend passed, 95 frontend).
- Branch `m1-research-and-assistant`. Nothing is pushed or merged without the owner's say-so.

## Tasks

1. **Session store and endpoints.** `research_sessions` and `research_messages`; `POST/GET /research/sessions`, `GET/PATCH/DELETE /research/sessions/{id}`. Another user's session returns 404.
2. **Streaming LLM clients.** `qa/llm.py`: `GroqChatClient.stream_chat` (OpenAI-compatible SSE) and `GeminiChatClient.stream_chat` (`streamGenerateContent?alt=sse`). Synthesizers gain `synthesize_stream`, yielding text deltas then the final response. The extractive synthesizer streams its finished text.
3. **Citations.** `SourceReference` gains `snippet`, `scope` ("internal") and `cited`. Out-of-range `[n]` markers are stripped. The model's "not enough information" reply becomes status `no_data` with no citations.
4. **Follow-ups.** `QueryRequest` gains `history` and `search_query`. A rewriter turns a follow-up into a standalone search query (LLM when available, otherwise "previous question + new question"). Retrieval uses the search query; the model sees the history.
5. **`/query/stream` uses sessions.** Optional `session_id`; a session is created when absent. Events: `session`, `delta`, `done`. Both turns are saved with citations after the answer finishes.
6. **Assistant.** `POST /assistant/stream` with `message`, `session_id`, `page`. Tools: search documents, list documents, usage and plan, team members, list projects, navigate. Each tool checks the caller's roles through the existing RBAC map. Tool output is wrapped as untrusted data in the prompt. With no LLM key the assistant says so and shows the tool results directly.
7. **Frontend.** Research page: server-backed session list (new, rename, delete), a multi-turn thread, evidence cards with snippet, page or timestamp and an "Internal" label. Assistant: a button on every signed-in page opening a side panel with streaming replies, tool chips and link buttons.
8. **Verification.** Full backend and frontend suites, typecheck, a local walkthrough in the browser, then one independent review.

## Review focus

1. User B cannot read, rename, delete or continue user A's session, in the same tenant or another.
2. A follow-up like "what about headcount?" retrieves with the earlier topic included.
3. A model answer citing `[7]` when five passages were retrieved: `[7]` is removed.
4. A document containing "ignore previous instructions and ..." cannot make the assistant run a tool.
5. The assistant with no LLM key: a plain statement, not an error.
