import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#111827",
        muted: "#6b7280",
        panel: "#f8fafc",
        line: "#d1d5db",
        accent: "#0f766e"
      }
    }
  },
  plugins: []
};

export default config;
