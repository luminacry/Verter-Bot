import type { TFunction } from "i18next"

/** Formats a duration as its two largest units, e.g. "2 天 3 小时" or "3h 12m". */
export function formatDuration(totalSeconds: number, t: TFunction): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3_600)
  const minutes = Math.floor((seconds % 3_600) / 60)
  const parts: string[] = []
  if (days > 0) {
    parts.push(t("duration.days", { count: days }))
  }
  if (hours > 0) {
    parts.push(t("duration.hours", { count: hours }))
  }
  if (minutes > 0) {
    parts.push(t("duration.minutes", { count: minutes }))
  }
  if (parts.length === 0) {
    return t("duration.lessThanMinute")
  }
  return parts.slice(0, 2).join(" ")
}

export function formatDateTime(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(iso))
}
