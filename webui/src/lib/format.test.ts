import { describe, expect, it } from "vitest"

import i18n from "@/i18n"
import { formatDateTime, formatDuration } from "@/lib/format"

describe("formatDuration", () => {
  it.each([
    [0, "不到 1 分钟"],
    [59, "不到 1 分钟"],
    [60, "1 分钟"],
    [3_660, "1 小时 1 分钟"],
    [90_061, "1 天 1 小时"],
    [86_460, "1 天 1 分钟"],
    [-5, "不到 1 分钟"],
  ])("formats %d seconds as %s", (seconds, expected) => {
    expect(formatDuration(seconds, i18n.t)).toBe(expected)
  })

  it("uses compact English units", async () => {
    await i18n.changeLanguage("en")
    expect(formatDuration(93_780, i18n.t)).toBe("1d 2h")
  })
})

describe("formatDateTime", () => {
  it("formats ISO timestamps for the locale", () => {
    const text = formatDateTime("2026-10-13T01:00:00Z", "en")
    expect(text).toMatch(/2026/)
  })
})
