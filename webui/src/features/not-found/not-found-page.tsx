import { Link } from "@tanstack/react-router"
import { FileQuestionIcon } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"

export function NotFoundPage() {
  const { t } = useTranslation()

  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FileQuestionIcon />
          </EmptyMedia>
          <EmptyTitle>{t("notFound.title")}</EmptyTitle>
          <EmptyDescription>{t("notFound.description")}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button render={<Link to="/" />}>{t("notFound.back")}</Button>
        </EmptyContent>
      </Empty>
    </main>
  )
}
