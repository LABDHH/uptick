/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink:  { DEFAULT: "#111527", soft: "#565e78", faint: "#8b93a9" },
        line: { DEFAULT: "#e9ebf2", strong: "#d5d9e4" },
        surf: { DEFAULT: "#ffffff", raised: "#f6f7fb", page: "#f8f9fc" },
        // Marketing tool palette: violet primary, coral accent, mint for wins.
        grape:  { 50:"#f3f1ff", 100:"#e9e5ff", 300:"#c3b5fd", 500:"#7c5cff", 600:"#6838f5", 700:"#5325d6" },
        coral:  { 50:"#fff1ef", 500:"#ff6b5a", 600:"#ef4a36" },
        mint:   { 50:"#eafff5", 500:"#10b981", 600:"#059669", 700:"#047857" },
        sun:    { 50:"#fff8e7", 500:"#f5a524", 600:"#d98a10" },
        sky:    { 50:"#eef6ff", 500:"#3b9dff", 600:"#1e7fe0" },
      },
      fontFamily: {
        sans: ['Inter var','Inter','ui-sans-serif','system-ui','-apple-system','Segoe UI','sans-serif'],
        display: ['Outfit','Inter','ui-sans-serif','system-ui','sans-serif'],
      },
      borderRadius: { xl2: "1.15rem", xl3: "1.6rem" },
      boxShadow: {
        card: "0 1px 2px rgba(17,21,39,.04), 0 2px 6px rgba(17,21,39,.05)",
        lift: "0 8px 28px rgba(17,21,39,.10), 0 2px 8px rgba(17,21,39,.05)",
        glow: "0 6px 22px rgba(104,56,245,.28)",
      },
      keyframes: {
        rise:  { "0%":{opacity:"0",transform:"translateY(10px)"}, "100%":{opacity:"1",transform:"none"} },
        pop:   { "0%":{transform:"scale(.94)",opacity:"0"}, "100%":{transform:"scale(1)",opacity:"1"} },
        slide: { "100%":{transform:"translateX(100%)"} },
        float: { "0%,100%":{transform:"translateY(0)"}, "50%":{transform:"translateY(-5px)"} },
      },
      animation: {
        rise: "rise .4s cubic-bezier(.22,.7,.3,1) both",
        pop:  "pop .25s cubic-bezier(.22,.7,.3,1) both",
        float:"float 3.5s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
