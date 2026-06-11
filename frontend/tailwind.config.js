/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                // Single brand accent used across the app (replaces the ad-hoc
                // mix of blue/purple/gray). Cyan-leaning blue reads as "secure
                // ops" without competing with the slate surfaces.
                brand: {
                    50: "#ecfeff",
                    100: "#cffafe",
                    200: "#a5f3fc",
                    300: "#67e8f9",
                    400: "#22d3ee",
                    500: "#06b6d4",
                    600: "#0891b2",
                    700: "#0e7490",
                    800: "#155e75",
                    900: "#164e63",
                    950: "#083344",
                },
                // Semantic severity palette — tuned for contrast on the light
                // console as well as the remaining dark surfaces.
                severity: {
                    critical: "#dc2626",
                    high: "#ea580c",
                    medium: "#d97706",
                    low: "#2563eb",
                    info: "#64748b",
                },
                // Light-theme accent (high-contrast blue) for the new console look.
                accent: {
                    50: "#eff6ff",
                    100: "#dbeafe",
                    200: "#bfdbfe",
                    300: "#93c5fd",
                    400: "#60a5fa",
                    500: "#3b82f6",
                    600: "#2563eb",
                    700: "#1d4ed8",
                    800: "#1e40af",
                    900: "#1e3a8a",
                },
            },
            fontFamily: {
                sans: [
                    "IBM Plex Sans",
                    "ui-sans-serif",
                    "system-ui",
                    "-apple-system",
                    "Segoe UI",
                    "Helvetica Neue",
                    "Arial",
                    "sans-serif",
                ],
                mono: [
                    "Roboto Mono",
                    "Monaco",
                    "ui-monospace",
                    "SFMono-Regular",
                    "Menlo",
                    "Consolas",
                    "monospace",
                ],
            },
            boxShadow: {
                "brand-glow": "0 0 20px -2px rgba(34, 211, 238, 0.35)",
                "console": "0 1px 2px 0 rgba(24,24,27,0.04)",
                "console-hover": "0 4px 16px -4px rgba(37,99,235,0.18)",
            },
            keyframes: {
                "fade-in": {
                    "0%": { opacity: "0", transform: "translateY(4px)" },
                    "100%": { opacity: "1", transform: "translateY(0)" },
                },
            },
            animation: {
                "fade-in": "fade-in 0.2s ease-out",
            },
        },
    },
    plugins: [],
}
