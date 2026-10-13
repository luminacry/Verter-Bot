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

type Doctor = components["schemas"]["DoctorModel"]
type Audit = components["schemas"]["AuditModel"]

export function SandboxPage() {
  const { t } = useTranslation()
  const doctorQuery = $api.useQuery(
    "get",
    "/api/sandbox/doctor",
    {},
    { retry: false }
  )
  const auditQuery = $api.useQuery(
    "get",
    "/api/sandbox/audit",
    {},
    { retry: false }
  )
  const doctor = doctorQuery.data
  const audit = auditQuery.data

  return (
    <>
      <PageHeader
        title={t("sandbox.title")}
        description={t("sandbox.description")}
      />
      {doctorQuery.isPending || auditQuery.isPending ? (
        <SandboxSkeleton />
      ) : doctorQuery.isError ||
        auditQuery.isError ||
        doctor === undefined ||
        audit === undefined ? (
        <Empty className="border" data-testid="sandbox-error">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <TriangleAlertIcon />
            </EmptyMedia>
            <EmptyTitle>{t("sandbox.error.title")}</EmptyTitle>
            <EmptyDescription>
              {t("sandbox.error.description")}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              variant="outline"
              onClick={() => {
                void doctorQuery.refetch()
                void auditQuery.refetch()
              }}
            >
              {t("sandbox.error.retry")}
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <SandboxBody doctor={doctor} audit={audit} />
      )}
    </>
  )
}

function SandboxBody({ doctor, audit }: { doctor: Doctor; audit: Audit[] }) {
  const { t } = useTranslation()
  const isolated = doctor.can_execute && doctor.auto !== "none"

  return (
    <div className="flex flex-col gap-4" data-testid="sandbox-page">
      <Card>
        <CardHeader>
          <CardTitle>{t("sandbox.policy")}</CardTitle>
          <CardDescription>{doctor.policy}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-4 text-sm">
            <span className="text-muted-foreground">{t("sandbox.auto")}</span>
            <span className="font-mono">
              {doctor.auto === "none" ? t("sandbox.none") : doctor.auto}
            </span>
          </div>
          {isolated ? (
            <p className="text-sm" data-testid="sandbox-isolated">
              {t("sandbox.isolated")}
            </p>
          ) : (
            <p
              className="text-sm text-muted-foreground"
              data-testid="sandbox-not-isolated"
            >
              {t("sandbox.notIsolated")}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("sandbox.capabilities")}</CardTitle>
        </CardHeader>
        <CardContent>
          {doctor.backends.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("sandbox.empty.description")}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {doctor.backends.map((backend) => (
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
                  <Badge variant={backend.available ? "secondary" : "outline"}>
                    {backend.available
                      ? t("sandbox.available")
                      : t("sandbox.unavailable")}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("sandbox.audit")}</CardTitle>
        </CardHeader>
        <CardContent>
          {audit.length === 0 ? (
            <p
              className="text-sm text-muted-foreground"
              data-testid="sandbox-audit-empty"
            >
              {t("sandbox.auditEmpty")}
            </p>
          ) : (
            <ul className="flex flex-col gap-3" data-testid="sandbox-audit">
              {audit.map((row) => (
                <li key={row.id} className="flex flex-col gap-1 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono">{row.command}</span>
                    <Badge variant={row.allowed ? "secondary" : "outline"}>
                      {row.allowed ? t("sandbox.allowed") : t("sandbox.denied")}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {row.initiator} · {row.session_id}
                    {row.backend ? ` · ${row.backend}` : ""}
                    {row.error ? ` · ${row.error}` : ""}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SandboxSkeleton() {
  return (
    <div
      className="flex flex-col gap-4"
      aria-busy="true"
      data-testid="sandbox-skeleton"
    >
      {[0, 1, 2].map((key) => (
        <Card key={key}>
          <CardHeader>
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-4 w-64" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-8 w-full" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
