import { describe, expect, it } from "vitest"

import i18n, { detectLanguage, isLanguage } from "@/i18n"

import { en } from "./locales/en"
import { zhCN } from "./locales/zh-CN"

function keys(value: object, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) =>
    typeof child === "object" && child !== null
      ? keys(child as object, `${prefix}${key}.`)
      : [`${prefix}${key}`]
  )
}

describe("detectLanguage", () => {
  it("prefers a stored language", () => {
    expect(detectLanguage("en", ["zh-CN"])).toBe("en")
  })

  it("falls back to the browser languages", () => {
    expect(detectLanguage(null, ["zh-TW", "en"])).toBe("zh-CN")
    expect(detectLanguage("fr", ["de-DE", "en-US"])).toBe("en")
  })
})

describe("locales", () => {
  it("define the same keys in every language", () => {
    expect(keys(en).sort()).toEqual(keys(zhCN).sort())
  })

  it("never ship empty strings", () => {
    for (const locale of [zhCN, en]) {
      const empty = keys(locale).filter(
        (key) =>
          i18n.getFixedT(locale === en ? "en" : "zh-CN")(key as never) === ""
      )
      expect(empty).toEqual([])
    }
  })

  it("updates the document language", async () => {
    await i18n.changeLanguage("en")
    expect(document.documentElement.lang).toBe("en")
    expect(localStorage.getItem("verter-language")).toBe("en")
    expect(isLanguage("en")).toBe(true)
    expect(isLanguage("fr")).toBe(false)
  })
})
