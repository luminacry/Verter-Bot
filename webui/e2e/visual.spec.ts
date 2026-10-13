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
      await expect(page).toHaveScreenshot(`overview-${language}-${theme}.png`, {
        fullPage: true,
      })
    })
  }
}
