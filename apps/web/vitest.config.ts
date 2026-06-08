import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
    // Only run files that end in .test.ts or .test.tsx AND have real test suites.
    // The legacy stub files (api-client.test.ts, error-boundary.test.tsx, etc.)
    // are pure TypeScript export files with no describe/it blocks — they are
    // excluded here so Vitest doesn't fail on "No test suite found".
    include: [
      "src/__tests__/env.test.ts",
      "src/__tests__/api-client-real.test.ts",
    ],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
