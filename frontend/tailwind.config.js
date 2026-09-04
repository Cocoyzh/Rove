/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        rove: {
          bg: "#ffffff",
          subtle: "#f8fafc",
          card: "#ffffff",
          border: "#e2e8f0",
          borderHover: "#cbd5e1",
          primary: "#0284c7",       // 柔和的天空蓝青
          primaryHover: "#0369a1",
          primaryLight: "#f0f9ff",
          amber: "#d97706",         // 温暖琥珀色 (Claude风格)
          amberLight: "#fffbeb",
          amberBorder: "#fde68a",
          red: "#ef4444",
          redLight: "#fef2f2",
          green: "#10b981",
          greenLight: "#ecfdf5",
          text: "#334155",          // 柔和深灰
          textDim: "#64748b",       // 次级灰
          textBright: "#0f172a",    // 标题墨黑
        },
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "Noto Sans", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "Liberation Mono", "monospace"],
      },
      boxShadow: {
        'subtle': '0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px -1px rgba(0, 0, 0, 0.05)',
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05)',
      },
    },
  },
  plugins: [],
}
