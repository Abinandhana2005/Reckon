import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds to web/dist, which FastAPI serves on its own origin. The dev proxy
// points at the same API so development and production differ only in who is
// serving the static files.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
