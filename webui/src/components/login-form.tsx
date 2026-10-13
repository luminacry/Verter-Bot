import { zodResolver } from "@hookform/resolvers/zod"
import { cn } from "cn"
import type { TFunction } from "i18next"
import { CircleAlertIcon } from "lucide-react"
import { useMemo } from "react"
import { Controller, useForm } from "react-hook-form"
import { Trans, useTranslation } from "react-i18next"
import * as z from "zod/mini"

import { Alert, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"

function createLoginSchema(t: TFunction) {
  return z.object({
    username: z
      .string()
      .check(z.trim(), z.minLength(1, t("auth.errors.usernameRequired"))),
    password: z
      .string()
      .check(z.minLength(1, t("auth.errors.passwordRequired"))),
  })
}

export type LoginValues = z.output<ReturnType<typeof createLoginSchema>>

interface LoginFormProps extends Omit<React.ComponentProps<"div">, "onSubmit"> {
  onSubmit: (values: LoginValues) => void
  pending?: boolean
  error?: string | null
}

export function LoginForm({
  className,
  onSubmit,
  pending = false,
  error = null,
  ...props
}: LoginFormProps) {
  const { t } = useTranslation()
  const schema = useMemo(() => createLoginSchema(t), [t])
  const form = useForm({
    resolver: zodResolver(schema),
    defaultValues: { username: "", password: "" },
  })

  return (
    <div className={cn("flex flex-col gap-6", className)} {...props}>
      <Card>
        <CardHeader className="text-center">
          <CardTitle className="text-xl">{t("auth.title")}</CardTitle>
          <CardDescription>{t("auth.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            noValidate
            onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
          >
            <FieldGroup>
              {error && (
                <Alert variant="destructive">
                  <CircleAlertIcon />
                  <AlertTitle>{error}</AlertTitle>
                </Alert>
              )}
              <Controller
                name="username"
                control={form.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor="login-username">
                      {t("auth.username")}
                    </FieldLabel>
                    <Input
                      {...field}
                      id="login-username"
                      autoComplete="username"
                      placeholder="admin"
                      aria-invalid={fieldState.invalid}
                    />
                    {fieldState.invalid && (
                      <FieldError errors={[fieldState.error]} />
                    )}
                  </Field>
                )}
              />
              <Controller
                name="password"
                control={form.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor="login-password">
                      {t("auth.password")}
                    </FieldLabel>
                    <Input
                      {...field}
                      id="login-password"
                      type="password"
                      autoComplete="current-password"
                      aria-invalid={fieldState.invalid}
                    />
                    {fieldState.invalid && (
                      <FieldError errors={[fieldState.error]} />
                    )}
                  </Field>
                )}
              />
              <Field>
                <Button type="submit" disabled={pending}>
                  {pending && <Spinner data-icon="inline-start" />}
                  {t("auth.submit")}
                </Button>
              </Field>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
      <FieldDescription className="px-6 text-center text-balance">
        <Trans
          i18nKey="auth.hint"
          components={{ cmd: <code className="font-mono whitespace-nowrap" /> }}
        />
      </FieldDescription>
    </div>
  )
}
