import type { Config } from "tailwindcss";

/**
 * OMERO design system — extracted verbatim from the Stitch reference screens
 * (dark navy enterprise theme). Legacy tokens (ink/muted/panel/line/accent)
 * are retained for backwards compatibility with any un-migrated components.
 */
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // OMERO palette (source of truth: Stitch screens)
        background: "#031427",
        surface: "#031427",
        "surface-dim": "#031427",
        "surface-bright": "#2a3a4f",
        "surface-container-lowest": "#000f21",
        "surface-container-low": "#0b1c30",
        "surface-container": "#102034",
        "surface-container-high": "#1b2b3f",
        "surface-container-highest": "#26364a",
        "surface-variant": "#26364a",
        "surface-tint": "#00daf3",
        "on-surface": "#d3e4fe",
        "on-surface-variant": "#bac9cc",
        "on-background": "#d3e4fe",
        outline: "#849396",
        "outline-variant": "#3b494c",
        primary: "#c3f5ff",
        "primary-container": "#00e5ff",
        "primary-fixed": "#9cf0ff",
        "primary-fixed-dim": "#00daf3",
        "on-primary": "#00363d",
        "on-primary-container": "#00626e",
        "on-primary-fixed": "#001f24",
        "on-primary-fixed-variant": "#004f58",
        "inverse-primary": "#006875",
        "inverse-surface": "#d3e4fe",
        "inverse-on-surface": "#213145",
        secondary: "#c6c6ca",
        "secondary-container": "#4a4b4f",
        "secondary-fixed": "#e2e2e6",
        "secondary-fixed-dim": "#c6c6ca",
        "on-secondary": "#2f3034",
        "on-secondary-container": "#bbbbbf",
        tertiary: "#ebecf6",
        "tertiary-container": "#ced0da",
        "tertiary-fixed": "#e0e2ec",
        "tertiary-fixed-dim": "#c4c6d0",
        "on-tertiary": "#2d3038",
        "on-tertiary-container": "#565961",
        error: "#ffb4ab",
        "error-container": "#93000a",
        "on-error": "#690005",
        "on-error-container": "#ffdad6",
        // Legacy (kept so any leftover references still compile)
        ink: "#d3e4fe",
        muted: "#bac9cc",
        panel: "#102034",
        line: "#3b494c",
        accent: "#00daf3"
      },
      borderRadius: {
        DEFAULT: "0.25rem",
        lg: "0.5rem",
        xl: "0.75rem",
        full: "9999px"
      },
      spacing: {
        xs: "4px",
        base: "4px",
        sm: "8px",
        md: "16px",
        lg: "24px",
        gutter: "24px",
        xl: "40px",
        sidebar_width: "240px",
        max_width: "1440px"
      },
      maxWidth: {
        max_width: "1440px"
      },
      fontFamily: {
        "display-lg": ["Inter", "sans-serif"],
        "headline-lg": ["Inter", "sans-serif"],
        "headline-md": ["Inter", "sans-serif"],
        "body-lg": ["Inter", "sans-serif"],
        "body-md": ["Inter", "sans-serif"],
        "label-md": ["Inter", "sans-serif"],
        "mono-sm": ["JetBrains Mono", "monospace"]
      },
      fontSize: {
        "display-lg": [
          "48px",
          { lineHeight: "1.1", letterSpacing: "-0.02em", fontWeight: "600" }
        ],
        "headline-lg": [
          "32px",
          { lineHeight: "1.2", letterSpacing: "-0.02em", fontWeight: "600" }
        ],
        "headline-md": [
          "24px",
          { lineHeight: "1.3", letterSpacing: "-0.01em", fontWeight: "500" }
        ],
        "body-lg": ["18px", { lineHeight: "1.6", letterSpacing: "0", fontWeight: "400" }],
        "body-md": [
          "15px",
          { lineHeight: "1.6", letterSpacing: "0.01em", fontWeight: "400" }
        ],
        "label-md": [
          "13px",
          { lineHeight: "1", letterSpacing: "0.03em", fontWeight: "500" }
        ],
        "mono-sm": ["12px", { lineHeight: "1.5", letterSpacing: "0", fontWeight: "400" }]
      }
    }
  },
  plugins: []
};

export default config;
