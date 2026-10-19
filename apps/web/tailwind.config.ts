import type { Config } from "tailwindcss";

/**
 * OMERO design system: a reading room for evidence. Ink-blue paper, warm
 * reading-white text, and one highlighter ochre reserved for evidence,
 * citations, focus and the main action. Token names follow Material 3 so
 * every component picks up the palette from here.
 */
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // OMERO palette (source of truth: Stitch screens)
        background: "#0F141B",
        surface: "#0F141B",
        "surface-dim": "#0F141B",
        "surface-bright": "#2C3642",
        "surface-container-lowest": "#0B0F15",
        "surface-container-low": "#141A23",
        "surface-container": "#18202A",
        "surface-container-high": "#1F2833",
        "surface-container-highest": "#27313D",
        "surface-variant": "#27313D",
        "surface-tint": "#E3B341",
        "on-surface": "#ECE7DC",
        "on-surface-variant": "#B3B6BA",
        "on-background": "#ECE7DC",
        outline: "#858B92",
        "outline-variant": "#36404B",
        primary: "#F0CF7A",
        "primary-container": "#E3B341",
        "primary-fixed": "#F0CF7A",
        "primary-fixed-dim": "#E3B341",
        "on-primary": "#1A1406",
        "on-primary-container": "#1A1406",
        "on-primary-fixed": "#1A1406",
        "on-primary-fixed-variant": "#3D2E07",
        "inverse-primary": "#8A6A12",
        "inverse-surface": "#ECE7DC",
        "inverse-on-surface": "#1F2833",
        secondary: "#c6c6ca",
        "secondary-container": "#4a4b4f",
        "secondary-fixed": "#e2e2e6",
        "secondary-fixed-dim": "#c6c6ca",
        "on-secondary": "#2f3034",
        "on-secondary-container": "#bbbbbf",
        tertiary: "#ebecf6",
        "tertiary-container": "#5E6E84",
        "tertiary-fixed": "#e0e2ec",
        "tertiary-fixed-dim": "#c4c6d0",
        "on-tertiary": "#2d3038",
        "on-tertiary-container": "#565961",
        error: "#F2A39A",
        "error-container": "#5C1A16",
        "on-error": "#3A0C08",
        "on-error-container": "#FFDAD5",
        // Legacy (kept so any leftover references still compile)
        ink: "#ECE7DC",
        muted: "#B3B6BA",
        panel: "#18202A",
        line: "#36404B",
        accent: "#E3B341"
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
        // Titles and quoted evidence read like the documents themselves.
        serif: ['"Source Serif 4"', "Georgia", "serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
        "display-lg": ['"Source Serif 4"', "Georgia", "serif"],
        "headline-lg": ['"Source Serif 4"', "Georgia", "serif"],
        "headline-md": ['"Source Serif 4"', "Georgia", "serif"],
        "body-lg": ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        "body-md": ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        "label-md": ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        // Small labels use the interface sans; code and kbd stay monospace (globals.css).
        "mono-sm": ['"IBM Plex Sans"', "system-ui", "sans-serif"]
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
          { lineHeight: "1.2", letterSpacing: "0.005em", fontWeight: "500" }
        ],
        "mono-sm": ["12px", { lineHeight: "1.5", letterSpacing: "0", fontWeight: "400" }]
      }
    }
  },
  plugins: []
};

export default config;
