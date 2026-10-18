"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AnswerMarkdown } from "@/components/answer-markdown";
import { EvidenceCard } from "@/components/evidence-card";
import { MaterialIcon } from "@/components/material-icon";
import { ReportViewer } from "@/components/report-viewer";
import { TopBar } from "@/components/top-bar";
import { useClaims } from "@/lib/use-claims";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";
import {
  addProjectDocument,
  addProjectItem,
  archiveProject,
  createProjectReport,
  deleteProjectItem,
  describeActivity,
  getProject,
  getReport,
  removeProjectDocument,
  restoreProject,
  searchProject,
  updateProject,
  type InsightLabel,
  type ProjectDetail,
  type ProjectItem,
  type ProjectSearch,
  type Report
} from "@/lib/projects-api";

const INSIGHT_LABELS: [InsightLabel, string][] = [
  ["decision", "Decisions"],
  ["risk", "Risks"],
  ["action_item", "Action items"],
  ["owner", "Owners"],
  ["deadline", "Deadlines"]
];

type LibraryDoc = { document_id: string; title: string; status: string };

function when(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const claims = useClaims();
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await getProject(projectId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "This project could not be loaded.");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(name: string, action: () => Promise<unknown>, reload = true) {
    setBusy(name);
    setActionError(null);
    try {
      await action();
      if (reload) await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "That did not work. Try again.");
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <main className="flex flex-1 flex-col bg-surface p-xl">
        <p role="alert" className="text-error">{error}</p>
        <Link href="/projects" className="mt-md text-primary-fixed-dim hover:underline">Back to projects</Link>
      </main>
    );
  }
  if (!detail) {
    return <main className="flex flex-1 bg-surface p-xl text-on-surface-variant">Loading project…</main>;
  }

  const { project } = detail;
  const archived = project.archived;
  const canManage = (claims?.roles ?? []).some((r) => r === "researcher" || r === "admin");
  const editable = canManage && !archived;
  const findings = detail.items.filter((i) => i.kind === "finding");
  const notes = detail.items.filter((i) => i.kind === "note");

  return (
    <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-surface">
      <TopBar searchPlaceholder="Search documents, projects, research..." />
      <div className="flex-1 overflow-y-auto px-lg py-xl md:px-xl">
        <div className="mx-auto flex max-w-6xl flex-col gap-xl">
          <Header
            detail={detail}
            editable={editable}
            canManage={canManage}
            busy={busy}
            onSave={(fields) => act("save", () => updateProject(projectId, fields))}
            onArchive={() => act("archive", () => (archived ? restoreProject(projectId) : archiveProject(projectId)))}
            onReport={() =>
              act("report", async () => {
                setReport(await createProjectReport(projectId));
              })
            }
          />
          {archived ? (
            <p className="rounded-xl border border-outline-variant/40 bg-surface-container-low px-md py-sm text-[14px] text-on-surface-variant">
              This project is archived and read-only. Restore it to add documents, findings or notes.
            </p>
          ) : null}
          {actionError ? (
            <p role="alert" className="rounded-xl border border-error/30 bg-error-container/10 px-md py-sm text-[14px] text-error">
              {actionError}
            </p>
          ) : null}

          <div className="grid grid-cols-1 gap-xl lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="flex min-w-0 flex-col gap-xl">
              <section aria-labelledby="findings-heading" className="flex flex-col gap-md">
                <div className="flex items-baseline justify-between">
                  <h3 id="findings-heading" className="text-[19px] font-semibold text-on-surface">
                    Findings <span className="text-on-surface-variant">({findings.length})</span>
                  </h3>
                  {editable ? (
                    <Link href={`/research?project=${project.id}`} className="text-[14px] text-primary-fixed-dim hover:underline">
                      Research to find more
                    </Link>
                  ) : null}
                </div>
                {findings.length === 0 ? (
                  <p className="rounded-xl border border-dashed border-outline-variant/40 p-md text-[14px] leading-relaxed text-on-surface-variant">
                    Findings are answers saved from research, with their sources. Ask a question in this project and
                    choose &ldquo;Save to project&rdquo; under an answer worth keeping.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-md">
                    {findings.map((item) => (
                      <ItemCard
                        key={item.id}
                        item={item}
                        canDelete={editable && (item.created_by === claims?.user_id || (claims?.roles ?? []).includes("admin"))}
                        onDelete={() => act(`del-${item.id}`, () => deleteProjectItem(projectId, item.id))}
                      />
                    ))}
                  </ul>
                )}
              </section>

              <section aria-labelledby="notes-heading" className="flex flex-col gap-md">
                <h3 id="notes-heading" className="text-[19px] font-semibold text-on-surface">
                  Notes <span className="text-on-surface-variant">({notes.length})</span>
                </h3>
                {editable ? (
                  <NoteForm
                    busy={busy === "note"}
                    onAdd={(title, body) => act("note", () => addProjectItem(projectId, { kind: "note", title, body }))}
                  />
                ) : null}
                <ul className="flex flex-col gap-sm">
                  {notes.map((item) => (
                    <ItemCard
                      key={item.id}
                      item={item}
                      canDelete={editable && (item.created_by === claims?.user_id || (claims?.roles ?? []).includes("admin"))}
                      onDelete={() => act(`del-${item.id}`, () => deleteProjectItem(projectId, item.id))}
                    />
                  ))}
                </ul>
              </section>

              <section aria-labelledby="reports-heading" className="flex flex-col gap-md">
                <h3 id="reports-heading" className="text-[19px] font-semibold text-on-surface">Reports</h3>
                {detail.reports.length === 0 ? (
                  <p className="text-[14px] text-on-surface-variant">
                    Generate a report to collect this project&apos;s findings, notes, decisions, risks and action items
                    into one Markdown document with a single source list.
                  </p>
                ) : (
                  <ul className="flex flex-col divide-y divide-outline-variant/20 rounded-xl border border-outline-variant/30">
                    {detail.reports.map((r) => (
                      <li key={r.id} className="flex items-center justify-between gap-md px-md py-sm">
                        <span className="min-w-0">
                          <span className="block truncate text-[15px] text-on-surface">{r.title}</span>
                          <span className="text-[12px] text-on-surface-variant">
                            {r.source === "session" ? "From a research session, " : ""}
                            {when(r.created_at)}
                          </span>
                        </span>
                        <button
                          type="button"
                          onClick={() => act(`open-${r.id}`, async () => setReport(await getReport(r.id)), false)}
                          className="shrink-0 rounded-lg border border-outline-variant/40 px-md py-xs text-[13px] text-on-surface hover:bg-surface-container"
                        >
                          Open
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            <aside className="flex min-w-0 flex-col gap-xl">
              <DocumentsPanel
                detail={detail}
                editable={editable}
                busy={busy}
                onAdd={(id) => act("add-doc", () => addProjectDocument(projectId, id))}
                onRemove={(id) => act(`rm-${id}`, () => removeProjectDocument(projectId, id))}
              />
              <InsightsPanel detail={detail} />
              <section aria-labelledby="research-heading" className="flex flex-col gap-sm">
                <h3 id="research-heading" className="text-[15px] font-semibold text-on-surface">Your research here</h3>
                {detail.sessions.length === 0 ? (
                  <p className="text-[14px] text-on-surface-variant">
                    {editable ? (
                      <Link href={`/research?project=${project.id}`} className="text-primary-fixed-dim hover:underline">
                        Ask a question in this project
                      </Link>
                    ) : (
                      "No research sessions yet."
                    )}
                  </p>
                ) : (
                  <ul className="flex flex-col gap-xs">
                    {detail.sessions.map((s) => (
                      <li key={s.id}>
                        <Link href={`/research?session=${s.id}`} className="text-[14px] text-on-surface hover:text-primary-fixed-dim">
                          {s.title}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <SearchPanel projectId={project.id} />
              <section aria-labelledby="activity-heading" className="flex flex-col gap-sm">
                <h3 id="activity-heading" className="text-[15px] font-semibold text-on-surface">Activity</h3>
                <ol className="flex flex-col gap-sm">
                  {detail.activity.map((a) => (
                    <li key={a.id} className="text-[13px] leading-snug text-on-surface-variant">
                      <span className="text-on-surface">{describeActivity(a)}</span>
                      <span className="block text-[12px] text-on-surface-variant/70">{when(a.created_at)}</span>
                    </li>
                  ))}
                </ol>
              </section>
            </aside>
          </div>
        </div>
      </div>
      {report ? <ReportViewer report={report} onClose={() => setReport(null)} /> : null}
    </main>
  );
}

function Header({
  detail,
  editable,
  canManage,
  busy,
  onSave,
  onArchive,
  onReport
}: {
  detail: ProjectDetail;
  editable: boolean;
  canManage: boolean;
  busy: string | null;
  onSave: (fields: { name: string; description: string }) => Promise<void>;
  onArchive: () => Promise<void>;
  onReport: () => Promise<void>;
}) {
  const { project } = detail;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);

  if (editing) {
    return (
      <form
        className="flex flex-col gap-sm"
        onSubmit={(e) => {
          e.preventDefault();
          void onSave({ name, description }).then(() => setEditing(false));
        }}
      >
        <label className="sr-only" htmlFor="project-name">Project name</label>
        <input
          id="project-name"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          className="rounded-lg border border-outline-variant/40 bg-surface px-md py-sm text-[24px] font-semibold text-on-surface"
        />
        <label className="sr-only" htmlFor="project-description">Description</label>
        <textarea
          id="project-description"
          value={description}
          rows={2}
          onChange={(e) => setDescription(e.target.value)}
          className="resize-none rounded-lg border border-outline-variant/40 bg-surface px-md py-sm text-[15px] text-on-surface"
        />
        <div className="flex gap-sm">
          <button type="submit" disabled={!name.trim() || busy === "save"} className="rounded-lg bg-primary-fixed-dim px-md py-xs text-[14px] font-semibold text-on-primary-fixed">
            Save
          </button>
          <button type="button" onClick={() => setEditing(false)} className="px-md text-[14px] text-on-surface-variant">
            Cancel
          </button>
        </div>
      </form>
    );
  }

  return (
    <div className="flex flex-col gap-md">
      <Link href="/projects" className="flex items-center gap-xs text-[14px] text-on-surface-variant hover:text-on-surface">
        <MaterialIcon name="arrow_back" size={16} /> Projects
      </Link>
      <div className="flex flex-col justify-between gap-md md:flex-row md:items-start">
        <div className="min-w-0">
          <h2 className="text-[30px] font-semibold leading-tight tracking-tight text-on-surface">{project.name}</h2>
          {project.description ? (
            <p className="mt-xs max-w-3xl text-[16px] leading-relaxed text-on-surface-variant">{project.description}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-sm">
          {editable ? (
            <Link
              href={`/research?project=${project.id}`}
              className="flex items-center gap-xs rounded-lg bg-primary-fixed-dim px-md py-sm text-[14px] font-semibold text-on-primary-fixed hover:opacity-90"
            >
              <MaterialIcon name="science" size={18} /> Research in this project
            </Link>
          ) : null}
          {canManage ? (
            <button
              type="button"
              disabled={busy === "report"}
              onClick={() => void onReport()}
              className="flex items-center gap-xs rounded-lg border border-outline-variant/40 px-md py-sm text-[14px] text-on-surface hover:bg-surface-container disabled:opacity-50"
            >
              <MaterialIcon name="description" size={18} /> {busy === "report" ? "Generating…" : "Generate report"}
            </button>
          ) : null}
          {editable ? (
            <button type="button" onClick={() => setEditing(true)} className="rounded-lg px-md py-sm text-[14px] text-on-surface-variant hover:text-on-surface">
              Edit
            </button>
          ) : null}
          {canManage ? (
            <button
              type="button"
              disabled={busy === "archive"}
              onClick={() => void onArchive()}
              className="rounded-lg px-md py-sm text-[14px] text-on-surface-variant hover:text-on-surface disabled:opacity-50"
            >
              {project.archived ? "Restore" : "Archive"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ItemCard({ item, canDelete, onDelete }: { item: ProjectItem; canDelete: boolean; onDelete: () => Promise<void> }) {
  const [showSources, setShowSources] = useState(false);
  const [confirming, setConfirming] = useState(false);
  return (
    <li className="rounded-2xl border border-outline-variant/30 bg-surface-container-low p-md">
      <div className="mb-sm flex items-start justify-between gap-md">
        <h4 className="text-[16px] font-semibold leading-snug text-on-surface">{item.title}</h4>
        {canDelete ? (
          confirming ? (
            <span className="flex shrink-0 gap-sm text-[13px]">
              <button type="button" onClick={() => void onDelete()} className="text-error">Delete</button>
              <button type="button" onClick={() => setConfirming(false)} className="text-on-surface-variant">Keep</button>
            </span>
          ) : (
            <button
              type="button"
              aria-label={`Delete ${item.title}`}
              onClick={() => setConfirming(true)}
              className="shrink-0 rounded-md p-xs text-on-surface-variant hover:text-error"
            >
              <MaterialIcon name="delete" size={16} />
            </button>
          )
        ) : null}
      </div>
      {item.body ? (
        <AnswerMarkdown
          markdown={item.body}
          citationCount={item.citations.length}
          onCite={() => setShowSources(true)}
        />
      ) : null}
      {item.citations.length > 0 ? (
        <div className="mt-sm">
          <button
            type="button"
            aria-expanded={showSources}
            onClick={() => setShowSources((v) => !v)}
            className="text-[13px] text-primary-fixed-dim hover:underline"
          >
            {showSources ? "Hide" : "Show"} {item.citations.length} source{item.citations.length === 1 ? "" : "s"}
          </button>
          {showSources ? (
            <div className="mt-sm flex flex-col gap-sm">
              {item.citations.map((c, i) => (
                <EvidenceCard key={`${c.chunk_id}-${i}`} citation={c} index={i + 1} active={false} />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function NoteForm({ busy, onAdd }: { busy: boolean; onAdd: (title: string, body: string) => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  return (
    <form
      className="flex flex-col gap-sm rounded-2xl border border-outline-variant/30 p-md"
      onSubmit={(e) => {
        e.preventDefault();
        if (!title.trim()) return;
        void onAdd(title.trim(), body.trim()).then(() => {
          setTitle("");
          setBody("");
        });
      }}
    >
      <label className="sr-only" htmlFor="note-title">Note title</label>
      <input
        id="note-title"
        value={title}
        maxLength={300}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Add a note"
        className="rounded-lg border border-outline-variant/40 bg-surface px-md py-sm text-[15px] text-on-surface focus:border-primary-fixed-dim focus:outline-none"
      />
      {title.trim() ? (
        <>
          <label className="sr-only" htmlFor="note-body">Note text</label>
          <textarea
            id="note-body"
            value={body}
            rows={3}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Details (optional)"
            className="resize-none rounded-lg border border-outline-variant/40 bg-surface px-md py-sm text-[14px] text-on-surface focus:border-primary-fixed-dim focus:outline-none"
          />
          <button type="submit" disabled={busy} className="self-start rounded-lg bg-primary-fixed-dim px-md py-xs text-[14px] font-semibold text-on-primary-fixed disabled:opacity-50">
            {busy ? "Saving…" : "Save note"}
          </button>
        </>
      ) : null}
    </form>
  );
}

function DocumentsPanel({
  detail,
  editable,
  busy,
  onAdd,
  onRemove
}: {
  detail: ProjectDetail;
  editable: boolean;
  busy: string | null;
  onAdd: (documentId: string) => Promise<void>;
  onRemove: (documentId: string) => Promise<void>;
}) {
  const [library, setLibrary] = useState<LibraryDoc[]>([]);
  const [choice, setChoice] = useState("");
  useEffect(() => {
    if (!editable) return;
    const { baseUrl, token } = getClientApiConfig();
    apiRequest("/documents?page_size=200", { headers: token ? { Authorization: `Bearer ${token}` } : {} }, { baseUrl })
      .then((r) => (r.ok ? r.json() : { documents: [] }))
      .then((data: { documents: LibraryDoc[] }) => setLibrary(data.documents ?? []))
      .catch(() => setLibrary([]));
  }, [editable, detail.documents.length]);
  const linked = useMemo(() => new Set(detail.documents.map((d) => d.document_id)), [detail.documents]);
  const available = library.filter((d) => !linked.has(d.document_id));

  return (
    <section aria-labelledby="documents-heading" className="flex flex-col gap-sm">
      <h3 id="documents-heading" className="text-[15px] font-semibold text-on-surface">
        Documents <span className="text-on-surface-variant">({detail.documents.length})</span>
      </h3>
      {detail.documents.length === 0 ? (
        <p className="text-[14px] leading-relaxed text-on-surface-variant">
          Research here only searches the documents added to the project. Add some from your library.
        </p>
      ) : (
        <ul className="flex flex-col gap-xs">
          {detail.documents.map((d) => (
            <li key={d.document_id} className="group flex items-center justify-between gap-sm text-[14px]">
              <span className="min-w-0 truncate text-on-surface" title={d.title}>{d.title}</span>
              {editable ? (
                <button
                  type="button"
                  disabled={busy === `rm-${d.document_id}`}
                  onClick={() => void onRemove(d.document_id)}
                  aria-label={`Remove ${d.title} from the project`}
                  className="shrink-0 rounded-md p-xs text-on-surface-variant opacity-60 hover:text-error group-hover:opacity-100"
                >
                  <MaterialIcon name="close" size={14} />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {editable && available.length > 0 ? (
        <form
          className="flex gap-xs"
          onSubmit={(e) => {
            e.preventDefault();
            if (choice) void onAdd(choice).then(() => setChoice(""));
          }}
        >
          <label className="sr-only" htmlFor="add-document">Add a document</label>
          <select
            id="add-document"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-outline-variant/40 bg-surface px-sm py-xs text-[14px] text-on-surface"
          >
            <option value="">Add a document…</option>
            {available.map((d) => (
              <option key={d.document_id} value={d.document_id}>{d.title}</option>
            ))}
          </select>
          <button type="submit" disabled={!choice || busy === "add-doc"} className="rounded-lg border border-outline-variant/40 px-sm text-[13px] text-on-surface disabled:opacity-50">
            Add
          </button>
        </form>
      ) : null}
    </section>
  );
}

function InsightsPanel({ detail }: { detail: ProjectDetail }) {
  const any = INSIGHT_LABELS.some(([label]) => (detail.insights[label] ?? []).length > 0);
  return (
    <section aria-labelledby="insights-heading" className="flex flex-col gap-sm">
      <h3 id="insights-heading" className="text-[15px] font-semibold text-on-surface">From the documents</h3>
      {!any ? (
        <p className="text-[14px] leading-relaxed text-on-surface-variant">
          Decisions, risks, action items, owners and deadlines found in this project&apos;s documents appear here.
        </p>
      ) : (
        INSIGHT_LABELS.map(([label, heading]) => {
          const entries = detail.insights[label] ?? [];
          if (entries.length === 0) return null;
          return (
            <div key={label}>
              <h4 className="text-[13px] font-medium text-on-surface-variant">{heading}</h4>
              <ul className="mt-xs flex flex-col gap-xs">
                {entries.slice(0, 8).map((e, i) => (
                  <li key={`${label}-${i}`} className="text-[14px] leading-snug text-on-surface">
                    {e.value}
                    <span className="block text-[12px] text-on-surface-variant/80">{e.document_title}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })
      )}
    </section>
  );
}

function SearchPanel({ projectId }: { projectId: string }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ProjectSearch | null>(null);
  const empty = results && results.documents.length + results.items.length + results.reports.length === 0;
  return (
    <section aria-labelledby="project-search-heading" className="flex flex-col gap-sm">
      <h3 id="project-search-heading" className="text-[15px] font-semibold text-on-surface">Search this project</h3>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) void searchProject(projectId, q.trim()).then(setResults).catch(() => setResults(null));
        }}
      >
        <label className="sr-only" htmlFor="project-search">Search document titles, findings, notes and reports</label>
        <input
          id="project-search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Titles, findings, notes, reports"
          className="w-full rounded-lg border border-outline-variant/40 bg-surface px-md py-xs text-[14px] text-on-surface focus:border-primary-fixed-dim focus:outline-none"
        />
      </form>
      {empty ? <p className="text-[13px] text-on-surface-variant">Nothing matches.</p> : null}
      {results && !empty ? (
        <ul className="flex flex-col gap-xs text-[14px]">
          {results.documents.map((d) => <li key={d.document_id} className="text-on-surface">Document: {d.title}</li>)}
          {results.items.map((i) => (
            <li key={i.id} className="text-on-surface">{i.kind === "finding" ? "Finding" : "Note"}: {i.title}</li>
          ))}
          {results.reports.map((r) => <li key={r.id} className="text-on-surface">Report: {r.title}</li>)}
        </ul>
      ) : null}
    </section>
  );
}
