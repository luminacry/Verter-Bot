import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useMetricsStream } from "./use-metrics-stream"

class FakeEventSource {
  static latest: FakeEventSource | null = null
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: (() => void) | null = null
  closed = false

  constructor() {
    FakeEventSource.latest = this
  }

  close() {
    this.closed = true
  }
}

const sample = {
  received: 4,
  intercepted: 0,
  dropped: 1,
  processed: 3,
  failed: 0,
  timed_out: 0,
  pending: 2,
  in_flight: 0,
  latency: { buckets_us: [50], counts: [3, 0], total: 3, sum_us: 90 },
}

describe("useMetricsStream", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    FakeEventSource.latest = null
  })

  it("follows open, message, and error", () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    const { result } = renderHook(() => useMetricsStream(undefined))
    const source = FakeEventSource.latest
    expect(source).not.toBeNull()
    expect(result.current.status).toBe("connecting")

    act(() => {
      source?.onopen?.()
    })
    expect(result.current.status).toBe("live")

    act(() => {
      source?.onmessage?.(
        new MessageEvent("message", { data: JSON.stringify(sample) })
      )
    })
    expect(result.current.metrics?.received).toBe(4)
    expect(result.current.metrics?.dropped).toBe(1)

    act(() => {
      source?.onerror?.()
    })
    expect(result.current.status).toBe("reconnecting")
    expect(result.current.metrics?.received).toBe(4)
  })

  it("keeps the initial snapshot until the first message", () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    const { result } = renderHook(() => useMetricsStream(sample))
    expect(result.current.metrics?.pending).toBe(2)
  })
})
