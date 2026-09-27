import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = "http://127.0.0.1:8765"; // dev proxy target: `scripts/tokenledger.sh serve`

export default defineConfig({
  plugins: [react()],
  cacheDir: "../.build/vite-cache",
  server: { port: 5173, strictPort: true, proxy: { "/api": backend } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
});
