import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "@tanstack/react-router"
import type { TFunction } from "i18next"

import { $api, ApiError, fetchClient, unwrap } from "@/lib/api/client"

export const sessionQuery = $api.queryOptions(
  "get",
  "/api/auth/me",
  {},
  { retry: false, staleTime: 5 * 60_000 }
)

export interface Credentials {
  username: string
  password: string
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Credentials) =>
      unwrap(fetchClient.POST("/api/auth/login", { body })),
    onSuccess: (session) => {
      queryClient.setQueryData(sessionQuery.queryKey, session)
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  const router = useRouter()
  return useMutation({
    mutationFn: () => unwrap(fetchClient.POST("/api/auth/logout")),
    onSettled: async () => {
      queryClient.clear()
      await router.navigate({ to: "/login" })
    },
  })
}

export function describeLoginError(error: unknown, t: TFunction): string {
  if (error instanceof ApiError) {
    switch (error.detail) {
      case "invalid_credentials":
        return t("auth.errors.invalidCredentials")
      case "too_many_attempts":
        return t("auth.errors.tooManyAttempts", {
          seconds: error.retryAfter ?? 60,
        })
      case "not_initialized":
        return t("auth.errors.notInitialized")
      default:
        return t("auth.errors.unknown")
    }
  }
  if (error instanceof TypeError) {
    return t("auth.errors.network")
  }
  return t("auth.errors.unknown")
}
