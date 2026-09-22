import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:5174", trace: "on-first-retry" },
  webServer: { command: "pnpm dev -- --port 5174", port: 5174, reuseExistingServer: false },
});
