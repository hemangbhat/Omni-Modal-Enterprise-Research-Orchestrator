import { Suspense } from "react";
import { ResearchChat } from "@/components/research-chat";

export default function ResearchPage() {
  return (
    <main className="flex flex-1 overflow-hidden bg-surface">
      <Suspense fallback={null}>
        <ResearchChat />
      </Suspense>
    </main>
  );
}
