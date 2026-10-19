# M5 — External research and polish

Branch: `m5-external-and-polish`. Spec: `docs/superpowers/specs/2026-10-11-omero-completion-design.md`, section 7, M5.

## Provider decision

The spec's default was Gemini with Google Search grounding, to be used only if it is free. On 2026-10-18 the Gemini pricing page lists grounding as "Not available" on the free tier for current Flash models. Only the legacy Gemini 2.5 Flash has a free allowance (500 a day), and legacy models get retired, as `gemini-2.0-flash` already was. The spec's fallback is a search API with a free tier: **Tavily**. It gives 1,000 searches a month with no card, through `POST https://api.tavily.com/search` with a bearer key. The owner adds `TAVILY_API_KEY`; without it, external research is off and the answer says so.

## External research

1. **When.** Per question, after internal synthesis. If the internal answer is `no_data` (no passages, a model that says the passages do not answer, or an extractive answer with no passage sharing a content word with the question), external research runs, but only if all of these hold:
   - the workspace setting is on (`organizations.external_research_enabled`, admin only, off by default; migration 0015);
   - the plan has the `external_delegation` feature (Pro and Enterprise);
   - a provider is configured;
   - the hourly delegation limit (10 per tenant) and the monthly `external_research` quota (Pro 300, Enterprise unlimited) allow it.
2. **What leaves.** Only the question, redacted. Email addresses, phone numbers, URLs, long digit runs, and any number or 4-word phrase that appears in the retrieved internal passages are replaced with `[redacted]`. A question that is mostly redacted is not sent. The message records exactly what was searched.
3. **Answer.** With a model, the answer is written from the web snippets with `[n]` citations, using the same grounded prompt; web text is treated as untrusted. Without a model, the top snippets are quoted. Citations carry `scope: "external"` and the source `url`. The answer is headed "Not in your documents — from the web", so external content is never mistaken for internal.
4. **Limits.** 10 s timeout, 2 retries with backoff, at most 5 results.
5. **Streaming.** The internal no-evidence sentence streams first, then the web section.

## Polish

- Keyboard and screen readers: visible focus rings on every interactive element, labels on icon-only buttons, skip link, `aria-live` for streaming answers and upload progress.
- Small screens: sidebar becomes a menu button below `lg`; research evidence becomes a toggled panel; tables scroll inside their card, not the page.
- Every page: empty, loading and error states (audit and fill gaps).
- First run: a dashboard checklist for a new workspace (upload a document, ask a question, create a project, generate a report) driven by real counts, dismissible.

## Tests

- Unit: redaction, the decision rules, the Tavily client (fake HTTP), the merge of web results, the setting and plan gates.
- HTTP: setting on and off, plan gate, quota, citations stored with `scope: external` and `url`, cross-tenant coverage for the new route.
- Frontend: settings toggle, external evidence card with link, first-run checklist.
- E2E: the journey still passes. Accessibility checked with keyboard-only navigation in the browser.

## Outcome (2026-10-19)

- Web research works end to end behind the setting, plan and quota gates, with redaction, labelled and linked sources, and the search text recorded on the message. It is tested with a fake provider. It needs a live check once the owner adds `TAVILY_API_KEY`.
- Without a model, an answer is now "not enough evidence" when no retrieved passage shares a content word with the question. The evaluation numbers are unchanged.
- A committed axe-core audit (`e2e/pages.spec.ts`) checks 15 pages at desktop and phone width: no WCAG 2 A/AA violations and no sideways scrolling. It found and fixed an invalid ARIA attribute on the search box, low-contrast sidebar labels, a control nested inside the upload drop zone, unlabelled inputs and selects, a top bar that overflowed on phones and hid the menu button, and a Documents toolbar that was cut off.
- First-run checklist on the dashboard, ticked from real progress (`/dashboard` `progress`), hideable per user.
- Not done: a live provider check (no key in the test environment), a reranker, and per-page empty and error states beyond what the audit and existing tests cover.
