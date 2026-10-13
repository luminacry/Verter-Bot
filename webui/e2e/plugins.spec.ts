import { expect, test } from "@playwright/test"

import { E2E_PASSWORD } from "./fixtures"

test("reloads a plugin from the keyboard", async ({ page }) => {
  await page.goto("/login")
  await page.getByLabel("用户名").fill("admin")
  await page.getByLabel("密码").fill(E2E_PASSWORD)
  await page.getByRole("button", { name: "登录" }).click()
  await page.getByRole("link", { name: "插件" }).click()
  await expect(page.getByRole("heading", { name: "插件" })).toBeVisible()
  await expect(page.getByText("helloworld", { exact: true })).toBeVisible()

  const reload = page.getByRole("button", { name: "重载 helloworld" })
  await reload.focus()
  await page.keyboard.press("Enter")
  await expect(page.getByText("已重载 helloworld")).toBeVisible()

  const builtin = page.getByRole("button", { name: "关闭 builtin_commands" })
  await expect(builtin).toBeDisabled()
})

test("shows a loading skeleton while plugins are in flight", async ({
  page,
}) => {
  await page.goto("/login")
  await page.getByLabel("用户名").fill("admin")
  await page.getByLabel("密码").fill(E2E_PASSWORD)
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page.getByRole("heading", { name: "概览" })).toBeVisible()
  await page.route("**/api/plugins", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 5_000))
    await route.fulfill({ json: [] })
  })
  await page.getByRole("link", { name: "插件" }).click()
  await expect(page.getByTestId("plugins-skeleton")).toBeVisible()
})
