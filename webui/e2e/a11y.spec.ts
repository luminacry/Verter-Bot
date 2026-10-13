import AxeBuilder from "@axe-core/playwright"
import { expect, test, type Page } from "@playwright/test"

import { mockApi, THEMES, usePreferences } from "./fixtures"

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze()
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => ({
      id: v.id,
      help: v.help,
      targets: v.nodes.map((n) => n.target),
    }))
}

for (const theme of THEMES) {
  test(`login page has no serious a11y violations (${theme})`, async ({
    page,
  }) => {
    await usePreferences(page, "zh-CN", theme)
    await mockApi(page, { signedIn: false })
    await page.goto("/login")
    await expect(page.getByRole("button", { name: "登录" })).toBeVisible()
    expect(await seriousViolations(page)).toEqual([])
  })

  test(`overview page has no serious a11y violations (${theme})`, async ({
    page,
  }) => {
    await usePreferences(page, "zh-CN", theme)
    await mockApi(page, { signedIn: true })
    await page.goto("/")
    await expect(page.getByText("bubblewrap")).toBeVisible()
    await expect(page.getByTestId("metrics-status")).toBeVisible()
    expect(await seriousViolations(page)).toEqual([])
  })

  test(`plugins page has no serious a11y violations (${theme})`, async ({
    page,
  }) => {
    await usePreferences(page, "zh-CN", theme)
    await mockApi(page, { signedIn: true })
    await page.goto("/plugins")
    await expect(page.getByTestId("plugins-list")).toBeVisible()
    expect(await seriousViolations(page)).toEqual([])
  })

  test(`plugins empty state has no serious a11y violations (${theme})`, async ({
    page,
  }) => {
    await usePreferences(page, "en", theme)
    await mockApi(page, { signedIn: true, plugins: "empty" })
    await page.goto("/plugins")
    await expect(page.getByTestId("plugins-empty")).toBeVisible()
    expect(await seriousViolations(page)).toEqual([])
  })

  test(`plugins error state has no serious a11y violations (${theme})`, async ({
    page,
  }) => {
    await usePreferences(page, "en", theme)
    await mockApi(page, { signedIn: true, plugins: "error" })
    await page.goto("/plugins")
    await expect(page.getByTestId("plugins-error")).toBeVisible()
    expect(await seriousViolations(page)).toEqual([])
  })
}
