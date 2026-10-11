/**
 * Unit tests for validateUploadFile — the client-side pre-check that runs
 * before an upload request is dispatched (the backend re-validates).
 */
import { describe, expect, it } from "vitest";
import {
  ALLOWED_EXTENSIONS,
  MAX_FILE_BYTES,
  validateUploadFile,
} from "./useUploadValidation";

function makeFile(name: string, size: number): File {
  return { name, size } as File;
}

describe("validateUploadFile", () => {
  it.each(Array.from(ALLOWED_EXTENSIONS))("accepts %s at exactly the size limit", (ext) => {
    expect(validateUploadFile(makeFile(`test${ext}`, MAX_FILE_BYTES))).toBeNull();
  });

  it.each(Array.from(ALLOWED_EXTENSIONS))("rejects %s one byte over the size limit", (ext) => {
    expect(validateUploadFile(makeFile(`test${ext}`, MAX_FILE_BYTES + 1))).toContain(
      "exceeds the 50 MiB limit"
    );
  });

  it.each(["test.exe", "test.txt", "archive.pdf.exe"])("rejects disallowed type %s", (name) => {
    expect(validateUploadFile(makeFile(name, 1024))).toContain("is not supported");
  });

  it("rejects a file with no extension", () => {
    expect(validateUploadFile(makeFile("test", 1024))).toContain("(no extension)");
  });

  it("treats the extension case-insensitively", () => {
    expect(validateUploadFile(makeFile("REPORT.PDF", 1024))).toBeNull();
  });
});
