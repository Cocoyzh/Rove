/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        rove: {
          bg: "#090d13",
          sidebar: "#0d1117",
          card: "#161b22",
          border: "#30363d",
          cyan: "#22d3ee",
          cyanHover: "#06b6d4",
          yellow: "#eab308",
          red: "#ef4444",
          green: "#10b981",
          text: "#c9d1d9",
          textDim: "#8b949e",
          textBright: "#f0f6fc",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
}
