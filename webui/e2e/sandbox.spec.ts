import { expect, test } from "@playwright/test"

import { E2E_PASSWORD } from "./fixtures"

test("shows doctor status and never offers execute", async ({ page }) => {
  await page.goto("/login")
  await page.getByLabel("用户名").fill("admin")
  await page.getByLabel("密码").fill(E2E_PASSWORD)
  await page.getByRole("button", { name: "登录" }).click()
  await page.getByRole("link", { name: "沙箱" }).click()
  await expect(page.getByRole("heading", { name: "沙箱" })).toBeVisible()
  await expect(page.getByTestId("sandbox-page")).toBeVisible()
  await expect(page.getByRole("button", { name: "执行" })).toHaveCount(0)
})
