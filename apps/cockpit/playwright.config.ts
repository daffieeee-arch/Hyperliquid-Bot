import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  webServer: {
    command: "pnpm run start",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      TRADING_MODE: "PAPER",
      PORT: "3000",
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
