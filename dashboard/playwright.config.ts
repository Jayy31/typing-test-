import { defineConfig, devices } from "@playwright/test";

// Local sandbox: a pre-installed Chromium (PW_CHROMIUM); CI: `npx playwright install chromium`.
const executablePath = process.env.PW_CHROMIUM || undefined;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:3100", launchOptions: { executablePath } },
  projects: [
    { name: "mobile", use: { ...devices["Pixel 7"], launchOptions: { executablePath } } },
  ],
  webServer: {
    command: "npx next start -p 3100",
    url: "http://localhost:3100/more",
    env: { DASHBOARD_FIXTURES: "1" },
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
