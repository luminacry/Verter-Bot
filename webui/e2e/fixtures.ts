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

export const RUNTIME_METRICS = {
  received: 128,
  intercepted: 2,
  dropped: 0,
  processed: 120,
  failed: 1,
  timed_out: 0,
  pending: 3,
  in_flight: 1,
  latency: {
    buckets_us: [50, 100, 250, 500, 1000, 2500, 10000, 50000, 250000],
    counts: [100, 15, 4, 1, 0, 0, 0, 0, 0, 0],
    total: 120,
    sum_us: 4800,
  },
}

export const PLUGINS = [
  {
    name: "builtin_commands",
    author: "verter",
    desc: "Help, session id, and plugin administration",
    version: "1.0.0",
    enabled: true,
    error: null,
    reserved: true,
  },
  {
    name: "helloworld",
    author: "verter",
    desc: "Reply to /helloworld",
    version: "1.0.0",
    enabled: true,
    error: null,
    reserved: false,
  },
  {
    name: "broken",
    author: "t",
    desc: "broken",
    version: "1",
    enabled: false,
    error: "RuntimeError: import boom",
    reserved: false,
  },
]

export const PLATFORMS = [
  {
    id: "qq_main",
    type: "onebot_v11",
    status: "running",
    restarts: 0,
    last_error: null,
  },
  {
    id: "tg_main",
    type: "telegram",
    status: "error",
    restarts: 2,
    last_error: "ConnectionError: closed",
  },
]

export type PluginFixture = "ok" | "empty" | "error"
export type PlatformFixture = "ok" | "empty" | "error"

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
export async function mockApi(
  page: Page,
  {
    signedIn,
    plugins = "ok",
    platforms = "ok",
  }: { signedIn: boolean; plugins?: PluginFixture; platforms?: PlatformFixture }
) {
  await page.route(
    (url) => url.pathname === "/api/auth/me",
    (route) =>
      signedIn
        ? route.fulfill({ json: { username: "admin" } })
        : route.fulfill({ status: 401, json: { detail: "unauthorized" } })
  )
  await page.route(
    (url) => url.pathname === "/api/system/info",
    (route) => route.fulfill({ json: SYSTEM_INFO })
  )
  await page.route(
    (url) => url.pathname === "/api/metrics",
    (route) => route.fulfill({ json: RUNTIME_METRICS })
  )
  await page.route(
    (url) => url.pathname === "/api/plugins",
    (route) => {
      if (plugins === "error") {
        return route.fulfill({ status: 500, json: { detail: "unavailable" } })
      }
      return route.fulfill({ json: plugins === "empty" ? [] : PLUGINS })
    }
  )
  await page.route(
    (url) => url.pathname === "/api/platforms",
    (route) => {
      if (platforms === "error") {
        return route.fulfill({ status: 500, json: { detail: "unavailable" } })
      }
      return route.fulfill({ json: platforms === "empty" ? [] : PLATFORMS })
    }
  )
  if (signedIn) {
    await page.addInitScript((payload) => {
      class StableEventSource {
        onopen: ((event: Event) => void) | null = null
        onmessage: ((event: MessageEvent<string>) => void) | null = null
        onerror: ((event: Event) => void) | null = null
        constructor() {
          queueMicrotask(() => {
            this.onopen?.(new Event("open"))
            this.onmessage?.(new MessageEvent("message", { data: payload }))
          })
        }
        close() {
          return undefined
        }
      }
      window.EventSource = StableEventSource as unknown as typeof EventSource
    }, JSON.stringify(RUNTIME_METRICS))
  }
}
