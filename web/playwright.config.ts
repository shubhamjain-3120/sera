import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:5173", trace: "on-first-retry" },
  webServer: { command: "pnpm dev", port: 5173, reuseExistingServer: true },
});

