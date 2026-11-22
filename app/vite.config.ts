import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  optimizeDeps: { exclude: ["maplibre-gl"] },
  server: { port: 5481, host: "127.0.0.1", strictPort: true, proxy: { "/api": "http://127.0.0.1:5482" } },
  preview: { port: 5481, host: "127.0.0.1", strictPort: true, proxy: { "/api": "http://127.0.0.1:5482" } },
});
