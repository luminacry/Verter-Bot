import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { $api } from "@/lib/api/client"

import { useMetricsStream } from "./use-metrics-stream"

function formatLatency(sumUs: number, total: number): string {
  if (total <= 0) {
    return "—"
  }
  const mean = sumUs / total
  if (mean < 1000) {
    return `${mean.toFixed(0)} µs`
  }
  return `${(mean / 1000).toFixed(2)} ms`
}

export function MetricsCard() {
  const { t } = useTranslation()
  const query = $api.useQuery("get", "/api/metrics")
  const stream = useMetricsStream(query.data)
  const metrics = stream.metrics

  return (
    <Card className="md:col-span-2 xl:col-span-3" data-testid="metrics-card">
      <CardHeader>
        <CardTitle>{t("overview.metrics.title")}</CardTitle>
        <CardDescription>{t("overview.metrics.description")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Badge
          variant={stream.status === "live" ? "secondary" : "outline"}
          data-testid="metrics-status"
        >
          {t(`overview.metrics.${stream.status}`)}
        </Badge>
        {query.isPending && metrics === undefined ? (
          <div
            className="grid gap-3 sm:grid-cols-3"
            aria-busy="true"
            data-testid="metrics-skeleton"
          >
            {[0, 1, 2].map((key) => (
              <Skeleton key={key} className="h-10 w-full" />
            ))}
          </div>
        ) : metrics === undefined ? null : (
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label={t("overview.metrics.received")}
              value={metrics.received}
            />
            <Stat
              label={t("overview.metrics.dropped")}
              value={metrics.dropped}
            />
            <Stat
              label={t("overview.metrics.processed")}
              value={metrics.processed}
            />
            <Stat
              label={t("overview.metrics.pending")}
              value={metrics.pending}
            />
            <Stat
              label={t("overview.metrics.inFlight")}
              value={metrics.in_flight}
            />
            <Stat label={t("overview.metrics.failed")} value={metrics.failed} />
            <Stat
              label={t("overview.metrics.timedOut")}
              value={metrics.timed_out}
            />
            <Stat
              label={t("overview.metrics.latency")}
              value={formatLatency(
                metrics.latency.sum_us,
                metrics.latency.total
              )}
            />
          </dl>
        )}
      </CardContent>
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border px-3 py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  )
}
