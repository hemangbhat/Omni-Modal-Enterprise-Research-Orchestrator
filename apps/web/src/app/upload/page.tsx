import { TopBar } from "@/components/top-bar";
import { UploadDropzone } from "@/components/upload-dropzone";

export default function UploadPage() {
  return (
    <main className="relative flex flex-1 flex-col overflow-hidden bg-background">
      <TopBar searchPlaceholder="Search documents..." />
      <div className="relative flex-1 overflow-y-auto p-lg md:p-xl">
        <div className="relative z-10 mx-auto max-w-6xl space-y-xl">
          <div className="space-y-sm">
            <h2 className="text-[30px] font-semibold tracking-tight text-on-surface">Upload documents</h2>
            <p className="max-w-2xl text-[16px] leading-relaxed text-on-surface-variant">
              Each file is stored, read, split into passages and indexed, so Research can answer from it and cite
              the page or the moment in the recording.
            </p>
          </div>
          <UploadDropzone />
        </div>
      </div>
    </main>
  );
}
