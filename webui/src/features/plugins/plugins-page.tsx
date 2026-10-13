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

type Plugin = components["schemas"]["PluginModel"]

export function PluginsPage() {
  const { t } = useTranslation()
  const query = $api.useQuery("get", "/api/plugins", {}, { retry: false })
  const plugins = query.data

  return (
    <>
      <PageHeader
        title={t("plugins.title")}
        description={t("plugins.description")}
      />
      {query.isPending ? (
        <PluginsSkeleton />
      ) : query.isError || plugins === undefined ? (
        <Empty className="border" data-testid="plugins-error">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <TriangleAlertIcon />
            </EmptyMedia>
            <EmptyTitle>{t("plugins.error.title")}</EmptyTitle>
            <EmptyDescription>
              {t("plugins.error.description")}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button variant="outline" onClick={() => void query.refetch()}>
              {t("plugins.error.retry")}
            </Button>
          </EmptyContent>
        </Empty>
      ) : plugins.length === 0 ? (
        <Empty className="border" data-testid="plugins-empty">
          <EmptyHeader>
            <EmptyTitle>{t("plugins.empty.title")}</EmptyTitle>
            <EmptyDescription>
              {t("plugins.empty.description")}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className="flex flex-col gap-4" data-testid="plugins-list">
          {plugins.map((plugin) => (
            <li key={plugin.name}>
              <PluginCard
                plugin={plugin}
                onChanged={() => void query.refetch()}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function PluginCard({
  plugin,
  onChanged,
}: {
  plugin: Plugin
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [pending, setPending] = useState(false)
  const enable = $api.useMutation("post", "/api/plugins/{name}/enabled")
  const reload = $api.useMutation("post", "/api/plugins/{name}/reload")
  const failed = plugin.error !== null

  async function run(
    action: () => Promise<{ ok: boolean; message: string } | undefined>
  ) {
    setPending(true)
    try {
      const result = await action()
      if (result?.ok) {
        toast.success(result.message)
      } else {
        toast.error(result?.message ?? t("plugins.actionFailed"))
      }
      onChanged()
    } catch {
      toast.error(t("plugins.actionFailed"))
    } finally {
      setPending(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{plugin.name}</span>
          <Badge variant={plugin.enabled && !failed ? "secondary" : "outline"}>
            {failed
              ? t("plugins.stateFailed")
              : plugin.enabled
                ? t("plugins.stateOn")
                : t("plugins.stateOff")}
          </Badge>
          {plugin.reserved ? (
            <Badge variant="outline">{t("plugins.reserved")}</Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          {plugin.author} · {plugin.version}
          {plugin.desc ? ` · ${plugin.desc}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {plugin.error ? (
          <p className="text-sm text-destructive">{plugin.error}</p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {plugin.enabled ? (
            <Button
              variant="outline"
              size="sm"
              disabled={pending || plugin.reserved}
              aria-label={t("plugins.disable", { name: plugin.name })}
              onClick={() =>
                void run(() =>
                  enable.mutateAsync({
                    params: { path: { name: plugin.name } },
                    body: { enabled: false },
                  })
                )
              }
            >
              {t("plugins.disableLabel")}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              disabled={pending}
              aria-label={t("plugins.enable", { name: plugin.name })}
              onClick={() =>
                void run(() =>
                  enable.mutateAsync({
                    params: { path: { name: plugin.name } },
                    body: { enabled: true },
                  })
                )
              }
            >
              {t("plugins.enableLabel")}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={pending}
            aria-label={t("plugins.reload", { name: plugin.name })}
            onClick={() =>
              void run(() =>
                reload.mutateAsync({ params: { path: { name: plugin.name } } })
              )
            }
          >
            {t("plugins.reloadLabel")}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function PluginsSkeleton() {
  return (
    <div
      className="flex flex-col gap-4"
      aria-busy="true"
      data-testid="plugins-skeleton"
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
