import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served locally by the FastAPI backend from frontend/dist at "/", with the
// API under /api/*. In dev (`npm run dev`) requests to /api are proxied to
// the backend so `npm run dev` + `python -m uvicorn applybro.server:app`
// can run side by side.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
