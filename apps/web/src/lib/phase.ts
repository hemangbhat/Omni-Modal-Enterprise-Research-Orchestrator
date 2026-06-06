export type PhaseComponentStatus = "ready" | "contract" | "deferred";

export type PhaseComponent = {
  name: string;
  status: PhaseComponentStatus;
  description: string;
};

export function getPhaseOneComponents(): PhaseComponent[] {
  return [
    {
      name: "Next.js shell",
      status: "ready",
      description: "App Router dashboard and local health endpoint."
    },
    {
      name: "Python orchestration",
      status: "contract",
      description: "Typed backend contracts and Phase 1 health snapshot."
    },
    {
      name: "Database",
      status: "contract",
      description: "Drizzle schema package and pgvector migration draft."
    },
    {
      name: "Whisper",
      status: "deferred",
      description: "Local transcription interface only; model runtime is not wired."
    },
    {
      name: "QLoRA entity extraction",
      status: "deferred",
      description: "Extractor interface only; model artifact is not wired."
    },
    {
      name: "ADK and external delegation",
      status: "deferred",
      description: "Capability boundary only; no ADK, A2A, or Gemini calls yet."
    }
  ];
}
