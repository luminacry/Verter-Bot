import createFetchClient from "openapi-fetch"
import createQueryClient from "openapi-react-query"

import type { paths } from "./schema"

/** A non-2xx response from the dashboard API. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly retryAfter: number | null

  constructor(
    status: number,
    detail: string,
    retryAfter: number | null = null
  ) {
    super(`API ${String(status)}: ${detail}`)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
    this.retryAfter = retryAfter
  }
}

export function errorDetail(body: unknown): string {
  if (
    typeof body === "object" &&
    body !== null &&
    "detail" in body &&
    typeof body.detail === "string"
  ) {
    return body.detail
  }
  return "unknown"
}

interface FetchResult<T> {
  data?: T
  error?: unknown
  response: Response
}

/** Resolves to the response body, or throws {@link ApiError} for error responses. */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await request
  if (error !== undefined || !response.ok) {
    const retryAfter = Number(response.headers.get("retry-after"))
    throw new ApiError(
      response.status,
      errorDetail(error),
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null
    )
  }
  return data as T
}

const unauthorizedListeners = new Set<() => void>()

/** Called when a non-auth request returns 401, i.e. the session expired. */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener)
  return () => {
    unauthorizedListeners.delete(listener)
  }
}

export function createApiClient(
  options: {
    baseUrl?: string
    fetch?: (input: Request) => Promise<Response>
  } = {}
) {
  const client = createFetchClient<paths>({
    baseUrl: options.baseUrl ?? window.location.origin,
    fetch: options.fetch,
  })
  client.use({
    onResponse({ request, response }) {
      const path = new URL(request.url).pathname
      if (response.status === 401 && !path.startsWith("/api/auth/")) {
        for (const listener of unauthorizedListeners) {
          listener()
        }
      }
      return undefined
    },
  })
  return client
}

export const fetchClient = createApiClient()
export const $api = createQueryClient(fetchClient)
