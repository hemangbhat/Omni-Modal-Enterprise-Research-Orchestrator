import { TopBar } from "@/components/top-bar";
import { UploadDropzone } from "@/components/upload-dropzone";

export default function UploadPage() {
  return (
    <main className="relative flex flex-1 flex-col overflow-hidden bg-background">
      <TopBar searchPlaceholder="Search uploads..." />
      <div className="relative flex-1 overflow-y-auto p-lg md:p-xl">
        <div className="pointer-events-none fixed left-1/2 top-20 -z-0 h-[400px] w-[800px] -translate-x-1/2 rounded-full bg-primary/5 blur-[120px]" />
        <div className="relative z-10 mx-auto max-w-6xl space-y-8">
          <div className="space-y-2">
            <h2 className="font-headline-lg text-headline-lg text-on-surface md:font-display-lg md:text-display-lg">
              Data Ingestion
            </h2>
            <p className="max-w-2xl font-body-lg text-body-lg text-on-surface-variant">
              Securely encrypting and indexing your enterprise data.
            </p>
          </div>
          <UploadDropzone />
        </div>
      </div>
    </main>
  );
}
