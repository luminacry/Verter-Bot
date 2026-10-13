import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { LoginForm } from "@/components/login-form"

describe("LoginForm", () => {
  it("reports missing fields without submitting", async () => {
    const onSubmit = vi.fn()
    render(<LoginForm onSubmit={onSubmit} />)

    await userEvent.click(screen.getByRole("button", { name: "登录" }))

    expect(await screen.findByText("请输入用户名")).toBeInTheDocument()
    expect(screen.getByText("请输入密码")).toBeInTheDocument()
    expect(screen.getByLabelText("用户名")).toHaveAttribute(
      "aria-invalid",
      "true"
    )
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("submits trimmed credentials", async () => {
    const onSubmit = vi.fn()
    render(<LoginForm onSubmit={onSubmit} />)

    await userEvent.type(screen.getByLabelText("用户名"), "  admin ")
    await userEvent.type(screen.getByLabelText("密码"), "s3cret")
    await userEvent.click(screen.getByRole("button", { name: "登录" }))

    expect(onSubmit).toHaveBeenCalledOnce()
    expect(onSubmit.mock.calls[0]?.[0]).toEqual({
      username: "admin",
      password: "s3cret",
    })
  })

  it("shows server errors and disables submit while pending", () => {
    render(<LoginForm onSubmit={vi.fn()} pending error="用户名或密码错误" />)

    expect(screen.getByRole("alert")).toHaveTextContent("用户名或密码错误")
    expect(screen.getByRole("button", { name: /登录/ })).toBeDisabled()
  })
})
