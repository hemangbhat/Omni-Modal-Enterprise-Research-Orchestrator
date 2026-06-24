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
      name: "Entity extraction (NER)",
      status: "ready",
      description:
        "Rule-based extractor by default; optional pretrained Hugging Face NER model (dslim/bert-base-NER). A QLoRA fine-tuning pipeline is scaffolded but no fine-tuned weights are trained."
    },
    {
      name: "ADK and external delegation",
      status: "deferred",
      description:
        "Deterministic orchestration graph (not multi-agent). External A2A/Gemini delegation is implemented but disabled unless an endpoint is configured."
    }
  ];
}
