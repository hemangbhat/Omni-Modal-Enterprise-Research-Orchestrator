import { getPhaseOneComponents } from "@/lib/phase";

export default function HomePage() {
  const components = getPhaseOneComponents();

  return (
    <main className="min-h-screen bg-white text-ink">
      <section className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-6xl flex-col gap-5 px-6 py-10">
          <div className="flex flex-col gap-2">
            <p className="text-sm font-semibold uppercase tracking-wide text-accent">
              Phase 1
            </p>
            <h1 className="max-w-3xl text-3xl font-semibold leading-tight md:text-5xl">
              Omni-Modal Enterprise Research Orchestrator
            </h1>
            <p className="max-w-3xl text-base leading-7 text-muted">
              Foundation shell for web, orchestration contracts, database schema,
              and security boundaries.
            </p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-8">
        <div className="overflow-hidden rounded border border-line">
          <div className="grid grid-cols-[1.2fr_0.8fr_2fr] border-b border-line bg-panel px-4 py-3 text-sm font-semibold">
            <span>Component</span>
            <span>Status</span>
            <span>Phase 1 role</span>
          </div>
          {components.map((component) => (
            <div
              className="grid grid-cols-[1.2fr_0.8fr_2fr] gap-4 border-b border-line px-4 py-3 text-sm last:border-b-0"
              key={component.name}
            >
              <span className="font-medium">{component.name}</span>
              <span className="text-muted">{component.status}</span>
              <span className="text-muted">{component.description}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
