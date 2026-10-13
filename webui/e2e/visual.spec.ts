import { expect, test } from "@playwright/test"

import { LANGUAGES, mockApi, THEMES, usePreferences } from "./fixtures"

for (const language of LANGUAGES) {
  for (const theme of THEMES) {
    test(`login page looks right (${language}, ${theme})`, async ({ page }) => {
      await usePreferences(page, language, theme)
      await mockApi(page, { signedIn: false })
      await page.goto("/login")
      await expect(
        page.getByRole("button", { name: /登录|Sign in/ })
      ).toBeVisible()
      await expect(page).toHaveScreenshot(`login-${language}-${theme}.png`, {
        fullPage: true,
      })
    })

    test(`overview page looks right (${language}, ${theme})`, async ({
      page,
    }) => {
      await usePreferences(page, language, theme)
      await mockApi(page, { signedIn: true })
      await page.goto("/")
      await expect(page.getByText("bubblewrap")).toBeVisible()
      await expect(page.getByTestId("metrics-status")).toBeVisible()
      await expect(page).toHaveScreenshot(`overview-${language}-${theme}.png`, {
        fullPage: true,
      })
    })

    test(`plugins page looks right (${language}, ${theme})`, async ({
      page,
    }) => {
      await usePreferences(page, language, theme)
      await mockApi(page, { signedIn: true })
      await page.goto("/plugins")
      await expect(page.getByTestId("plugins-list")).toBeVisible()
      await expect(page.getByText("import boom")).toBeVisible()
      await expect(page).toHaveScreenshot(`plugins-${language}-${theme}.png`, {
        fullPage: true,
      })
    })
  }
}

test("plugins page empty state", async ({ page }) => {
  await usePreferences(page, "zh-CN", "light")
  await mockApi(page, { signedIn: true, plugins: "empty" })
  await page.goto("/plugins")
  await expect(page.getByTestId("plugins-empty")).toBeVisible()
  await expect(page).toHaveScreenshot("plugins-empty-zh-CN-light.png", {
    fullPage: true,
  })
})

test("plugins page error state", async ({ page }) => {
  await usePreferences(page, "en", "dark")
  await mockApi(page, { signedIn: true, plugins: "error" })
  await page.goto("/plugins")
  await expect(page.getByTestId("plugins-error")).toBeVisible()
  await expect(page).toHaveScreenshot("plugins-error-en-dark.png", {
    fullPage: true,
  })
})
