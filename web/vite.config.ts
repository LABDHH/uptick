import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // The API runs separately in development; proxying keeps the frontend
    // origin-clean so fetch() needs no CORS handling here.
    proxy: {
      "/api": { target: "http://localhost:8700", changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
