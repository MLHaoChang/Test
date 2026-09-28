/// <reference types="vitest/config" />

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Plan 5.10: "Vite proxies API calls to 127.0.0.1:8765 in development; in UAT the built page is
// served by pg serve." These are exactly the paths api/app.py registers (plan 5.8): everything
// else (the page itself, its JS and CSS) is served by Vite in development and by pg serve, from
// web/dist, once the page is built.
const API_ORIGIN = "http://127.0.0.1:8765";
const API_PATHS = ["/health", "/portfolio", "/instruments", "/benchmarks"];

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, { target: API_ORIGIN, changeOrigin: true }])),
  },
  build: {
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    globals: false,
    css: false,
    setupFiles: ["./src/setupTests.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
