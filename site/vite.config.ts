import react from "@vitejs/plugin-react";
import { loadEnv, type Plugin } from "vite";
import { defineConfig } from "vitest/config";

/** GitHub Pages has no rewrites: it answers an unknown path with 404.html, so that is the page
 * too, and a link to /pair/<id> loads the page, which reads its route from the address. */
const deepLinks: Plugin = {
  name: "deep-links",
  apply: "build",
  enforce: "post",
  generateBundle(_options, bundle) {
    const page = bundle["index.html"];
    if (page?.type !== "asset") throw new Error("the build has no index.html");
    this.emitFile({ type: "asset", fileName: "404.html", source: page.source });
  },
};

export default defineConfig(({ command, mode }) => ({
  plugins: [react(), deepLinks],
  // Routes are paths, so the build needs the path it is served from: a project page's is the
  // repository's name, which the deploy sets. The dev server and tests serve from the root.
  base: command === "build" ? loadEnv(mode, ".", "SITE_").SITE_BASE || "/" : "/",
  // Bundled up front, so the dev server never reloads a page mid-test when it first meets one.
  optimizeDeps: { include: ["d3", "react", "react-dom", "react-dom/client", "react/jsx-runtime"] },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    // CSS is empty in tests unless included; the contrast test reads the tokens.
    css: { include: [/tokens\.css/] },
  },
}));
