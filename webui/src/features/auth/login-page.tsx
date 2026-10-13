import { useRouter, useSearch } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"

import { BrandMark } from "@/components/brand-mark"
import { LanguageToggle } from "@/components/language-toggle"
import { LoginForm, type LoginValues } from "@/components/login-form"
import { ThemeToggle } from "@/components/theme-toggle"
import { describeLoginError, useLogin } from "@/features/auth/session"

export function LoginPage() {
  const { t } = useTranslation()
  const router = useRouter()
  const { redirect } = useSearch({ from: "/login" })
  const login = useLogin()

  const handleSubmit = (values: LoginValues) => {
    login.mutate(values, {
      onSuccess: () => {
        void router.navigate({ href: redirect ?? "/", replace: true })
      },
    })
  }

  return (
    <div className="relative flex min-h-svh flex-col items-center justify-center gap-6 bg-muted p-6 md:p-10">
      <div className="absolute top-4 right-4 flex items-center gap-1">
        <LanguageToggle />
        <ThemeToggle />
      </div>
      <main className="flex w-full max-w-sm flex-col gap-6">
        <div className="flex items-center gap-2 self-center font-medium">
          <BrandMark className="size-6 rounded-md" />
          {t("app.name")}
        </div>
        <LoginForm
          onSubmit={handleSubmit}
          pending={login.isPending}
          error={login.error ? describeLoginError(login.error, t) : null}
        />
      </main>
    </div>
  )
}
