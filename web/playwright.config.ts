import { defineConfig } from "@playwright/test";

const port = Number(process.env.E2E_PORT ?? 5174);

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: `http://localhost:${port}`, trace: "on-first-retry" },
  webServer: { command: `pnpm exec vite --port ${port}`, port, reuseExistingServer: false },
});
