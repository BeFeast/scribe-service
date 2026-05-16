import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/spa/",
  plugins: [react()],
  build: {
    outDir: "../../src/scribe/web/static/spa",
    emptyOutDir: true,
    manifest: true,
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:13120",
        changeOrigin: true,
      },
    },
  },
});
