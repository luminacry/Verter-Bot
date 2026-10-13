import { TriangleAlertIcon } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { PageHeader } from "@/components/page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { $api } from "@/lib/api/client"
import type { components } from "@/lib/api/schema"

type Platform = components["schemas"]["PlatformModel"]

export function PlatformsPage() {
  const { t } = useTranslation()
  const query = $api.useQuery("get", "/api/platforms", {}, { retry: false })
  const platforms = query.data

  return (
    <>
      <PageHeader
        title={t("platforms.title")}
        description={t("platforms.description")}
      />
      {query.isPending ? (
        <PlatformsSkeleton />
      ) : query.isError || platforms === undefined ? (
        <Empty className="border" data-testid="platforms-error">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <TriangleAlertIcon />
            </EmptyMedia>
            <EmptyTitle>{t("platforms.error.title")}</EmptyTitle>
            <EmptyDescription>
              {t("platforms.error.description")}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button variant="outline" onClick={() => void query.refetch()}>
              {t("platforms.error.retry")}
            </Button>
          </EmptyContent>
        </Empty>
      ) : platforms.length === 0 ? (
        <Empty className="border" data-testid="platforms-empty">
          <EmptyHeader>
            <EmptyTitle>{t("platforms.empty.title")}</EmptyTitle>
            <EmptyDescription>
              {t("platforms.empty.description")}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className="flex flex-col gap-4" data-testid="platforms-list">
          {platforms.map((platform) => (
            <li key={platform.id}>
              <PlatformCard
                platform={platform}
                onChanged={() => void query.refetch()}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function PlatformCard({
  platform,
  onChanged,
}: {
  platform: Platform
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [pending, setPending] = useState(false)
  const restart = $api.useMutation(
    "post",
    "/api/platforms/{platform_id}/restart"
  )
  const failed = platform.status === "error"

  async function run() {
    setPending(true)
    try {
      const result = await restart.mutateAsync({
        params: { path: { platform_id: platform.id } },
      })
      if (result.ok) {
        toast.success(result.message)
      } else {
        toast.error(result.message)
      }
      onChanged()
    } catch {
      toast.error(t("platforms.actionFailed"))
    } finally {
      setPending(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{platform.id}</span>
          <Badge variant={failed ? "outline" : "secondary"}>
            {statusLabel(platform.status, t)}
          </Badge>
        </CardTitle>
        <CardDescription>
          {platform.type}
          {" · "}
          {t("platforms.restarts", { count: platform.restarts })}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {platform.last_error ? (
          <p className="text-sm text-destructive">
            {t("platforms.lastError")}: {platform.last_error}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={pending}
            aria-label={t("platforms.restart", { name: platform.id })}
            onClick={() => void run()}
          >
            {t("platforms.restartLabel")}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function statusLabel(
  status: Platform["status"],
  t: ReturnType<typeof useTranslation>["t"]
): string {
  switch (status) {
    case "running":
      return t("platforms.stateRunning")
    case "error":
      return t("platforms.stateError")
    case "stopped":
      return t("platforms.stateStopped")
    default:
      return t("platforms.statePending")
  }
}

function PlatformsSkeleton() {
  return (
    <div
      className="flex flex-col gap-4"
      aria-busy="true"
      data-testid="platforms-skeleton"
    >
      {[0, 1].map((key) => (
        <Card key={key}>
          <CardHeader>
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-4 w-64" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-8 w-24" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
