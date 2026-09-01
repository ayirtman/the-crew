import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname) } },
  test: {
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
    environment: "jsdom",
    globals: false,
  },
});
