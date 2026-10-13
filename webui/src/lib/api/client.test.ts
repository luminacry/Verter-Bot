import { describe, expect, it, vi } from "vitest"

import {
  ApiError,
  createApiClient,
  errorDetail,
  onUnauthorized,
  unwrap,
} from "@/lib/api/client"

function respond(
  status: number,
  body: unknown,
  headers: Record<string, string> = {}
) {
  return vi.fn<(input: Request) => Promise<Response>>(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json", ...headers },
      })
    )
  )
}

describe("unwrap", () => {
  it("returns the body of successful responses", async () => {
    const client = createApiClient({
      baseUrl: "http://test",
      fetch: respond(200, { username: "admin" }),
    })
    await expect(unwrap(client.GET("/api/auth/me"))).resolves.toEqual({
      username: "admin",
    })
  })

  it("throws ApiError with detail and Retry-After", async () => {
    const client = createApiClient({
      baseUrl: "http://test",
      fetch: respond(
        429,
        { detail: "too_many_attempts" },
        { "retry-after": "42" }
      ),
    })
    const error = await unwrap(
      client.POST("/api/auth/login", { body: { username: "a", password: "b" } })
    ).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 429,
      detail: "too_many_attempts",
      retryAfter: 42,
    })
  })
})

describe("errorDetail", () => {
  it("only accepts string details", () => {
    expect(errorDetail({ detail: "x" })).toBe("x")
    expect(errorDetail({ detail: [{ msg: "bad" }] })).toBe("unknown")
    expect(errorDetail(null)).toBe("unknown")
  })
})

describe("onUnauthorized", () => {
  it("fires for expired sessions on non-auth endpoints only", async () => {
    const listener = vi.fn()
    const unsubscribe = onUnauthorized(listener)
    const unauthorized = respond(401, { detail: "unauthorized" })
    const client = createApiClient({
      baseUrl: "http://test",
      fetch: unauthorized,
    })

    await client.GET("/api/auth/me")
    expect(listener).not.toHaveBeenCalled()

    await client.GET("/api/system/info")
    expect(listener).toHaveBeenCalledOnce()

    unsubscribe()
    await client.GET("/api/system/info")
    expect(listener).toHaveBeenCalledOnce()
  })
})
