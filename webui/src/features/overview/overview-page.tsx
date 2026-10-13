import { TriangleAlertIcon } from "lucide-react"
import { useTranslation } from "react-i18next"

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
import { formatDateTime, formatDuration } from "@/lib/format"

type SystemInfo = components["schemas"]["SystemInfo"]

export function OverviewPage() {
  const { t } = useTranslation()
  const query = $api.useQuery(
    "get",
    "/api/system/info",
    {},
    { refetchInterval: 30_000 }
  )

  return (
    <>
      <PageHeader
        title={t("overview.title")}
        description={t("overview.description")}
      />
      {query.isPending ? (
        <OverviewSkeleton />
      ) : query.isError ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <TriangleAlertIcon />
            </EmptyMedia>
            <EmptyTitle>{t("overview.error.title")}</EmptyTitle>
            <EmptyDescription>
              {t("overview.error.description")}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button variant="outline" onClick={() => void query.refetch()}>
              {t("overview.error.retry")}
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <OverviewCards info={query.data} />
      )}
    </>
  )
}

function Row({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-sm">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right font-medium">{children}</dd>
    </div>
  )
}

function OverviewCards({ info }: { info: SystemInfo }) {
  const { t, i18n } = useTranslation()
  const { policy, runner } = info.sandbox

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>{t("overview.status.title")}</CardTitle>
          <CardDescription>{t("overview.status.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="divide-y">
            <Row label={t("overview.status.version")}>{info.version}</Row>
            <Row label={t("overview.status.uptime")}>
              {formatDuration(info.uptime_seconds, t)}
            </Row>
            <Row label={t("overview.status.startedAt")}>
              {formatDateTime(info.started_at, i18n.language)}
            </Row>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("overview.environment.title")}</CardTitle>
          <CardDescription>
            {t("overview.environment.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="divide-y">
            <Row label={t("overview.environment.python")}>
              {info.python_version}
            </Row>
            <Row label={t("overview.environment.system")}>
              {info.os} · {info.arch}
            </Row>
            <Row label={t("overview.environment.platform")}>
              <span title={info.platform}>{info.platform}</span>
            </Row>
          </dl>
        </CardContent>
      </Card>

      <Card className="md:col-span-2 xl:col-span-1">
        <CardHeader>
          <CardTitle>{t("overview.sandbox.title")}</CardTitle>
          <CardDescription>{t("overview.sandbox.description")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <dl className="divide-y">
            <Row label={t("overview.sandbox.status")}>
              <Badge variant={policy.enabled ? "secondary" : "outline"}>
                {policy.enabled
                  ? t("overview.sandbox.enabled")
                  : t("overview.sandbox.disabled")}
              </Badge>
            </Row>
            <Row label={t("overview.sandbox.backend")}>
              {t(`overview.sandbox.backendValues.${policy.backend}`)}
            </Row>
            <Row label={t("overview.sandbox.allow")}>
              {t(`overview.sandbox.allowValues.${policy.allow}`)}
            </Row>
            <Row label={t("overview.sandbox.network")}>
              {t(`overview.sandbox.networkValues.${policy.network}`)}
            </Row>
            <Row label={t("overview.sandbox.runner")}>
              {runner.available ? (
                (runner.version ?? "—")
              ) : (
                <Badge variant="destructive" title={runner.error ?? undefined}>
                  {t("overview.sandbox.runnerUnavailable")}
                </Badge>
              )}
            </Row>
          </dl>
          <div className="flex flex-col gap-2">
            <p className="text-sm font-medium">
              {t("overview.sandbox.isolation")}
            </p>
            {runner.backends.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("overview.sandbox.noBackends")}
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {runner.backends.map((backend) => (
                  <li
                    key={backend.kind}
                    className="flex items-center justify-between gap-4 text-sm"
                  >
                    <span className="flex min-w-0 flex-col">
                      <span className="font-mono">{backend.kind}</span>
                      <span
                        className="truncate text-xs text-muted-foreground"
                        title={backend.detail}
                      >
                        {backend.detail}
                      </span>
                    </span>
                    <Badge
                      variant={backend.available ? "secondary" : "outline"}
                    >
                      {backend.available
                        ? t("overview.sandbox.available")
                        : t("overview.sandbox.unavailable")}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function OverviewSkeleton() {
  return (
    <div
      className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
      aria-busy="true"
      data-testid="overview-skeleton"
    >
      {[0, 1, 2].map((key) => (
        <Card key={key}>
          <CardHeader>
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-4 w-40" />
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
