import { defineConfig, devices } from "@playwright/test"

import { E2E_PASSWORD, E2E_PORT } from "./e2e/fixtures"

const isCI = Boolean(process.env.CI)

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  reporter: isCI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${String(E2E_PORT)}`,
    trace: "retain-on-failure",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  },
  expect: {
    // Baselines come from the pinned Playwright image (scripts/playwright-docker.sh),
    // so rendering is deterministic and any real change must fail.
    toHaveScreenshot: {
      maxDiffPixels: 20,
      animations: "disabled",
      caret: "hide",
    },
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
      },
    },
  ],
  webServer: {
    command: "uv run --no-sync python scripts/e2e_server.py",
    cwd: "..",
    url: `http://127.0.0.1:${String(E2E_PORT)}/api/health`,
    reuseExistingServer: !isCI,
    timeout: 60_000,
    env: { E2E_PORT: String(E2E_PORT), E2E_PASSWORD },
  },
})
