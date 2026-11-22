/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f4f7f5",
          100: "#e4ebe6",
          200: "#c9d6ce",
          700: "#2f3f37",
          800: "#243029",
          900: "#18201b",
        },
        accent: {
          DEFAULT: "#2f6f5e",
          soft: "#d7ebe3",
        },
      },
      fontFamily: {
        sans: ['"Segoe UI"', "PingFang SC", "Microsoft YaHei", "sans-serif"],
      },
    },
  },
  plugins: [],
};
