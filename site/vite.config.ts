import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Relative asset paths, so the build works from a project page's subpath.
  base: "./",
  // Bundled up front, so the dev server never reloads a page mid-test when it first meets one.
  optimizeDeps: { include: ["d3", "react", "react-dom", "react-dom/client", "react/jsx-runtime"] },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    // CSS is empty in tests unless included; the contrast test reads the tokens.
    css: { include: [/tokens\.css/] },
  },
});
