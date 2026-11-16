import { defineConfig } from "@playwright/test";

// Browser checks run against the dev server, which serves the fixture exports. They use the
// installed Chrome, so nothing is downloaded.
export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://127.0.0.1:5179", channel: "chrome" },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5179 --strictPort",
    url: "http://127.0.0.1:5179",
    reuseExistingServer: false,
  },
});
