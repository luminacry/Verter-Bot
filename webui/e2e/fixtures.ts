import type { Page } from "@playwright/test"

export const E2E_PORT = Number(process.env.E2E_PORT ?? "6186")
export const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "verter-e2e-password"

export const LANGUAGES = ["zh-CN", "en"] as const
export const THEMES = ["light", "dark"] as const

export type Language = (typeof LANGUAGES)[number]
export type Theme = (typeof THEMES)[number]

export const SYSTEM_INFO = {
  version: "0.1.0",
  python_version: "3.12.3",
  os: "linux",
  arch: "x86_64",
  platform: "Linux-6.12.0-x86_64-with-glibc2.39",
  started_at: "2026-10-13T01:00:00Z",
  uptime_seconds: 93_780,
  sandbox: {
    policy: {
      enabled: true,
      backend: "local",
      allow: "admins",
      network: "deny",
    },
    runner: {
      available: true,
      version: "0.1.0",
      backends: [
        { kind: "bubblewrap", available: true, detail: "/usr/bin/bwrap" },
      ],
      error: null,
    },
  },
}

/** Seeds theme and language before any page script runs. */
export async function usePreferences(
  page: Page,
  language: Language,
  theme: Theme
) {
  await page.addInitScript(
    ([lang, mode]) => {
      localStorage.setItem("verter-language", lang)
      localStorage.setItem("theme", mode)
    },
    [language, theme] as const
  )
}

/** Serves deterministic API responses so screenshots never depend on the host. */
export async function mockApi(page: Page, { signedIn }: { signedIn: boolean }) {
  await page.route("**/api/auth/me", (route) =>
    signedIn
      ? route.fulfill({ json: { username: "admin" } })
      : route.fulfill({ status: 401, json: { detail: "unauthorized" } })
  )
  await page.route("**/api/system/info", (route) =>
    route.fulfill({ json: SYSTEM_INFO })
  )
}
