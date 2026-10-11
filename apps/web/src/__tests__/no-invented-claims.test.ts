/**
 * The UI must not show facts the system does not have.
 *
 * Earlier screens presented constants as data: an answer "confidence", an
 * encryption label, a retention period, compliance wording, and sample people.
 * This scans the page and component sources so they cannot come back.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

const BANNED = [
  "Confidence: High",
  "AES-256",
  "7 years",
  "compliance mandates",
  "cold storage",
  "Legal Hold",
  "Sarah Jenkins",
  "Dr. Marcus Chen",
  "GPT-4o"
];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("no invented claims in the UI", () => {
  const files = [...sourceFiles(join(SRC, "app")), ...sourceFiles(join(SRC, "components"))];

  it("scans the page and component sources", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it.each(BANNED)("no screen contains %j", (phrase) => {
    const offenders = files
      .filter((file) => readFileSync(file, "utf-8").includes(phrase))
      .map((file) => relative(SRC, file).replace(/\\/g, "/"));
    expect(offenders).toEqual([]);
  });
});
