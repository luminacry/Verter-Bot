import { expect, test } from "@playwright/test"

import { E2E_PASSWORD } from "./fixtures"

test("signs in, shows the overview, keeps the session, and signs out", async ({
  page,
}) => {
  await page.goto("/")
  await expect(page).toHaveURL(/\/login/)

  await page.getByLabel("用户名").fill("admin")
  await page.getByLabel("密码").fill("not-the-password")
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page.getByRole("alert")).toContainText("用户名或密码错误")

  await page.getByLabel("密码").fill(E2E_PASSWORD)
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page.getByRole("heading", { name: "概览" })).toBeVisible()
  await expect(page.getByText("仅管理员")).toBeVisible()
  await expect(page.getByText("禁止")).toBeVisible()

  await page.reload()
  await expect(page.getByRole("heading", { name: "概览" })).toBeVisible()

  await page.getByRole("button", { name: /admin/ }).click()
  await page.getByRole("menuitem", { name: "退出登录" }).click()
  await expect(page).toHaveURL(/\/login/)
  await page.goto("/")
  await expect(page).toHaveURL(/\/login/)
})

test("validates required fields before calling the API", async ({ page }) => {
  await page.goto("/login")
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page.getByText("请输入用户名")).toBeVisible()
  await expect(page.getByText("请输入密码")).toBeVisible()
})

test("unknown routes show the not-found page", async ({ page }) => {
  await page.goto("/does-not-exist")
  await expect(page.getByText("页面不存在", { exact: true })).toBeVisible()
})

test("language and theme preferences persist across reloads", async ({
  page,
}) => {
  await page.goto("/login")
  await page.getByRole("button", { name: "切换语言" }).click()
  await page.getByRole("menuitemradio", { name: "English" }).click()
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible()
  await expect(page.locator("html")).toHaveAttribute("lang", "en")

  await page.getByRole("button", { name: "Change theme" }).click()
  await page.getByRole("menuitemradio", { name: "Dark" }).click()
  await expect(page.locator("html")).toHaveClass(/dark/)

  await page.reload()
  await expect(page.locator("html")).toHaveClass(/dark/)
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible()
})
