import { useEffect, useState } from "react"

import type { components } from "@/lib/api/schema"

export type RuntimeMetrics = components["schemas"]["RuntimeMetrics"]
export type StreamStatus = "connecting" | "live" | "reconnecting"

/** Live runtime metrics. The browser reconnects the stream after a drop. */
export function useMetricsStream(initial: RuntimeMetrics | undefined): {
  metrics: RuntimeMetrics | undefined
  status: StreamStatus
} {
  const [liveMetrics, setLiveMetrics] = useState<RuntimeMetrics>()
  const [status, setStatus] = useState<StreamStatus>("connecting")

  useEffect(() => {
    const source = new EventSource("/api/metrics/stream")
    let sawMessage = false
    source.onopen = () => {
      setStatus("live")
    }
    source.onmessage = (event: MessageEvent<string>) => {
      sawMessage = true
      setStatus("live")
      try {
        setLiveMetrics(JSON.parse(event.data) as RuntimeMetrics)
      } catch {
        setStatus("reconnecting")
      }
    }
    source.onerror = () => {
      setStatus(sawMessage ? "reconnecting" : "connecting")
    }
    return () => {
      source.close()
    }
  }, [])

  return { metrics: liveMetrics ?? initial, status }
}
