import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export function loadRootEnv() {
  const envPath = resolve(process.cwd(), "../../.env");
  if (!existsSync(envPath)) {
    return;
  }

  for (const line of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const separatorIndex = trimmed.indexOf("=");
    if (separatorIndex === -1) {
      continue;
    }

    const key = trimmed.slice(0, separatorIndex);
    const rawValue = trimmed.slice(separatorIndex + 1);
    process.env[key] ??= rawValue.replace(/^["']|["']$/g, "");
  }
}

export function requireDatabaseUrl() {
  loadRootEnv();

  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL is required.");
  }

  return process.env.DATABASE_URL;
}
