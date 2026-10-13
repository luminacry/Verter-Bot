import { expect, test } from "@playwright/test"

import { E2E_PASSWORD } from "./fixtures"

test("restarts an adapter from the keyboard", async ({ page }) => {
  await page.goto("/login")
  await page.getByLabel("用户名").fill("admin")
  await page.getByLabel("密码").fill(E2E_PASSWORD)
  await page.getByRole("button", { name: "登录" }).click()
  await page.getByRole("link", { name: "平台" }).click()
  await expect(page.getByRole("heading", { name: "平台" })).toBeVisible()
  await expect(page.getByText("mem", { exact: true })).toBeVisible()

  const restart = page.getByRole("button", { name: "重启 mem" })
  await restart.focus()
  await page.keyboard.press("Enter")
  await expect(page.getByText("已重启 mem")).toBeVisible()
})

test("shows a loading skeleton while platforms are in flight", async ({
  page,
}) => {
  await page.route("**/api/platforms", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 5_000))
    await route.fulfill({ json: [] })
  })
  await page.goto("/login")
  await page.getByLabel("用户名").fill("admin")
  await page.getByLabel("密码").fill(E2E_PASSWORD)
  await page.getByRole("button", { name: "登录" }).click()
  await page.getByRole("link", { name: "平台" }).click()
  await expect(page.getByTestId("platforms-skeleton")).toBeVisible()
})
